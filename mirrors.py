"""Beatmap mirrors: the registry, the scheduler and the downloader.

The scheduler keeps every enabled mirror busy at once instead of using one at a time:

* Maps are split into two classes. Ranked/approved/loved maps can come from any mirror;
  everything else (graveyard, pending, unknown) needs a full-archive mirror.
* When a mirror has a free slot it takes whichever class is furthest behind, which is the
  water-filling rule for finishing both classes at the same time (the shortest overall run).
* Mirrors are chosen by how fast they have actually been for you, times their success rate,
  minus how close they are to their published quota.
* Each mirror's parallel downloads tune themselves up while it is happy and halve when it
  complains (additive increase, multiplicative decrease).
* A mirror that refuses (429) marks itself busy until the moment it names; the map moves to
  another mirror immediately. Nothing waits unless every mirror is busy at once.
* Stalled downloads are abandoned and restarted elsewhere, and near the end a slow map can be
  raced on a second mirror so one bad connection can't hold up the finish.
"""
import io
import json
import os
import random
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

from osu_api import UA, WIDELY_MIRRORED, osz_name, find_osz

# ---------------------------------------------------------------- the mirrors
#
# "coverage": "all" keeps graveyard/pending maps too; "ranked" mirrors only ranked-ish ones.
# "cap" is the most parallel downloads we will ever ask of that mirror (measured, see README).

MIRRORS = [
    {
        "key": "catboy", "name": "catboy.best", "by": "Mino",
        "home": "https://catboy.best", "coverage": "all", "cap": 4, "default": True,
        "url": "https://catboy.best/d/{id}{novideo}", "novideo": "n", "video": "",
        "about": "Fast, near-complete mirror (also known as Mino). Publishes its remaining quota, "
                 "so the app can pace itself exactly.",
    },
    {
        "key": "osudl", "name": "osudl.org", "by": "kaysting",
        "home": "https://osudl.org", "coverage": "ranked", "cap": 5, "default": True,
        "url": "https://osudl.org/s/{id}{novideo}", "novideo": "?video=false", "video": "",
        "about": "Very fast, but stores ranked, approved and loved maps only (about 61k sets). "
                 "Takes ranked maps off the other mirrors.",
    },
    {
        "key": "nekoha", "name": "mirror.nekoha.moe", "by": "Nekoha",
        "home": "https://mirror.nekoha.moe", "coverage": "all", "cap": 3, "default": True,
        "url": "https://mirror.nekoha.moe/api/download/{id}{novideo}", "novideo": "?noVideo=1", "video": "",
        "about": "Newer archive holding about 1.3 million sets, including 1.2 million graveyard maps. "
                 "Slower per download, so it runs several at once.",
    },
    {
        "key": "osudirect", "name": "osu.direct", "by": "osu.direct",
        "home": "https://osu.direct", "coverage": "all", "cap": 3, "default": True,
        "url": "https://osu.direct/api/d/{id}{novideo}", "novideo": "?noVideo=1", "video": "",
        "about": "Complete and dependable, including graveyard maps. Allows about 120 requests a minute.",
    },
    {
        "key": "sayobot", "name": "sayobot.cn", "by": "SayoBot",
        "home": "https://osu.sayobot.cn", "coverage": "all", "cap": 1, "default": False,
        "url": "https://dl.sayobot.cn/beatmaps/download/{novideo}/{id}", "novideo": "novideo", "video": "full",
        "missing_codes": (403, 404, 410),   # sayobot answers 403 for maps it doesn't have
        "about": "China-hosted mirror. Complete-ish but slow from most places, so it is off by default "
                 "and used last.",
    },
]
BY_KEY = {m["key"]: m for m in MIRRORS}
DEFAULT_ENABLED = {m["key"]: bool(m.get("default")) for m in MIRRORS}

STALL_BYTES_PER_S = 50 * 1024     # a stream slower than this for STALL_SECONDS is abandoned
STALL_SECONDS = 20
MIN_OSZ_BYTES = 1024
HEDGE_AFTER = 25                  # near the end, race a slow map on a second mirror after this long


def seconds_until(value, default=30.0):
    """Retry-After and reset headers come either as seconds or as an HTTP date."""
    if not value:
        return default
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        return max(parsedate_to_datetime(value).timestamp() - time.time(), 0.0)
    except (TypeError, ValueError, IndexError):
        return default


def is_beatmap(data):
    """Did we actually receive a beatmap? (an .osz is a zip holding at least one .osu)"""
    if len(data) < MIN_OSZ_BYTES or data[:2] != b"PK":
        return False
    return _zip_has_osu(io.BytesIO(data))


def check_osz(path, sid=None):
    """Check a downloaded file on disk. Returns (ok, what's wrong).

    A real .osz is a zip with at least one .osu inside, and each .osu names the beatmapset it
    belongs to, so a mirror serving the wrong map is caught here too. Both checks are free:
    the file is already on disk and nothing else is fetched.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False, "the file went missing"
    if size < MIN_OSZ_BYTES:
        return False, f"only {size} bytes"
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"PK":
                return False, "not a beatmap archive"
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            charts = [n for n in names if n.lower().endswith(".osu")]
            if not charts:
                return False, "archive contains no .osu difficulties"
            if sid:
                inside = _set_id_inside(z, charts)
                if inside and str(inside) != str(sid):
                    return False, f"contains beatmapset {inside}, not {sid}"
    except (zipfile.BadZipFile, OSError):
        return False, "damaged archive"
    return True, ""


def _set_id_inside(z, charts):
    """The beatmapset id an .osz claims, or None when the difficulties don't say."""
    for name in charts[:3]:
        try:
            text = z.read(name)[:8000].decode("utf-8", "replace")
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
        m = re.search(r"^BeatmapSetID:\s*(-?\d+)", text, re.M)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    return None


def peek(path, limit=140):
    """First readable characters of a file, for the log when a mirror sends something odd."""
    try:
        with open(path, "rb") as f:
            raw = f.read(400)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"<[^>]{1,80}>", " ", text)          # strip html tags
    text = re.sub(r"[^\x20-\x7e]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _zip_has_osu(source):
    try:
        with zipfile.ZipFile(source) as z:
            return any(n.lower().endswith(".osu") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


class MirrorState:
    """What we know about one mirror right now."""

    def __init__(self, spec, enabled):
        self.spec = spec
        self.key = spec["key"]
        self.enabled = bool(enabled)
        self.max_cap = spec["cap"]
        self.cap = min(2, self.max_cap)     # concurrency, tuned at runtime
        self.inflight = 0
        self.busy_until = 0.0               # rate limited or backing off until this time
        self.speed = None                   # MB/s, rolling average
        self.ok = 0
        self.failed = 0
        self.streak = 0
        self.bytes = 0
        self.quota_note = ""
        self.last_error = ""

    # -- availability
    def available(self, now=None):
        now = now or time.time()
        return self.enabled and self.inflight < self.cap and now >= self.busy_until

    def serves(self, item_class):
        return item_class == "ranked" or self.spec["coverage"] == "all"

    def score(self):
        """Higher is better: fast, reliable, not close to its quota."""
        speed = self.speed if self.speed is not None else {4: 8.0, 5: 8.0, 3: 4.0, 1: 1.0}.get(self.max_cap, 4.0)
        tries = self.ok + self.failed
        success = (self.ok + 2) / (tries + 2)          # optimistic until proven otherwise
        free = 1 + (self.cap - self.inflight)          # prefer mirrors with room to spare
        return speed * success * free * random.uniform(0.9, 1.1)

    # -- outcomes
    def note_success(self, mbytes, seconds):
        self.ok += 1
        self.streak += 1
        self.bytes += mbytes
        if seconds > 0.05:
            measured = mbytes / 1e6 / seconds
            self.speed = measured if self.speed is None else self.speed * 0.7 + measured * 0.3
        if self.streak >= 5 and self.cap < self.max_cap:
            self.cap += 1                               # additive increase
            self.streak = 0

    def note_failure(self, reason="", backoff=0.0):
        self.failed += 1
        self.streak = 0
        self.last_error = reason
        if backoff:
            self.busy_until = max(self.busy_until, time.time() + backoff)
        if reason in ("rate limited", "error"):
            self.cap = max(1, self.cap // 2)            # multiplicative decrease

    def note_headers(self, headers):
        """Ease off before hitting a published quota instead of waiting to be refused."""
        remaining = headers.get("X-Ratelimit-Remaining") or headers.get("RateLimit-Remaining")
        reset = headers.get("X-Ratelimit-Reset") or headers.get("RateLimit-Reset")
        if remaining is None:
            return
        try:
            left = int(remaining)
        except ValueError:
            return
        limit = headers.get("X-Ratelimit-Limit") or headers.get("RateLimit-Limit") or ""
        self.quota_note = f"{left} left{' of ' + limit if limit else ''}"
        # catboy counts a download as ~20 units, so keep a healthy margin
        if left <= 40:
            self.busy_until = max(self.busy_until, time.time() + min(seconds_until(reset, 20), 300))

    def snapshot(self):
        spec = self.spec
        now = time.time()
        return {
            "key": self.key, "name": spec["name"], "by": spec.get("by", ""), "home": spec.get("home", ""),
            "about": spec.get("about", ""), "coverage": spec["coverage"],
            "enabled": self.enabled, "inflight": self.inflight, "cap": self.cap, "max_cap": self.max_cap,
            "speed": round(self.speed, 1) if self.speed else None,
            "done": self.ok, "failed": self.failed, "mb": round(self.bytes / 1e6, 1),
            "waiting": max(0, round(self.busy_until - now)) if self.busy_until > now else 0,
            "quota_note": self.quota_note, "last_error": self.last_error,
        }


def item_class(item):
    """'ranked' maps can come from any mirror; everything else needs a full archive.

    Unknown (a pasted list) counts as "other", so we never send it to a ranked-only mirror.
    """
    return "ranked" if (item.get("map_status") or "").lower() in WIDELY_MIRRORED else "other"


class MirrorDownloader:
    """Downloads a queue of beatmaps from several mirrors at once."""

    def __init__(self, items, folder, opts, emit, log, enabled=None):
        self.items = items
        self.folder = Path(folder)
        self.opts = opts
        self.emit = emit
        self.log = log
        self.lock = threading.Condition()
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        enabled = enabled or DEFAULT_ENABLED
        self.mirrors = [MirrorState(m, enabled.get(m["key"], False)) for m in MIRRORS]
        self.by_key = {m.key: m for m in self.mirrors}
        self.workers = []
        self.started_at = 0.0
        self.finished_at = 0.0
        self.claimed = set()         # ids currently being downloaded (also guards hedging)
        self.reported_bad = set()    # mirrors we've already grumbled about in the log
        self.done_bytes = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        for it in self.items:
            it.setdefault("tried", [])
            it.setdefault("source", "")

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.pause_flag.clear()
        with self.lock:
            self.lock.notify_all()

    def set_mirror(self, key, enabled):
        m = self.by_key.get(key)
        if m:
            m.enabled = enabled
            with self.lock:
                self.lock.notify_all()

    # ------------------------------------------------------------ queue helpers
    def _pending(self):
        return [i for i in self.items if i["status"] == "queued" and i["id"] not in self.claimed]

    def _counts(self):
        ranked = other = 0
        for i in self.items:
            if i["status"] in ("queued", "downloading"):
                if item_class(i) == "ranked":
                    ranked += 1
                else:
                    other += 1
        return ranked, other

    def _eligible(self, item, ready_only=False):
        """Mirrors that may still be asked for this map.

        A ranked-only mirror is normally skipped for graveyard/pending maps, but when the status
        is unknown (a pasted list) it gets a turn once every full-archive mirror has been tried.
        """
        cls = item_class(item)
        out = [m for m in self.mirrors if m.enabled and m.serves(cls) and m.key not in item["tried"]]
        if not item.get("map_status"):
            full_tried = all(m.key in item["tried"] for m in self.mirrors
                             if m.enabled and m.spec["coverage"] == "all")
            if full_tried:
                out += [m for m in self.mirrors if m.enabled and m.spec["coverage"] == "ranked"
                        and m.key not in item["tried"]]
        return [m for m in out if not ready_only or m.available()]

    def _capacity(self):
        """(capacity of full-archive mirrors, capacity of every mirror) in MB/s-ish units."""
        full = all_ = 0.0
        for m in self.mirrors:
            if not m.enabled or m.busy_until > time.time():
                continue
            weight = (m.speed or 4.0) * m.cap
            all_ += weight
            if m.spec["coverage"] == "all":
                full += weight
        return full, all_

    def _choose(self):
        """Pick (mirror, item) for a free worker, or None. Called with the lock held."""
        now = time.time()
        ready = [m for m in self.mirrors if m.available(now)]
        if not ready:
            return None
        pending = self._pending()
        if not pending:
            return None

        ranked_left, other_left = self._counts()
        full_cap, all_cap = self._capacity()
        # water filling: is the "other" class the bottleneck right now?
        other_first = False
        if other_left and full_cap > 0:
            other_first = (other_left / full_cap) >= (ranked_left / all_cap if all_cap else 0)

        for mirror in sorted(ready, key=lambda m: m.score(), reverse=True):
            wanted = ("other", "ranked") if (other_first and mirror.spec["coverage"] == "all") else ("ranked", "other")
            for cls in wanted:
                for item in pending:
                    if item_class(item) != cls or mirror.key in item["tried"]:
                        continue
                    if mirror in self._eligible(item):
                        return mirror, item
        # last resort: an unknown-status map that every full archive has refused
        for mirror in sorted(ready, key=lambda m: m.score(), reverse=True):
            if mirror.spec["coverage"] != "ranked":
                continue
            for item in pending:
                if not item.get("map_status") and mirror in self._eligible(item):
                    return mirror, item
        return None

    def _choose_hedge(self):
        """Near the end, race a slow download on a second mirror so one bad stream can't hold up the run."""
        now = time.time()
        slow = [i for i in self.items
                if i["status"] == "downloading" and now - i.get("started_at", now) > HEDGE_AFTER
                and not i.get("hedged")]
        if not slow:
            return None
        for mirror in sorted((m for m in self.mirrors if m.available(now)), key=lambda m: m.score(), reverse=True):
            for item in slow:
                if mirror.serves(item_class(item)) and mirror.key not in item["tried"]:
                    item["hedged"] = True
                    return mirror, item
        return None

    def _next_free_time(self):
        times = [m.busy_until for m in self.mirrors if m.enabled and m.busy_until > time.time()]
        return min(times) - time.time() if times else 1.0

    # ------------------------------------------------------------ the run
    def _run(self):
        self.started_at = time.time()
        self.folder.mkdir(parents=True, exist_ok=True)
        for leftover in self.folder.glob("*.part"):      # from an interrupted run
            leftover.unlink(missing_ok=True)
        for leftover in self.folder.glob(".*.part"):
            leftover.unlink(missing_ok=True)
        if not any(m.enabled for m in self.mirrors):
            self.log("error", "No mirrors are enabled. Turn at least one on under Mirrors.")
            return
        names = ", ".join(m.spec["name"] for m in self.mirrors if m.enabled)
        self.log("info", f"Downloading from {names}.")
        self._check_space()

        count = max(1, min(int(self.opts.get("workers", 10)), 16))
        self.workers = [threading.Thread(target=self._worker, daemon=True) for _ in range(count)]
        for w in self.workers:
            w.start()
        for w in self.workers:
            w.join()

        self.finished_at = time.time()
        if self.stop_flag.is_set():
            for it in self.items:
                if it["status"] in ("queued", "downloading"):
                    self._set(it, "cancelled")
            self.log("info", "Stopped.")
            return
        for it in self.items:
            if it["status"] in ("queued", "downloading"):
                self._set(it, "not_on_mirrors")
        done = sum(1 for i in self.items if i["status"] == "done")
        missing = sum(1 for i in self.items if i["status"] in ("not_on_mirrors", "failed"))
        secs = max(self.finished_at - self.started_at, 0.1)
        if done:
            self.log("ok", f"Downloaded {done} map{'' if done == 1 else 's'} in {secs / 60:.1f} min "
                           f"({self.done_bytes / 1e6 / secs:.1f} MB/s average).")
        else:
            self.log("warn", "No maps were downloaded.")
        if missing:
            self.log("warn", f"{missing} map{' was' if missing == 1 else 's were'} not available on the enabled "
                             f"mirrors. Use “Check on osu!” below to see which can still be downloaded.")

    def _check_space(self):
        """Warn when the drive probably can't hold the queue (about 7 MB a map without video)."""
        pending = sum(1 for i in self.items if i["status"] == "queued")
        per_map = 7 if self.opts.get("no_video", True) else 20
        needed = pending * per_map
        try:
            free = shutil.disk_usage(self.folder).free / 1e6
        except OSError:
            return
        if free < needed:
            self.log("warn", f"{pending} maps need roughly {needed / 1000:.1f} GB, but the drive has "
                             f"{free / 1000:.1f} GB free. The run will stop early if it fills up.")
        if free < max(pending * 2, 200):
            self.log("error", "Not enough free disk space to download safely. Free some space, "
                              "or pick another download folder, then start again.")
            self.stop_flag.set()

    def _worker(self):
        while not self.stop_flag.is_set():
            while self.pause_flag.is_set() and not self.stop_flag.is_set():
                time.sleep(0.2)
            with self.lock:
                choice = self._choose() or self._choose_hedge()
                if choice is None:
                    if not self._pending() and not self.claimed:
                        return                      # nothing left to do
                    if not any(m.enabled for m in self.mirrors):
                        return
                    self.lock.wait(min(max(self._next_free_time(), 0.2), 5))
                    continue
                mirror, item = choice
                mirror.inflight += 1
                self.claimed.add(item["id"])
                item["tried"].append(mirror.key)
                self._set(item, "downloading", source=mirror.spec["name"], started_at=time.time())
            try:
                self._download(mirror, item)
            finally:
                with self.lock:
                    mirror.inflight -= 1
                    self.claimed.discard(item["id"])
                    self.lock.notify_all()

    # ------------------------------------------------------------ one download
    def url_for(self, mirror, sid):
        spec = mirror.spec
        part = spec["novideo"] if self.opts.get("no_video", True) else spec["video"]
        return spec["url"].format(id=sid, novideo=part)

    def _download(self, mirror, item):
        sid = item["id"]
        existing = find_osz(self.folder, sid)
        if existing:
            self._finish(item, existing, mirror, 0, 0)
            return
        url = self.url_for(mirror, sid)
        started = time.time()
        tmp = self.folder / f".{sid}.{mirror.key}.part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                mirror.note_headers(r.headers)
                name = self._filename(r, item)
                size = self._stream(r, tmp)
        except urllib.error.HTTPError as e:
            tmp.unlink(missing_ok=True)
            mirror.note_headers(e.headers)
            if e.code in mirror.spec.get("missing_codes", (404, 410)):
                self._not_here(item, mirror)
            elif e.code in (429, 403):
                # 429 is a stated limit; 403 usually means "too many connections at once"
                wait = seconds_until(e.headers.get("Retry-After"), 30 if e.code == 429 else 15)
                mirror.note_failure("rate limited", backoff=wait)
                self._requeue(item, mirror, keep_tried=False)
            else:
                mirror.note_failure(f"http {e.code}", backoff=5)
                self._requeue(item, mirror)
            return
        except (urllib.error.URLError, OSError, TimeoutError):
            tmp.unlink(missing_ok=True)
            mirror.note_failure("error" if not self.stop_flag.is_set() else "", backoff=5)
            self._requeue(item, mirror)
            return

        if size is None:                    # stalled or stopped
            tmp.unlink(missing_ok=True)
            mirror.note_failure("stalled", backoff=2)
            self._requeue(item, mirror)
            return
        ok, problem = check_osz(tmp, sid)
        if not ok:
            # an error page, a rate-limit message, a truncated file, or the wrong map
            snippet = peek(tmp) if "beatmapset" not in problem else ""
            tmp.unlink(missing_ok=True)
            if mirror.key not in self.reported_bad:
                self.reported_bad.add(mirror.key)
                self.log("warn", f"{mirror.spec['name']} sent something that isn't beatmap {sid}: "
                                 f"{problem}{' · ' + snippet if snippet else ''}. Trying another mirror.")
            mirror.note_failure(f"bad file ({problem})", backoff=2)
            self._requeue(item, mirror)
            return

        path = self.folder / name
        try:
            # find_osz matches on the id, so a race won by another mirror is caught
            # even when the two mirrors name the file differently
            if path.exists() or find_osz(self.folder, sid):
                tmp.unlink(missing_ok=True)
            else:
                tmp.replace(path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            self.log("error", f"Couldn't save {name}: {e}")
            mirror.note_failure("disk")
            self._requeue(item, mirror)
            return
        secs = time.time() - started
        mirror.note_success(size, secs)
        self.done_bytes += size
        self._finish(item, str(path), mirror, size, secs)

    def _stream(self, response, path):
        """Write the body to disk, giving up if the stream stalls. Returns bytes written."""
        total = 0
        window_start, window_bytes = time.time(), 0
        with open(path, "wb") as f:
            while True:
                if self.stop_flag.is_set():
                    return None
                chunk = response.read(1 << 16)
                if not chunk:
                    return total
                f.write(chunk)
                total += len(chunk)
                window_bytes += len(chunk)
                elapsed = time.time() - window_start
                if elapsed >= STALL_SECONDS:
                    if window_bytes / elapsed < STALL_BYTES_PER_S:
                        return None
                    window_start, window_bytes = time.time(), 0

    def _filename(self, response, item):
        """'<id> Artist - Title.osz', from our own metadata whenever we have it."""
        if item.get("title"):
            return osz_name(item["id"], item.get("artist", ""), item.get("title", ""))
        cd = response.headers.get("Content-Disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if m:
            name = urllib.request.unquote(m.group(1)).strip()
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
            # some mirrors send just "<id>.osz", which is no better than what we compose
            if name.lower().endswith(".osz") and name[:1].isdigit() and " " in name:
                return name
        return osz_name(item["id"], item.get("artist", ""), item.get("title", ""))

    # ------------------------------------------------------------ item outcomes
    def _set(self, item, status, **extra):
        item["status"] = status
        item.update(extra)
        self.emit(item)

    def _finish(self, item, path, mirror, size, secs):
        self._set(item, "done", file=path, source=mirror.spec["name"], error="",
                  mb=round(size / 1e6, 1) if size else item.get("mb", 0))
        if self.opts.get("auto_open"):
            try:
                import osu_local
                osu_local.import_into_osu(path, self.opts.get("import_client", "stable"),
                                          self.opts.get("songs_dir", ""), self.opts.get("osu_paths"))
            except Exception as e:
                self.log("warn", f"Couldn't hand {Path(path).name} to osu!: {e}")

    def _not_here(self, item, mirror):
        """This mirror doesn't have the map; try the next one."""
        with self.lock:
            if item["status"] == "done":
                return
            if self._eligible(item):
                self._set(item, "queued")
            else:
                self._set(item, "not_on_mirrors", error="Not on any enabled mirror")
            self.lock.notify_all()

    def _requeue(self, item, mirror, keep_tried=True):
        """Put the map back for another mirror (a refusal shouldn't count as 'tried')."""
        with self.lock:
            if item["status"] == "done":
                return  # a parallel attempt already finished this map
            if not keep_tried and mirror.key in item["tried"]:
                item["tried"].remove(mirror.key)
            if self._eligible(item):
                self._set(item, "queued")
            elif item.get("attempts", 0) < 1:
                item["attempts"] = item.get("attempts", 0) + 1   # one full second pass
                item["tried"] = []
                self._set(item, "queued")
            else:
                self._set(item, "failed", error=f"Every mirror failed ({mirror.last_error or 'error'})")
            self.lock.notify_all()

    # ------------------------------------------------------------ status for the UI
    def status(self, remaining):
        speed = 0.0
        elapsed = max(time.time() - self.started_at, 0.1) if self.started_at else 0
        if elapsed:
            speed = self.done_bytes / 1e6 / elapsed
        done = sum(1 for i in self.items if i["status"] == "done")
        avg_mb = (self.done_bytes / 1e6 / done) if done else 4.0
        eta = remaining * avg_mb / speed if speed > 0.05 and remaining else 0
        return {
            "eta": round(eta),
            "speed": round(speed, 1),
            "mirrors": [m.snapshot() for m in self.mirrors],
            "mb": round(self.done_bytes / 1e6, 1),
            "limited": False,
        }
