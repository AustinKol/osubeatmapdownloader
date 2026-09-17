"""osu! beatmap fetching + headless Chrome downloading.

Everything that talks to osu.ppy.sh lives here; app.py only wires it to the UI.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OSU = "https://osu.ppy.sh"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) osu-beatmap-downloader"


# ---------------------------------------------------------------- helpers

def parse_cookie(raw):
    """Accept a bare cookie value, `osu_session=...`, or a whole Cookie header."""
    raw = (raw or "").strip().strip('"').strip("'")
    m = re.search(r"osu_session=([^;\s]+)", raw)
    return m.group(1) if m else raw


def parse_ids(text):
    """Pull beatmapset IDs out of pasted text: bare IDs, /beatmapsets/ links, /s/ links."""
    ids, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = (re.search(r"beatmapsets/(\d+)", line)
             or re.search(r"/s/(\d+)", line)
             or re.match(r"(\d+)", line))
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
    return ids


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def resolve_user_id(user):
    """Turn an ID, profile URL, or username into a numeric user ID."""
    user = (user or "").strip()
    m = re.search(r"users/(\d+)", user) or re.fullmatch(r"(\d+)", user)
    if m:
        return m.group(1)
    m = re.search(r"users/([^/?#]+)", user)
    name = urllib.parse.unquote(m.group(1)) if m else user
    if not name:
        raise ValueError("Enter a username, profile link or user ID.")
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"{OSU}/users/{urllib.parse.quote(name)}", headers={"User-Agent": UA})
    try:
        opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        m = re.search(r"/users/(\d+)", e.headers.get("Location", ""))
        if e.code in (301, 302) and m:
            return m.group(1)
    raise ValueError(f"Couldn't find an osu! user called “{name}”.")


def fetch_user_maps(user, kind, limit, on_progress=None):
    """Return [{id, title, artist, cover}] from a user's profile list, deduplicated."""
    uid = resolve_user_id(user)
    out, seen, offset = [], set(), 0
    while len(out) < limit:
        page = _get_json(f"{OSU}/users/{uid}/beatmapsets/{kind}?offset={offset}&limit=100")
        if not page:
            break
        for item in page:
            s = item["beatmapset"] if kind == "most_played" else item
            sid = str(s["id"])
            if sid in seen:
                continue
            seen.add(sid)
            out.append({"id": sid, "title": s.get("title", ""), "artist": s.get("artist", ""),
                        "cover": (s.get("covers") or {}).get("list", "")})
            if len(out) >= limit:
                break
        offset += len(page)
        if on_progress:
            on_progress(len(out))
        if len(page) < 100:
            break
    return out


def scan_songs_folder(path):
    """Beatmapset IDs present in an osu!stable Songs folder (folders are named '<id> Artist - Title')."""
    ids = set()
    p = Path(path)
    if not p.is_dir():
        raise ValueError("That Songs folder doesn't exist.")
    for entry in os.scandir(p):
        m = re.match(r"(\d+)\s", entry.name)
        if m:
            ids.add(m.group(1))
    return ids


def find_osz(folder, sid):
    """Finished .osz for this set in the folder, if any (osu names them '<id> Artist - Title.osz')."""
    try:
        for entry in os.scandir(folder):
            if entry.name.endswith(".osz") and re.match(rf"{sid}(\D|$)", entry.name):
                return entry.path
    except FileNotFoundError:
        pass
    return None


# ---------------------------------------------------------------- browser

def make_driver(download_dir=None, headless=True):
    from selenium import webdriver  # imported lazily so the UI starts instantly

    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-first-run")
    opts.add_argument("--log-level=3")
    # stop Chrome's component updater from dropping files into the download folder
    opts.add_argument("--disable-component-update")
    opts.add_argument("--disable-background-networking")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    if download_dir:
        opts.add_experimental_option("prefs", {
            "download.default_directory": str(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        })
    # Selenium Manager fetches a chromedriver that matches the installed Chrome.
    driver = webdriver.Chrome(options=opts)
    if download_dir:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior",
                               {"behavior": "allow", "downloadPath": str(download_dir)})
    return driver


def login(driver, cookie):
    """Inject the session cookie. Returns {id, username, avatar} or raises."""
    driver.get(f"{OSU}/home")
    driver.delete_all_cookies()
    driver.add_cookie({"name": "osu_session", "value": parse_cookie(cookie),
                       "domain": ".ppy.sh", "path": "/", "secure": True, "httpOnly": True})
    driver.get(f"{OSU}/home")
    user = driver.execute_script(
        "const u = window.currentUser || {};"
        "return u.id ? {id: u.id, username: u.username, avatar: u.avatar_url} : null;")
    if not user:
        raise PermissionError("osu! didn't accept that session cookie. It may have expired — "
                              "grab a fresh one and try again.")
    return user


def check_login(cookie):
    driver = make_driver()
    try:
        return login(driver, cookie)
    finally:
        driver.quit()


CLICK_DOWNLOAD_JS = """
const [sid, noVideo] = arguments;
const links = [...document.querySelectorAll('a[href*="/beatmapsets/' + sid + '/download"]')];
if (!links.length) return null;
const pick = links.find(a => noVideo === a.href.includes('noVideo')) || links[0];
pick.click();
return pick.href;
"""


def _duration(seconds):
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


# osu! allows a fixed number of downloads per rolling hour (200 for regular accounts,
# more for osu!supporters). When it refuses, retry on this cycle — it adds up to an hour,
# so the 4th retry lands after the window has rolled over. Then start again at 5 min.
QUOTA_RETRY_WAITS = (5 * 60, 10 * 60, 20 * 60, 25 * 60)
DEFAULT_HOURLY_LIMIT = 200


def _resume_time(refused_at, batch_start):
    """First retry (on the QUOTA_RETRY_WAITS cycle) after the batch's hour has rolled over."""
    free_at = batch_start + 3600
    t, i = refused_at, 0
    while True:
        t += QUOTA_RETRY_WAITS[i % len(QUOTA_RETRY_WAITS)]
        i += 1
        if t >= free_at:
            return t


class Downloader:
    """Runs a download queue on a background thread and reports through callbacks."""

    def __init__(self, items, cookie, folder, opts, emit, log):
        self.items = items          # list of dicts; this class mutates item["status"]
        self.cookie = cookie
        self.folder = Path(folder)
        self.opts = opts
        self.emit = emit            # emit(item) after any status change
        self.log = log              # log(level, message)
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.warned_client = False
        # progress/ETA bookkeeping, read by the UI
        self.batch_done = 0             # downloads since the last time osu! let us resume
        self.batch_started_at = None    # time of the batch's first download
        self.hourly_limit = DEFAULT_HOURLY_LIMIT  # learned from the first refusal
        self.limit_learned = False
        self.quota_hit_at = None        # when osu! started refusing (None = not limited)
        self.retry_at = None            # when the next retry happens while limited
        self.retry_attempt = 0
        self.per_map = None             # average seconds per successful map
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.pause_flag.clear()

    def status(self, remaining):
        """Progress info for the UI, including an ETA that accounts for osu!'s hourly limit."""
        limit = self.hourly_limit
        if not self.limit_learned and self.batch_done > limit:
            limit = None  # past the default without being refused: probably a supporter
        per_map = self.per_map or float(self.opts.get("delay", 3)) + 3
        now = time.time()
        t, left = now, remaining
        if self.quota_hit_at:
            batch_start = self.batch_started_at or self.quota_hit_at - 3600
            t = batch_start = max(now, _resume_time(self.quota_hit_at, batch_start))
            cap = limit or left
        else:
            batch_start = self.batch_started_at or now
            cap = left if limit is None else max(0, limit - self.batch_done)
        while True:
            n = min(left, cap)
            t += n * per_map
            left -= n
            if left <= 0 or not limit:
                break
            resumed = _resume_time(t, batch_start)  # osu! refuses at t; wait for the window
            t = batch_start = resumed
            cap = limit
        return {
            "eta": round(t - now),
            "hourly_limit": limit,
            "limited": bool(self.quota_hit_at),
            "retry_at": self.retry_at,
            "retry_attempt": self.retry_attempt,
            "retry_attempts": len(QUOTA_RETRY_WAITS),
        }

    def _sleep(self, seconds):
        """Interruptible sleep that also honours pause."""
        end = time.time() + seconds
        while time.time() < end or self.pause_flag.is_set():
            if self.stop_flag.is_set():
                return False
            time.sleep(0.25)
        return True

    def _set(self, item, status, **extra):
        item["status"] = status
        item.update(extra)
        self.emit(item)

    def _run(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        driver = None
        try:
            self.log("info", "Starting Chrome in the background…")
            driver = make_driver(self.folder, headless=not self.opts.get("show_browser"))
            user = login(driver, self.cookie)
            self.log("ok", f"Signed in as {user['username']}.")
            self._loop(driver)
        except Exception as e:  # surface anything unexpected to the UI instead of dying silently
            self.log("error", f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
        finally:
            if driver:
                # give in-flight downloads a moment to land before closing Chrome
                deadline = time.time() + 30
                while time.time() < deadline and any(self.folder.glob("*.crdownload")):
                    time.sleep(0.5)
                driver.quit()
            for item in self.items:
                if item["status"] in ("queued", "downloading"):
                    self._set(item, "cancelled" if self.stop_flag.is_set() else "failed")
            self.log("info", "Stopped." if self.stop_flag.is_set() else "All done.")

    def _loop(self, driver):
        delay = float(self.opts.get("delay", 3))
        batch, rest = int(self.opts.get("batch", 60)), float(self.opts.get("rest", 15))
        cooldown = float(self.opts.get("cooldown", 300))
        timeout = float(self.opts.get("timeout", 90))
        since_rest, fails_in_row = 0, 0

        for item in self.items:
            if self.stop_flag.is_set():
                return
            if item["status"] != "queued":
                continue
            if since_rest >= batch:
                self.log("info", f"Taking a {rest:g}s breather to stay under osu!'s rate limit.")
                if not self._sleep(rest):
                    return
                since_rest = 0
            if not self._sleep(0):
                return

            self._set(item, "downloading")
            started = time.time()
            ok, reason = self._download_one(driver, item, timeout)
            since_rest += 1
            attempt = 0
            while not ok and "quota" in reason.lower():
                # osu!'s hourly download limit: keep this map and retry it on a 5/10/20/25 min cycle
                if not self.quota_hit_at:
                    self.quota_hit_at = time.time()
                    # only learn *higher* limits (supporters): a run started partway through
                    # an hour sees fewer than the real allowance before being refused
                    if self.batch_done > self.hourly_limit:
                        self.hourly_limit = self.batch_done
                    self.limit_learned = True
                wait = QUOTA_RETRY_WAITS[attempt % len(QUOTA_RETRY_WAITS)]
                attempt += 1
                self.retry_attempt = (attempt - 1) % len(QUOTA_RETRY_WAITS) + 1
                self.retry_at = time.time() + wait
                self._set(item, "queued", error="")
                self.log("warn", f"osu!'s hourly download limit was reached. Retrying in {_duration(wait)} "
                                 f"(at {time.strftime('%H:%M', time.localtime(self.retry_at))}, "
                                 f"attempt {self.retry_attempt} of {len(QUOTA_RETRY_WAITS)}).")
                if not self._sleep(wait):
                    return
                self.retry_at = None
                self._set(item, "downloading")
                started = time.time()
                ok, reason = self._download_one(driver, item, timeout)
            if ok and self.quota_hit_at:
                self.log("ok", "osu! is accepting downloads again.")
                self.quota_hit_at, self.retry_attempt, self.batch_done = None, 0, 0
                self.batch_started_at = None
            if ok:
                fails_in_row = 0
                self.batch_done += 1
                self.batch_started_at = self.batch_started_at or started
                took = time.time() - started + delay
                self.per_map = took if self.per_map is None else self.per_map * 0.9 + took * 0.1
                self._set(item, "done", file=ok, error="")
                if self.opts.get("auto_open"):
                    self._import(ok)
            else:
                fails_in_row += 1
                self._set(item, "failed", error=reason)
                self.log("warn", f"{item['id']}: {reason}")
                if fails_in_row >= 3:
                    self.log("warn", f"Several failures in a row — osu! may be rate limiting. "
                                     f"Cooling down for {_duration(cooldown)}.")
                    if not self._sleep(cooldown):
                        return
                    fails_in_row = 0
            if not self._sleep(delay):
                return

    def _import(self, path):
        wanted = self.opts.get("import_client", "stable")
        used = import_into_osu(path, wanted, self.opts.get("songs_dir", ""), self.opts.get("osu_paths"))
        if used != wanted and not self.warned_client:
            self.warned_client = True
            other = f"osu!{used}" if used != "default" else "the default app"
            self.log("warn", f"osu!{wanted} isn't installed — importing with {other} instead.")

    def _download_one(self, driver, item, timeout):
        sid = item["id"]
        existing = find_osz(self.folder, sid)
        if existing:
            return existing, ""

        # close stray tabs a download might have opened
        while len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            driver.close()
        driver.switch_to.window(driver.window_handles[0])

        driver.get(f"{OSU}/beatmapsets/{sid}")
        href = None
        for _ in range(20):  # page is rendered client-side; wait for the button
            href = driver.execute_script(CLICK_DOWNLOAD_JS, sid, bool(self.opts.get("no_video")))
            if href or self.stop_flag.is_set():
                break
            time.sleep(0.25)
        if not item.get("title"):
            meta = driver.execute_script(
                "const t = document.title.replace(/[\\u200e\\u200f\\u202a-\\u202e]/g, '');"
                "return t.split(' · ')[0];")
            if meta and "not found" not in meta.lower():
                artist, _, title = meta.partition(" - ")
                item.update(artist=artist, title=title or artist)
                self.emit(item)
        if not href:
            text = driver.execute_script("return document.body.innerText.slice(0, 2000)") or ""
            if "not found" in text.lower() or "doesn't exist" in text.lower():
                return None, "Beatmap not found (deleted or restricted)."
            return None, "No download button (map may be unavailable, or explicit content is hidden in your osu! settings)."

        # wait for the .osz to appear and finish
        start = time.time()
        while time.time() - start < timeout:
            if self.stop_flag.is_set():
                return None, "Cancelled."
            done = find_osz(self.folder, sid)
            if done:
                return done, ""
            if time.time() - start > 1 and not any(self.folder.glob("*.crdownload")):
                # a refused download replaces the page with osu!'s "too many requests" page
                text = (driver.execute_script(
                    "return document.title + ' ' + (document.body ? document.body.innerText.slice(0, 500) : '')") or "").lower()
                if "quota" in text or "too many requests" in text:
                    driver.back()
                    return None, "osu! download quota reached."
            time.sleep(0.5)
        return None, "Timed out waiting for the download."


# ---------------------------------------------------------------- osu! clients

def _child_env():
    """Environment for programs we launch: undo app.py's TEMP redirect so osu! uses the real one."""
    env = dict(os.environ)
    for key in ("TEMP", "TMP"):
        if env.get(f"OBD_ORIGINAL_{key}"):
            env[key] = env[f"OBD_ORIGINAL_{key}"]
    return env


def _association_exe(prog_id):
    """Executable registered for a file type, e.g. 'osustable.File.osz' → C:\\...\\osu!.exe."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            command = winreg.QueryValue(key, None)
    except (ImportError, OSError):
        return None
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command or "")
    return (m.group(1) or m.group(2)) if m else None


def _is_lazer(exe):
    return (Path(exe).parent / "osu.Game.dll").exists()


def _exe_in(folder):
    """osu!.exe inside a folder the user picked (lazer's install root keeps it under current/)."""
    if not folder:
        return []
    p = Path(folder)
    if p.suffix.lower() == ".exe":
        return [p]
    return [p / "osu!.exe", p / "current" / "osu!.exe"]


def osu_in_folder(client, folder):
    """The osu!stable/lazer exe inside a user-picked folder, or None."""
    for exe in _exe_in(folder):
        if exe.is_file() and _is_lazer(exe) == (client == "lazer"):
            return str(exe)
    return None


def find_osu(client, songs_dir="", custom=""):
    """Path to the osu!stable or osu!lazer executable, or None if it isn't installed.

    Installs can live anywhere, so check (in order) the folder the user picked, the folder
    above their Songs folder, the program Windows opens .osz files with, and the default paths.
    """
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    if client == "stable":
        candidates = [*_exe_in(custom),
                      Path(songs_dir).parent / "osu!.exe" if songs_dir else None,
                      _association_exe("osustable.File.osz"),
                      _association_exe("osu!"),
                      local / "osu!" / "osu!.exe"]
    else:
        candidates = [*_exe_in(custom),
                      _association_exe("osu.File.osz"),
                      local / "osulazer" / "current" / "osu!.exe",
                      local / "osulazer" / "osu!.exe"]
    for exe in candidates:
        if exe and Path(exe).is_file() and _is_lazer(exe) == (client == "lazer"):
            return str(exe)
    return None


def import_into_osu(path, client, songs_dir="", custom_paths=None):
    """Open an .osz with the chosen client (the other one if it's missing). Returns what was used."""
    other = "lazer" if client == "stable" else "stable"
    for c in (client, other):
        exe = find_osu(c, songs_dir, (custom_paths or {}).get(c, ""))
        if exe:
            import subprocess
            subprocess.Popen([exe, str(path)], env=_child_env(), cwd=str(Path(exe).parent))
            return c
    open_file(path)
    return "default"


def open_file(path):
    """Hand a file/folder to the OS — .osz files open in whichever osu! owns the file type."""
    import subprocess, sys
    if os.name == "nt":
        # explorer hands the file to its default app from the desktop shell, so that app
        # doesn't inherit our redirected TEMP the way os.startfile's children would
        subprocess.Popen(["explorer", str(Path(path))])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
