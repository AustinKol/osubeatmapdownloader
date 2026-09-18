# The mirror load balancer

How the app spreads a download queue over several community mirrors at once, and why it never
stops to wait. Everything described here lives in [`mirrors.py`](../mirrors.py); the numbers come
from [the benchmark](benchmark.md).

<picture>
  <source media="(prefers-color-scheme: light)" srcset="images/scheduler-light.svg">
  <img src="images/scheduler-dark.svg" width="900" alt="Architecture of the mirror load balancer">
</picture>

- [The problem](#the-problem)
- [Model](#model)
- [1. Two classes of map](#1-two-classes-of-map)
- [2. Water filling between the classes](#2-water-filling-between-the-classes)
- [3. Choosing a mirror on merit](#3-choosing-a-mirror-on-merit)
- [4. Self-tuning concurrency (AIMD)](#4-self-tuning-concurrency-aimd)
- [5. Refusals, quotas and instant failover](#5-refusals-quotas-and-instant-failover)
- [6. Stalls and tail racing](#6-stalls-and-tail-racing)
- [7. Verifying every file](#7-verifying-every-file)
- [Constants](#constants)

## The problem

Mirrors are run by volunteers and no two behave alike:

| | catboy.best | osudl.org | mirror.nekoha.moe | osu.direct |
|---|---|---|---|---|
| Coverage | full archive | ranked/approved/loved only | full archive | full archive |
| One stream | ~31 MB/s | ~15 MB/s | ~4 MB/s | ~8 MB/s |
| Limit | ~60 downloads per window, published in headers | none seen | none | ~120 requests/min |

Coverage overlaps but isn't nested (each full archive has maps another lacks), speeds depend on
where you are, and limits shift during a run. A fixed priority list gets all of this wrong somewhere.
A plain "try mirror A, fall back to B" loop is also slow, because it only ever uses one mirror at a
time and sits idle every time A says *slow down*.

The app treats the enabled mirrors as one pool instead. A fixed set of worker threads (10 by
default) share one lock. Each time a worker is free, it asks the scheduler for the best
**(mirror, map)** pair *right now*, downloads it, and reports what happened back into the
mirror's state.

## Model

Each mirror $m$ carries live state ([`MirrorState`](../mirrors.py)):

| Symbol | Field | Meaning |
|---|---|---|
| $s_m$ | `speed` | recent throughput in MB/s (EWMA) |
| $ok_m,\ f_m$ | `ok`, `failed` | completed and failed attempts |
| $c_m$ | `cap` | current parallel-download limit, tuned at runtime |
| $C_m$ | `max_cap` | ceiling for $c_m$ (deliberately modest, per mirror) |
| $i_m$ | `inflight` | downloads running on it now |
| $t_m$ | `busy_until` | parked until this time (rate limit or backoff) |

A mirror is **available** when

$$\text{enabled}_m \;\wedge\; i_m < c_m \;\wedge\; \text{now} \ge t_m$$

Each map carries its `map_status` (from the osu! profile list, or unknown for pasted IDs) and a
`tried` list of mirrors already asked for it.

## 1. Two classes of map

```python
def item_class(item):
    return "ranked" if (item.get("map_status") or "").lower() in WIDELY_MIRRORED else "other"
```

`WIDELY_MIRRORED` is ranked, approved, loved and qualified. Every mirror can serve those.
Graveyard, pending, WIP and unknown maps are **other**, and only full-archive mirrors are asked for
them, so a ranked-only mirror never wastes a request on a map it can't have.

The one exception is maps whose status is unknown (a pasted list): once every full archive has
said 404, the ranked-only mirrors get a turn too, since "unknown" might turn out to be ranked.

## 2. Water filling between the classes

Full-archive mirrors are the scarce resource: they are the only ones that can serve **other**
maps, but they are also useful for ranked ones. If they spend their time on ranked maps, the run
ends with them grinding through a tail of graveyard maps alone while the ranked-only mirrors sit
idle.

Let $R$ and $O$ be the ranked and other maps still queued or downloading, and weight each
non-parked mirror's capacity by what it can actually move:

$$W_m = s_m \cdot c_m \qquad
W_{\text{full}} = \sum_{m\ \in\ \text{full archive}} W_m \qquad
W_{\text{all}} = \sum_{m} W_m$$

$O / W_{\text{full}}$ is roughly how long the other maps would take if the full archives did
nothing else, and $R / W_{\text{all}}$ is how long the ranked maps take using everything. When a
full-archive mirror frees up, it serves the class that is furthest behind:

$$\text{other first} \iff \frac{O}{W_{\text{full}}} \;\ge\; \frac{R}{W_{\text{all}}}$$

This is the water-filling rule: keep both "water levels" falling at the same rate so both classes
run dry together. Ranked-only mirrors always take ranked maps.

```python
ranked_left, other_left = self._counts()
full_cap, all_cap = self._capacity()
other_first = False
if other_left and full_cap > 0:
    other_first = (other_left / full_cap) >= (ranked_left / all_cap if all_cap else 0)
```

## 3. Choosing a mirror on merit

Available mirrors are tried in order of a score:

$$\text{score}_m \;=\; \hat s_m \;\cdot\; \frac{ok_m + 2}{ok_m + f_m + 2} \;\cdot\; \bigl(1 + c_m - i_m\bigr) \;\cdot\; U(0.9,\ 1.1)$$

- **Speed** $\hat s_m$ is an exponentially weighted moving average of measured throughput,
  $\hat s \leftarrow 0.7\,\hat s + 0.3\,s_{\text{new}}$. Before a mirror has finished anything it
  gets a prior from its cap (8, 4 or 1 MB/s).
- **Success rate** uses a Laplace-style prior of two virtual successes, so a new mirror starts
  optimistic and one early failure doesn't sink it.
- **Free slots** $1 + c_m - i_m$ spread load toward mirrors with room to spare.
- **Jitter** of ±10% stops ten workers stampeding the same host when scores are close.

The first map (in queue order) of the wanted class that this mirror may serve and hasn't tried
wins. A mirror having a bad minute loses work automatically, and wins it back once its measured
speed recovers. No ranking is hard-coded: in the benchmark the fastest mirror ended up with 72%
of a 1,000-map list without being told to.

```python
def score(self):
    speed = self.speed if self.speed is not None else {4: 8.0, 5: 8.0, 3: 4.0, 1: 1.0}.get(self.max_cap, 4.0)
    tries = self.ok + self.failed
    success = (self.ok + 2) / (tries + 2)          # optimistic until proven otherwise
    free = 1 + (self.cap - self.inflight)          # prefer mirrors with room to spare
    return speed * success * free * random.uniform(0.9, 1.1)
```

## 4. Self-tuning concurrency (AIMD)

Each mirror's parallel limit adapts with **additive increase, multiplicative decrease**, the same
feedback shape TCP uses to find a link's capacity:

$$\text{after 5 consecutive successes:}\quad c_m \leftarrow \min(c_m + 1,\ C_m)$$

$$\text{on a refusal or connection error:}\quad c_m \leftarrow \max\bigl(1,\ \lfloor c_m / 2 \rfloor\bigr)$$

Every mirror starts at $c_m = 2$. The ceilings $C_m$ are kept low on purpose (catboy 4,
osudl 5, nekoha 3, osu.direct 3, sayobot 1). These are volunteers' servers, and the app should
settle near what each one is comfortable giving rather than as much as it will tolerate. In the
benchmark, $c_m$ moved between 1 and 5 during a single run.

## 5. Refusals, quotas and instant failover

This is the part that makes the pool never pause. Every response maps to one of four outcomes:

| Response | Meaning | What happens |
|---|---|---|
| `429` | explicit rate limit | park the mirror until `Retry-After` (default 30 s), halve its cap, **re-queue the map for another mirror immediately** |
| `403` | usually too many connections | as above, default 15 s |
| `404` / `410` | this mirror doesn't have the map | mark it tried, move the map on; no penalty |
| `5xx`, network error | the mirror is struggling | 5 s backoff (a network error also halves its cap), map moves on |

Parking affects **only that mirror**: $t_m$ moves into the future, `available()` becomes false,
and the other workers keep pulling from the rest of the pool. The refused map goes straight back
into the queue, and because a refusal says nothing about whether the mirror *has* the map, it's
removed from `tried`, so it can come back to that mirror later. When $\text{now} \ge t_m$, the
mirror simply shows up as available again. Nothing sleeps and nothing retries in place.

```python
elif e.code in (429, 403):
    wait = seconds_until(e.headers.get("Retry-After"), 30 if e.code == 429 else 15)
    mirror.note_failure("rate limited", backoff=wait)
    self._requeue(item, mirror, keep_tried=False)
```

Mirrors that **publish** their quota are handled before they have to refuse. catboy sends
`X-Ratelimit-Remaining` and `X-Ratelimit-Reset` (osu.direct uses the `RateLimit-*` spelling). A
download costs about 20 units, so when 40 or fewer remain the mirror parks itself until the reset
(capped at 5 minutes):

```python
if left <= 40:
    self.busy_until = max(self.busy_until, time.time() + min(seconds_until(reset, 20), 300))
```

In practice catboy serves about 60 maps, sits out for up to 50 seconds, and rejoins, while the
rest of the pool keeps going. Workers only ever wait when *every* enabled mirror is parked. Even
then they wait on a condition variable until the earliest $t_m$ (at most 5 s), and a finishing
download wakes them sooner.

A map that has been tried on every eligible mirror gets one full second pass. If that also fails,
it's handed to the optional osu! step, not dropped.

## 6. Stalls and tail racing

**Stall cutoff.** Downloads stream to a `.part` file and are measured in 20-second windows. A
window averaging under 50 KB/s abandons the stream (2 s backoff for that mirror) and re-dispatches
the map:

```python
elapsed = time.time() - window_start
if elapsed >= STALL_SECONDS:
    if window_bytes / elapsed < STALL_BYTES_PER_S:
        return None
    window_start, window_bytes = time.time(), 0
```

**Tail racing (hedging).** A free worker that finds nothing in the queue it can serve looks for
downloads that have been running for more than 25 s. It starts the same map on a second mirror
that hasn't tried it. That mostly happens near the end of a run, when one slow connection would
otherwise decide the finish time. The first complete, valid file wins and the loser is discarded.
The check is on the **beatmapset id**, not the filename, because different mirrors name the same
map differently:

```python
if path.exists() or find_osz(self.folder, sid):   # another mirror already delivered this map
    tmp.unlink(missing_ok=True)
```

## 7. Verifying every file

A download only counts once the file on disk passes three offline checks. Nothing extra is
fetched:

1. It's at least 1 KB and starts with the zip signature `PK`.
2. It opens as a zip holding at least one `.osu` difficulty.
3. The `BeatmapSetID:` line in the first few difficulties matches the map we asked for. Charts
   with no id or `-1` (common in old uploads) are accepted, since only a positive mismatch is
   evidence.

```python
charts = [n for n in names if n.lower().endswith(".osu")]
if not charts:
    return False, "archive contains no .osu difficulties"
if sid:
    inside = _set_id_inside(z, charts)
    if inside and str(inside) != str(sid):
        return False, f"contains beatmapset {inside}, not {sid}"
```

A file that fails is deleted, the mirror takes a 2 s backoff, and the map is re-dispatched. The
activity log gets one line per mirror per run, naming the problem and quoting the first ~140
readable characters of what arrived (almost always a rate-limit or error page).

This deliberately does **not** compare difficulty counts or checksums against osu!: maps get
updated, and a mirror's copy with a different difficulty set, or without copyrighted audio, is
still a beatmap worth having.

Before a run starts, the app also checks the drive for room: about 7 MB per queued map (20 MB with
video). A tight fit logs a warning. Less than $\max(2\,\text{MB} \times n,\ 200\,\text{MB})$ free
stops the run up front, so it doesn't fail map by map once the disk fills.

## Constants

| Name | Value | Purpose |
|---|---|---|
| workers | 10 (1 to 16) | downloads in flight across all mirrors |
| initial cap | 2 | parallel downloads per mirror at start |
| AIMD step | +1 per 5 successes, ÷2 on refusal | concurrency tuning |
| EWMA weight | 0.3 | how fast speed estimates follow reality |
| `STALL_BYTES_PER_S` / `STALL_SECONDS` | 50 KB/s over 20 s | stall cutoff |
| `HEDGE_AFTER` | 25 s | when a slow download may be raced |
| quota margin | 40 units | when catboy-style mirrors park pre-emptively |
| max park | 300 s | cap on waiting for a published reset |
| `MIN_OSZ_BYTES` | 1024 | smallest file that could be a beatmap |
