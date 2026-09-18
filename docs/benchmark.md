# Mirror benchmark and full-run results

Measurements behind the mirror list and the scheduler in [`mirrors.py`](../mirrors.py).
Everything here was measured on one fast home connection (Canada, ~500 Mbit down), so treat the
numbers as relative. Dates: 17 September 2026.

## Full 1,000-map runs (production build)

The same list both times: `Emilico`'s top 1,000 most played, 1,000 unique sets, skip-videos on,
10 downloads in flight, the four default mirrors.

| | Run 1 | Run 2 (after fixes) |
|---|---|---|
| Maps downloaded | 1000 / 1000 | 1000 / 1000 |
| Wall clock | 2.5 min | 2.5 min |
| Data | 7.27 GB | 7.30 GB |
| Average throughput | 48 MB/s | 49 MB/s |
| Peak reported | 51.5 MB/s | 53.6 MB/s |
| Maps no mirror had | 0 | 0 |
| Files that failed validation | 0 | 0 |
| Duplicate files | 1 | 0 |
| Files named `<id>.osz` only | 25 | 0 |

Median map size was 6.5 MB without video, so **budget about 7 GB per 1,000 maps**.

For comparison, downloading the same list from osu.ppy.sh takes about 5 hours, because osu! allows
roughly 200 downloads an hour per account.

### Work split (run 2)

| Mirror | Maps | Data | Failures | Notes |
|---|---|---|---|---|
| osudl.org | 721 | 5.21 GB | 0 | Ranked/loved only, and most of the list is ranked, so it did the bulk |
| catboy.best | 162 | 1.18 GB | 4 | Hit its published quota early, paced itself, came back |
| osu.direct | 92 | 0.77 GB | 3 | Occasional 502s from the mirror |
| mirror.nekoha.moe | 26 | 0.17 GB | 0 | Slowest per stream (~0.5 MB/s here), so it got the least work |

Every failure was recovered on another mirror; nothing was lost. Concurrency tuned itself between
1 and 5 per mirror during the run, and catboy's quota pacing produced waits of 10 to 50 seconds
that cost nothing because other mirrors kept going.

## Mirror comparison

Coverage on a 34-map sample (10 from a real most-played list, plus 6 each ranked, loved,
graveyard, pending):

| Mirror | Total | Graveyard | Pending | Coverage |
|---|---|---|---|---|
| mirror.nekoha.moe | 34/34 | 6/6 | 6/6 | full archive, claims ~1.3M sets |
| osu.direct | 34/34 | 6/6 | 6/6 | full archive |
| nerinyan.moe | 34/34 | 6/6 | 6/6 | full archive |
| catboy.best | 33/34 | 5/6 | 6/6 | near-complete |
| sayobot | 29/34 | 3/6 | 4/6 | partial |
| osudl.org | 22/34 | 0/6 | 0/6 | ranked, approved and loved only (~61k sets) |
| beatconnect.io | n/a | | | refuses scripted downloads |

Coverage is not a strict hierarchy: catboy had maps nekoha lacked and vice versa, which is why the
app tries a second mirror on a 404 instead of giving up.

Throughput by parallel downloads (aggregate MB/s, measured one mirror at a time, warm cache):

| Mirror | 1 | 2 | 4 | 6 | Cap used |
|---|---|---|---|---|---|
| catboy.best | 31 | 33 | 66 | 64 | 4 |
| osudl.org | 15 | 28 | 46 | 50 | 5 |
| osu.direct | 8 | 9 | 24 | 17 | 3 |
| mirror.nekoha.moe | 4.4 | 3.5 | 3.3 | 6.4 | 3 |
| sayobot | 1.0 | 0.5 | 1.8 | 4.3 | 1 |

Rate-limit behaviour:

- **catboy.best** publishes `X-Ratelimit-*`: 1,200 units per window, about 20 per download, so
  roughly 60 downloads per window. The app eases off below 40 units left.
- **osu.direct** publishes `RateLimit-*`: about 120 requests a minute.
- **nerinyan.moe** refused half of a 20-request burst with `Retry-After: 10`.
- **beatconnect.io** answers `429` with "Please use beatconnect.io to download beatmaps".
- **nekoha** and **sayobot** advertise no limits; nekoha's site says "no ratelimit".

## Bugs this testing found

1. **Ranked status was overwritten** by the download state, so the ranked-only mirror was never
   used. (Fixed: the API field is now `map_status`.)
2. **catboy rejected our User-Agent** because it contained a URL; every request returned 403.
   (Fixed: the agent is plain `osu-beatmap-downloader/1.0`.)
3. **403 was treated as a hard failure.** It usually means too many connections, so it now backs
   off like a 429. sayobot uses 403 for "not here", which is handled per mirror.
4. **Downloads were buffered in memory**, which would have meant about 1 GB of RAM with videos
   enabled. (Fixed: streamed to a `.part` file.)
5. **Maps that failed with server errors** (a 502) were written off instead of being offered to the
   optional osu! step. (Fixed.)
6. **One map was saved twice** when two mirrors raced and named the file differently.
   (Fixed: the race check now matches on the beatmapset id.)
7. **nekoha sends `Content-Disposition: filename="<id>.osz"`**, so 25 files were named with just
   the id. (Fixed: our own `<id> Artist - Title.osz` wins whenever we have the metadata.)

## Safeguards added after these runs

The runs suggested three improvements; all three are in the build now.

1. **Disk-space check before a run.** The download folder's drive is measured against 7 MB per
   queued map (20 MB with video). A tight fit logs a warning naming both numbers; less than
   `max(2 MB per map, 200 MB)` free stops the run before it starts, instead of failing map by map
   when the drive fills.
2. **"Bad file" responses are quoted.** When a file fails validation, the log now says what was
   wrong and, unless the file was simply the wrong beatmap, the first ~140 characters of it
   (sanitised, one line, once per mirror per run): usually a rate-limit or error page. The four
   bad responses in run 2 would have been identifiable instead of anonymous.
3. **The beatmapset id inside each `.osz` is verified.** Every `.osu` carries `BeatmapSetID:`, so
   the first few difficulties are read from the archive and compared with the map we asked for.
   A mismatch is treated like any other bad file: deleted, short backoff for that mirror, map
   re-dispatched elsewhere. Charts with a missing or negative id (older uploads use `-1`) are
   accepted, since only a positive mismatch is evidence.

Verified against a fake mirror that serves a genuine archive for the wrong beatmapset: the file was
rejected with `contains beatmapset 999999, not 1007`, and all 40 maps still completed from other
mirrors. The other cases covered are a missing `.osu`, an HTML error page, a truncated file, and
both disk thresholds.

## Ideas worth considering

- **Sanity-check the queue against the Songs folder** after import, so a partially imported batch
  is easy to spot.
- **Remember per-mirror speeds between runs**, so the first minute of a run doesn't have to
  rediscover which mirror is fast from your location.

## Reproducing

The scripts used live in the repository history of this document's pull request, and were run
against the built exe with a monitor that polls `/api/state` every 10 seconds. The mirror probes
used `Range: bytes=0-3` requests so no full files were transferred during coverage checks.
