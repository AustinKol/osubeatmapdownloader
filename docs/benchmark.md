# Benchmark

Measurements behind the mirror list and the scheduler in [`mirrors.py`](../mirrors.py).
Everything here was measured on one fast home connection (Canada, ~500 Mbit down), so treat the
numbers as relative. Date: September 2026.

## A full 1,000-map run

A real player's top 1,000 most played maps (1,000 unique sets), using the release build with its
default settings: skip videos on, 10 downloads in flight, the four default mirrors.

| | |
|---|---|
| Maps downloaded | 1000 / 1000 |
| Wall clock | 2.5 min |
| Data | 7.30 GB |
| Average throughput | 49 MB/s |
| Peak | 53.6 MB/s |
| Maps no mirror had | 0 |
| Invalid files | 0 |

Median map size was 6.5 MB without video, so **budget about 7 GB per 1,000 maps**.

For comparison, downloading the same list from osu.ppy.sh takes about 5 hours, because osu! allows
roughly 200 downloads an hour per account.

### Work split

| Mirror | Maps | Data | Failures | Notes |
|---|---|---|---|---|
| osudl.org | 721 | 5.21 GB | 0 | Ranked/loved only, and most of the list is ranked, so it did the bulk |
| catboy.best | 162 | 1.18 GB | 4 | Reached its published quota, paced itself, came back |
| osu.direct | 92 | 0.77 GB | 3 | Occasional 502s from the mirror |
| mirror.nekoha.moe | 26 | 0.17 GB | 0 | Slowest per stream (~0.5 MB/s here), so it got the least work |

Every failure was recovered on another mirror; nothing was lost. Concurrency tuned itself between
1 and 5 per mirror during the run, and catboy's quota pauses of 10 to 50 seconds cost nothing
because the other mirrors kept going.

## Mirror comparison

Coverage on a 34-map sample (10 from a real most-played list, plus 6 each ranked, loved,
graveyard, pending):

| Mirror | Total | Graveyard | Pending | Coverage |
|---|---|---|---|---|
| mirror.nekoha.moe | 34/34 | 6/6 | 6/6 | full archive, claims ~1.3M sets |
| osu.direct | 34/34 | 6/6 | 6/6 | full archive |
| catboy.best | 33/34 | 5/6 | 6/6 | near-complete |
| sayobot | 29/34 | 3/6 | 4/6 | partial |
| osudl.org | 22/34 | 0/6 | 0/6 | ranked, approved and loved only (~61k sets) |

Coverage is not a strict hierarchy: catboy had maps nekoha lacked and vice versa, which is why the
app tries another mirror on a 404 instead of giving up.

Throughput by parallel downloads (aggregate MB/s, measured one mirror at a time):

| Mirror | 1 | 2 | 4 | 6 | Cap used |
|---|---|---|---|---|---|
| catboy.best | 31 | 33 | 66 | 64 | 4 |
| osudl.org | 15 | 28 | 46 | 50 | 5 |
| osu.direct | 8 | 9 | 24 | 17 | 3 |
| mirror.nekoha.moe | 4.4 | 3.5 | 3.3 | 6.4 | 3 |
| sayobot | 1.0 | 0.5 | 1.8 | 4.3 | 1 |

Rate limits:

- **catboy.best** publishes `X-Ratelimit-*`: 1,200 units per window, about 20 per download, so
  roughly 60 downloads per window. The app eases off below 40 units left.
- **osu.direct** publishes `RateLimit-*`: about 120 requests a minute.
- **nekoha** and **sayobot** advertise no limits; nekoha's site says "no ratelimit".
