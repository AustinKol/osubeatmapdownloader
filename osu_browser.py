"""The optional osu.ppy.sh step: signing in and downloading with Chrome.

Nothing here runs in the main flow. It is used only for maps that no mirror had, and only
after the user chooses to sign in.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from osu_api import OSU, UA, find_osz
from osu_local import CREATE_BREAKAWAY_FROM_JOB, _child_env, friendly_error, import_into_osu


def make_driver(download_dir=None, headless=True, profile_dir=None):
    from selenium import webdriver  # imported lazily so the UI starts instantly

    opts = webdriver.ChromeOptions()
    if profile_dir:
        opts.add_argument(f"--user-data-dir={profile_dir}")
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


def current_user(driver):
    """The osu! account this browser is signed in to, or None."""
    driver.get(f"{OSU}/home")
    return driver.execute_script(
        "const u = window.currentUser || {};"
        "return u.id ? {id: u.id, username: u.username, avatar: u.avatar_url} : null;")


# Chrome can only open a profile once at a time: the sign-in window, the sign-in check and
# the downloader all take this lock while they use it.
PROFILE_LOCK = threading.RLock()


def _hold_profile():
    if not PROFILE_LOCK.acquire(timeout=5):
        raise RuntimeError("The sign-in window is still open. Finish signing in (or cancel) first.")


def check_profile(profile_dir):
    """Who the saved Chrome profile is signed in as (starts a hidden Chrome briefly)."""
    if not Path(profile_dir).is_dir():
        return None
    _hold_profile()
    try:
        driver = make_driver(profile_dir=profile_dir)
        try:
            return current_user(driver)
        finally:
            driver.quit()
    finally:
        PROFILE_LOCK.release()


# ---------------------------------------------------------------- sign-in window
#
# osu!'s login form is protected by Cloudflare Turnstile, which fails in any browser that is
# automated or even just has a DevTools debugging port open. So the sign-in window is a plain
# Chrome window on the app's own profile, with nothing attached. The user tells the app when
# they're done (or closes the window); the app then closes Chrome normally, so the session is
# saved to disk, and checks the profile with a hidden Chrome.

LOGIN_URL = f"{OSU}/home/account/edit"  # logged-out visitors get the sign-in form here


class SignInCancelled(Exception):
    pass


def find_chrome():
    """Path to chrome.exe (or the platform equivalent), or None."""
    if os.name == "nt":
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    path = winreg.QueryValue(k, None)
                if path and Path(path).is_file():
                    return path
            except OSError:
                pass
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(base):
                path = Path(os.environ[base]) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if path.is_file():
                    return str(path)
        return None
    import shutil
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if Path(mac).exists() else None


def _close_gracefully(proc):
    """Close Chrome the way clicking X does, so it writes its cookies to disk."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def close_window(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True
        user32.EnumWindows(close_window, 0)
    else:
        proc.terminate()  # Chrome treats SIGTERM as a normal quit
    try:
        proc.wait(20)
    except Exception:
        proc.kill()


def sign_in(profile_dir, cancel, done):
    """Open a Chrome window for the user to sign in to osu!, then return who signed in.

    `done` is set when the user says they've finished; closing the window counts too.
    Raises SignInCancelled if cancelled or if the profile still isn't signed in.
    """
    _hold_profile()
    try:
        return _sign_in(profile_dir, cancel, done)
    finally:
        PROFILE_LOCK.release()


def _sign_in(profile_dir, cancel, done):
    import subprocess
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Google Chrome isn't installed. Install it from google.com/chrome and try again.")
    profile = Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        chrome, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--window-size=620,860", "--new-window", LOGIN_URL,
    ], env=_child_env())

    while proc.poll() is None and not (cancel.is_set() or done.is_set()):
        time.sleep(0.5)
    if proc.poll() is None:
        _close_gracefully(proc)
    time.sleep(1)  # let Chrome's helper processes let go of the profile

    if cancel.is_set():
        raise SignInCancelled("Sign-in cancelled.")
    user = check_profile(profile)
    if not user:
        raise SignInCancelled("You're not signed in yet. Click “Sign in with osu!” to try again.")
    return user


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
# more for osu!supporters). When it refuses, retry on this cycle. It adds up to an hour,
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


class BrowserDownloader:
    """Downloads the leftover maps from osu.ppy.sh in a hidden Chrome, respecting its hourly limit."""

    def __init__(self, items, profile_dir, folder, opts, emit, log):
        self.items = items          # list of dicts; this class mutates item["status"]
        self.profile_dir = profile_dir
        self.folder = Path(folder)
        self.opts = opts
        self.emit = emit            # emit(item) after any status change
        self.log = log              # log(level, message)
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.warned_client = False
        self.signed_out = False
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
        per_map = self.per_map or float(self.opts.get("delay", 5)) + 3
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
        locked = False
        try:
            _hold_profile()
            locked = True
            self.log("info", "Starting Chrome in the background…")
            driver = make_driver(self.folder, headless=not self.opts.get("show_browser"),
                                 profile_dir=self.profile_dir)
            user = current_user(driver)
            if not user:
                self.signed_out = True
                raise PermissionError("You're signed out of osu! Click “Sign in with osu!” and try again.")
            self.log("ok", f"Signed in as {user['username']}.")
            self._loop(driver)
        except Exception as e:  # surface anything unexpected to the UI instead of dying silently
            self.log("error", friendly_error(e))
        finally:
            if driver:
                # give in-flight downloads a moment to land before closing Chrome
                deadline = time.time() + 30
                while time.time() < deadline and any(self.folder.glob("*.crdownload")):
                    time.sleep(0.5)
                driver.quit()
            if locked:
                PROFILE_LOCK.release()
            for item in self.items:
                if item["status"] in ("queued", "downloading"):
                    self._set(item, "cancelled" if self.stop_flag.is_set() else "failed")
            self.log("info", "Stopped." if self.stop_flag.is_set() else "All done.")

    def _loop(self, driver):
        delay = float(self.opts.get("delay", 5))
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
                    self.log("warn", f"Several failures in a row, so osu! may be rate limiting. "
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
            self.log("warn", f"osu!{wanted} isn't installed, so importing with {other} instead.")

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


