"""osu! Beatmap Downloader: local web UI.

Run `python app.py` (or start.bat, or the built exe) and a browser tab opens. Everything stays on
this machine: the server only listens on 127.0.0.1.

Main flow: fetch a list over plain HTTP, download from community mirrors. No account, no Chrome.
Optional last step: sign in and fetch whatever no mirror had from osu.ppy.sh.
"""
import atexit
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import mirrors
import osu_api
import osu_audio
import osu_local

FROZEN = getattr(sys, "frozen", False)  # running as the PyInstaller build
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # bundled resources
# Portable: everything the app creates lives next to the exe (or the project folder from source).
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def _writable(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


DATA = APP_DIR / "data"
if not _writable(DATA):
    # e.g. unzipped into Program Files, so fall back to the user's AppData
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "osu! Beatmap Downloader"
    DATA = APP_DIR / "data"
    DATA.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOADS = APP_DIR / "downloads"
TEMP_DIR = DATA / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
# Selenium Manager caches ChromeDriver here, and Chrome/ChromeDriver put their scratch files
# in TEMP, so both stay inside the app folder instead of the user profile.
os.environ["SE_CACHE_PATH"] = str(DATA / "selenium")
for _key in ("TEMP", "TMP"):  # remembered so osu! can be launched with the real TEMP
    os.environ.setdefault(f"OBD_ORIGINAL_{_key}", os.environ.get(_key, ""))
    os.environ[_key] = str(TEMP_DIR)
PROFILE_DIR = DATA / "chrome-profile"  # only used by the optional osu! step
CONFIG_FILE = DATA / "config.json"
INDEX = ROOT / "web" / "index.html"
PREFERRED_PORT = 8765
APP_ID = "osu-beatmap-downloader"

DEFAULT_OPTS = {"no_video": True, "auto_open": False, "import_client": "stable", "show_browser": False,
                "workers": 10, "delay": 5, "batch": 60, "rest": 15, "cooldown": 300, "timeout": 90}


def _load(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, value):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2), "utf-8")
    tmp.replace(path)


class State:
    def __init__(self):
        self.lock = threading.RLock()
        cfg = _load(CONFIG_FILE, {})
        self.folder = cfg.get("folder") or str(DEFAULT_DOWNLOADS)
        self.songs_dir = cfg.get("songs_dir", "")
        self.osu_paths = {"stable": "", "lazer": "", **cfg.get("osu_paths", {})}  # user-picked installs
        self.opts = {**DEFAULT_OPTS, **cfg.get("opts", {})}
        saved = {k: v for k, v in cfg.get("mirrors", {}).items() if k in mirrors.BY_KEY}  # drop retired mirrors
        self.mirrors = {**mirrors.DEFAULT_ENABLED, **saved}
        self.last_user_query = cfg.get("last_user_query", "")
        self.user = cfg.get("user") if PROFILE_DIR.is_dir() else None  # confirmed when needed
        self.owned = set()
        self.queue = []
        self.logs = []
        self.busy = ""
        self.busy_token = None
        self.job = None            # mirror download
        self.osu_job = None        # optional osu.ppy.sh download
        self.importer = None       # sending finished maps to osu!
        self.muter = osu_audio.OsuMuter()  # silences osu! while maps pour in
        self.signing_in = None     # (cancel, done) events while the sign-in window is open
        self.checked_missing = False

    # -- persistence
    def save_config(self):
        _save(CONFIG_FILE, {
            "user": self.user, "folder": self.folder, "songs_dir": self.songs_dir,
            "osu_paths": self.osu_paths, "opts": self.opts, "mirrors": self.mirrors,
            "last_user_query": self.last_user_query,
        })

    # -- logging / queue
    def log(self, level, msg):
        with self.lock:
            self.logs.append({"i": len(self.logs), "t": time.strftime("%H:%M:%S"), "level": level, "msg": msg})
            del self.logs[:-2000]  # keep memory bounded
        print(f"[{level}] {msg}", flush=True)

    def on_item(self, item):
        pass  # the queue items are shared with the job, so the UI already sees every change

    def clients(self):
        """Which osu! installs exist (cached briefly, since this runs on every UI poll)."""
        now = time.time()
        if now - getattr(self, "_clients_at", 0) > 10:
            self._clients = {c: osu_local.find_osu(c, self.songs_dir, self.osu_paths[c])
                             for c in ("stable", "lazer")}
            self._clients_at = now
        return self._clients

    def running(self):
        return bool(self.job and self.job.thread.is_alive())

    def osu_running(self):
        return bool(self.osu_job and self.osu_job.thread.is_alive())

    def check_job(self):
        if self.osu_job and getattr(self.osu_job, "signed_out", False) and self.user:
            self.user = None
            self.save_config()

    def refresh_owned(self):
        self.owned = set()
        if self.songs_dir:
            try:
                self.owned = osu_api.scan_songs_folder(self.songs_dir)
            except (ValueError, OSError) as e:
                self.log("warn", f"Couldn't read Songs folder: {e}")

    def classify(self, item):
        sid = item["id"]
        if sid in self.owned:
            return "have", "Already in your osu! Songs folder"
        if osu_api.find_osz(self.folder, sid):
            return "have", "Already in the download folder"
        return "queued", ""

    def set_queue(self, items):
        self.refresh_owned()
        for it in items:
            it["status"], it["note"] = self.classify(it)
            it.setdefault("error", "")
            it.setdefault("source", "")
            it["tried"] = []
        with self.lock:
            self.queue = items
            self.checked_missing = False

    def snapshot(self, log_since):
        self.check_job()
        with self.lock:
            counts = {}
            for it in self.queue:
                counts[it["status"]] = counts.get(it["status"], 0) + 1
            job = self.job or self.osu_job
            remaining = counts.get("queued", 0) + counts.get("downloading", 0)
            return {
                "user": self.user, "signing_in": bool(self.signing_in),
                "folder": self.folder, "songs_dir": self.songs_dir, "opts": self.opts,
                "app": APP_ID, "last_user_query": self.last_user_query,
                "clients": self.clients(), "mirrors": self.mirror_rows(),
                "queue": self.queue, "counts": counts, "busy": self.busy,
                "running": self.running(), "osu_running": self.osu_running(),
                "checked_missing": self.checked_missing,
                "osu_muted": self.muter.muted, "mute_error": self.muter.error,
                "importing": self.importer.status() if self.importer and self.importer.running() else None,
                "job": job.status(remaining) if job and job.thread.is_alive() else None,
                "paused": bool(job and job.pause_flag.is_set()),
                "logs": [l for l in self.logs if l["i"] >= log_since],
            }

    def mirror_rows(self):
        """Mirror cards for the UI: live while downloading, last run's totals afterwards."""
        if self.running():
            return self.job.status(0)["mirrors"]
        last = {}
        if self.job:  # keep the finished run's numbers on screen
            last = {m["key"]: m for m in self.job.status(0)["mirrors"]}
        rows = []
        for spec in mirrors.MIRRORS:
            was = last.get(spec["key"], {})
            rows.append({
                "key": spec["key"], "name": spec["name"], "by": spec.get("by", ""),
                "home": spec.get("home", ""), "about": spec.get("about", ""),
                "coverage": spec["coverage"],
                "enabled": bool(self.mirrors.get(spec["key"])),
                "inflight": 0, "cap": 0, "max_cap": spec["cap"], "speed": was.get("speed"),
                "done": was.get("done", 0), "failed": was.get("failed", 0), "mb": was.get("mb", 0),
                "waiting": 0, "quota_note": "", "last_error": was.get("last_error", ""),
            })
        return rows


S = State()


# ---------------------------------------------------------------- actions

def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def in_background(label, fn):
    token = object()
    S.busy, S.busy_token = label, token  # set before the thread starts so a second click can't slip in

    def run():
        try:
            fn()
        except Exception as e:
            S.log("error", osu_local.friendly_error(e))
        finally:
            if S.busy_token is token:  # the task may have updated the label with its progress
                S.busy, S.busy_token = "", None
    threading.Thread(target=run, daemon=True).start()


def act_fetch(body):
    if S.running() or S.osu_running() or S.busy:
        raise ValueError("Wait for the current task to finish first.")
    query = (body.get("user") or "").strip() or (str(S.user["id"]) if S.user else "")
    if not query:
        raise ValueError("Enter a player name, profile link or user ID.")
    kind = body.get("kind", "most_played")
    if kind not in osu_api.LIST_KINDS:
        raise ValueError("Unknown list type.")
    limit = max(1, min(int(body.get("limit") or 100), 20000))
    S.last_user_query = body.get("user", "")
    S.save_config()

    def run():
        S.log("info", f"Fetching up to {limit} maps from {query}'s {kind.replace('_', ' ')} list…")
        items = osu_api.fetch_user_maps(query, kind, limit,
                                        on_progress=lambda n: setattr(S, "busy", f"Fetching… {n} maps"))
        S.set_queue(items)
        have = sum(1 for i in items if i["status"] == "have")
        S.log("ok", f"Found {plural(len(items), 'beatmap set')}"
                          + (f", {have} of which you already have." if have else "."))
    in_background("Fetching…", run)


def act_paste(body):
    if S.running() or S.osu_running() or S.busy:
        raise ValueError("Wait for the current task to finish first.")
    ids = osu_api.parse_ids(body.get("text", ""))
    if not ids:
        raise ValueError("No beatmap IDs or links found in that text.")
    S.set_queue([{"id": i, "title": "", "artist": "", "cover": "", "status_hint": ""} for i in ids])
    S.log("ok", f"Loaded {len(ids)} beatmap sets from your list.")


def act_settings(body):
    with S.lock:
        if "folder" in body and body["folder"].strip():
            S.folder = body["folder"].strip()
        if "songs_dir" in body:
            S.songs_dir = body["songs_dir"].strip()
            S._clients_at = 0
        for client, folder in (body.get("osu_paths") or {}).items():
            if client not in S.osu_paths:
                continue
            if folder and not osu_local.osu_in_folder(client, folder):
                raise ValueError(f"Couldn't find osu!{client} in that folder. Pick the folder that contains osu!.exe.")
            S.osu_paths[client] = folder
            S._clients_at = 0
        if "opts" in body:
            for k, v in body["opts"].items():
                if k == "import_client" and v not in ("stable", "lazer"):
                    raise ValueError("Pick osu!stable or osu!lazer.")
                if k in DEFAULT_OPTS:
                    S.opts[k] = type(DEFAULT_OPTS[k])(v)
        S.save_config()
        if ("folder" in body or "songs_dir" in body) and not S.running():
            S.set_queue(S.queue)  # re-evaluate what's already owned


def act_mirror(body):
    key, enabled = body.get("key"), bool(body.get("enabled"))
    spec = mirrors.BY_KEY.get(key)
    if not spec:
        raise ValueError("Unknown mirror.")
    S.mirrors[key] = enabled
    S.save_config()
    if S.running():
        S.job.set_mirror(key, enabled)


def act_start(_):
    if S.running() or S.osu_running():
        raise ValueError("Already downloading.")
    if S.busy:
        raise ValueError("Wait for the current task to finish first.")
    if not any(i["status"] == "queued" for i in S.queue):
        raise ValueError("Nothing to download: the queue is empty or you already have everything.")
    if not any(v and k in mirrors.BY_KEY for k, v in S.mirrors.items()):
        raise ValueError("Turn on at least one mirror first.")
    for it in S.queue:
        it["tried"] = []
        it.pop("attempts", None)
        it.pop("hedged", None)
    opts = {**S.opts, "songs_dir": S.songs_dir, "osu_paths": dict(S.osu_paths)}
    S.job = mirrors.MirrorDownloader(S.queue, S.folder, opts, S.on_item, S.log, dict(S.mirrors))
    S.checked_missing = False
    S.job.start()


def act_pause(_):
    job = S.job if S.running() else (S.osu_job if S.osu_running() else None)
    if job:
        if job.pause_flag.is_set():
            job.pause_flag.clear()
            S.log("info", "Resumed.")
        else:
            job.pause_flag.set()
            S.log("info", "Paused.")


def act_stop(_):
    for job in (S.job, S.osu_job):
        if job and job.thread.is_alive():
            job.stop()
    S.log("info", "Stopping…")


def act_retry(_):
    if S.running() or S.osu_running():
        raise ValueError("Wait for the current run to finish.")
    n = 0
    for it in S.queue:
        if it["status"] in ("failed", "cancelled", "not_on_mirrors"):
            it.update(status="queued", error="", tried=[])
            it.pop("attempts", None)
            n += 1
    S.checked_missing = False
    S.log("info", f"Re-queued {n} maps.")


def act_toggle(body):
    """Flip a single item between skipped and queued."""
    if S.running() or S.osu_running():
        raise ValueError("Can't change the queue while downloading.")
    for it in S.queue:
        if it["id"] == str(body.get("id")):
            if it["status"] in ("have", "skipped"):
                it["status"] = "queued"
            elif it["status"] == "queued":
                it["status"] = "skipped"


def act_clear_queue(_):
    if S.running() or S.osu_running():
        raise ValueError("Stop the download first.")
    S.queue = []


# ---------------------------------------------------------------- the optional osu! step

def missing_items():
    return [i for i in S.queue if i["status"] in ("not_on_mirrors", "osu_only", "gone")]


def act_check_missing(_):
    """Ask osu! which of the maps no mirror had still exist."""
    if S.busy or S.running():
        raise ValueError("Wait for the current task to finish first.")
    todo = [i for i in S.queue if i["status"] in ("not_on_mirrors", "failed")]
    if not todo:
        raise ValueError("Nothing to check.")

    def run():
        S.log("info", f"Checking {plural(len(todo), 'map')} against osu.ppy.sh…")
        gone = only = 0
        for n, item in enumerate(todo, 1):
            if S.busy == "":            # stopped
                break
            S.busy = f"Checking on osu!… {n}/{len(todo)}"
            info = osu_api.lookup_beatmapset(item["id"])
            if info.get("exists") is False:
                item.update(status="gone", error="Deleted from osu!")
                gone += 1
            elif info.get("exists") is None:
                item.update(error=f"Couldn't check ({info.get('reason', 'error')})")
            elif info.get("download_disabled"):
                item.update(status="gone", error="Download disabled on osu! " +
                                                 (info.get("more_information") or "(usually a copyright claim)"))
                gone += 1
            else:
                item.update(status="osu_only", error="",
                            title=item.get("title") or info.get("title", ""),
                            artist=item.get("artist") or info.get("artist", ""))
                only += 1
        S.checked_missing = True
        S.log("ok", f"{plural(only, 'map')} can still be downloaded from osu!; "
                          f"{gone} {'is' if gone == 1 else 'are'} gone for good.")
    in_background("Checking on osu!…", run)


def act_start_osu(_):
    """Download the 'osu! only' maps with Chrome, after signing in."""
    if S.running() or S.osu_running() or S.busy:
        raise ValueError("Wait for the current task to finish first.")
    if not S.user:
        raise ValueError("Sign in to osu! first.")
    todo = [i for i in S.queue if i["status"] == "osu_only"]
    if not todo:
        raise ValueError("No maps need osu! right now.")
    import osu_browser
    for it in todo:
        it["status"] = "queued"
    opts = {**S.opts, "songs_dir": S.songs_dir, "osu_paths": dict(S.osu_paths)}
    S.osu_job = osu_browser.BrowserDownloader(todo, PROFILE_DIR, S.folder, opts, S.on_item, S.log)
    S.osu_job.start()


def act_login(_):
    if S.running() or S.osu_running() or S.busy:
        raise ValueError("Wait for the current task to finish first.")
    import osu_browser
    cancel, done = threading.Event(), threading.Event()
    S.signing_in = (cancel, done)

    def run():
        try:
            S.log("info", "Opened a Chrome window. Sign in to osu! there.")
            user = osu_browser.sign_in(PROFILE_DIR, cancel, done)
            S.user = user
            S.save_config()
            S.log("ok", f"Signed in as {user['username']}.")
        except osu_browser.SignInCancelled as e:
            S.log("info", str(e))
        finally:
            S.signing_in = None
    in_background("Waiting for you to sign in…", run)


def act_cancel_login(_):
    if S.signing_in:
        S.signing_in[0].set()


def act_finish_login(_):
    if S.signing_in:
        S.signing_in[1].set()


def act_logout(_):
    if S.running() or S.osu_running() or S.busy:
        raise ValueError("Wait for the current task to finish first.")
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    S.user = None
    S.save_config()
    S.log("info", "Signed out. This app no longer has access to your osu! account.")


# ---------------------------------------------------------------- importing and folders

class Importer:
    """Hands .osz files to osu! one at a time, and can be paused, resumed or stopped in between."""

    def __init__(self, files, client):
        self.files, self.client = files, client
        self.sent = 0
        self.go = threading.Event()
        self.go.set()                   # cleared while paused
        self.stopped = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def running(self):
        return self.thread.is_alive()

    def status(self):
        return {"sent": self.sent, "total": len(self.files), "client": self.client, "paused": not self.go.is_set()}

    def pause(self):
        self.go.clear()

    def resume(self):
        self.go.set()

    def stop(self):
        self.stopped = True
        self.go.set()                   # wake a paused loop so it can exit

    def _run(self):
        S.log("info", f"Sending {plural(len(self.files), 'map')} to osu!{self.client}…")
        used = self.client
        try:
            for f in self.files:
                self.go.wait()
                if self.stopped:
                    break
                if not os.path.exists(f):   # osu! already took it, or it was deleted
                    self.sent += 1
                    continue
                used = osu_local.import_into_osu(f, self.client, S.songs_dir, S.osu_paths)
                self.sent += 1
                time.sleep(3 if self.sent == 1 else 0.4)  # give osu! a moment to start before sending the rest
        except Exception as e:
            S.log("error", osu_local.friendly_error(e))
            return
        if used != self.client:
            S.log("warn", f"osu!{self.client} isn't installed, so the maps went to "
                          f"{'osu!' + used if used != 'default' else 'the default app'} instead.")
        if self.stopped:
            S.log("info", f"Stopped importing after {self.sent} of {len(self.files)}. "
                          f"The rest are still in the download folder.")
        else:
            S.log("ok", "Handed everything to osu!, which will finish importing on its own.")


def importer():
    if not (S.importer and S.importer.running()):
        raise ValueError("Nothing is being imported.")
    return S.importer


def act_import_pause(_):
    importer().pause()
    S.log("info", f"Paused importing at {S.importer.sent} of {len(S.importer.files)}.")


def act_import_resume(_):
    importer().resume()
    S.log("info", "Resumed importing.")


def act_import_stop(_):
    importer().stop()


def act_mute(body):
    muted = bool(body.get("muted"))
    S.muter.set(muted)
    S.log("info", "Muted osu! (only osu!, nothing else on your PC). It'll be unmuted when you click again "
                  "or close this app." if muted else "Unmuted osu!.")


def act_open_all(_):
    if S.importer and S.importer.running():
        raise ValueError("Already importing. Pause or stop it first.")
    files = [f for it in S.queue if it["status"] == "done"
             for f in [it.get("file") or osu_api.find_osz(S.folder, it["id"])] if f and os.path.exists(f)]
    if not files:
        files = sorted(str(p) for p in Path(S.folder).glob("*.osz"))
    if not files:
        raise ValueError("No .osz files waiting in the download folder (osu! may have imported them already).")
    S.importer = Importer(files, S.opts["import_client"])
    S.importer.thread.start()


def act_open_folder(body):
    path = Path(S.songs_dir if body.get("which") == "songs" else S.folder)
    path.mkdir(parents=True, exist_ok=True)
    osu_local.open_file(str(path))


def act_browse(body):
    """Native folder picker, run as a child process so tkinter can't upset the server threads."""
    initial = body.get("initial") or str(Path.home())
    cmd = [sys.executable] + ([] if FROZEN else [str(Path(__file__).resolve())])
    out = subprocess.run(cmd + ["--pick-folder", initial, body.get("title", "Choose a folder")],
                         capture_output=True, text=True, timeout=600,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    path = out.stdout.strip()
    return {"path": os.path.normpath(path) if path else ""}


def pick_folder(initial, title):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    print(filedialog.askdirectory(initialdir=initial, title=title) or "", flush=True)


def act_scan(_):
    if not S.songs_dir:
        raise ValueError("Pick your osu! Songs folder first.")
    ids = osu_api.scan_songs_folder(S.songs_dir)
    S.log("ok", f"Your Songs folder has {len(ids)} beatmap sets.")
    return {"ids": sorted(ids, key=int)}


ACTIONS = {
    "fetch": act_fetch, "paste": act_paste, "settings": act_settings, "mirror": act_mirror,
    "start": act_start, "pause": act_pause, "stop": act_stop, "retry": act_retry,
    "toggle": act_toggle, "clear-queue": act_clear_queue,
    "check-missing": act_check_missing, "start-osu": act_start_osu,
    "login": act_login, "cancel-login": act_cancel_login, "finish-login": act_finish_login,
    "logout": act_logout, "open-all": act_open_all, "import-pause": act_import_pause,
    "import-resume": act_import_resume, "import-stop": act_import_stop, "mute": act_mute, "open-folder": act_open_folder,
    "browse": act_browse, "scan": act_scan,
}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _allowed(self):
        # Block DNS-rebinding and cross-site requests: only our own page may talk to us.
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self._send(200, S.snapshot(int(q.get("since", ["0"])[0])))
        if url.path == "/api/export":
            which = q.get("which", ["all"])[0]
            if which == "library":
                ids = sorted(osu_api.scan_songs_folder(S.songs_dir), key=int) if S.songs_dir else []
            elif which == "missing":
                ids = [i["id"] for i in missing_items()]
            else:
                ids = [i["id"] for i in S.queue if which == "all" or i["status"] == which]
            return self._send(200, ("\n".join(ids) + "\n").encode(), "text/plain; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="beatmaps_{which}.txt"'})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # The custom header forces a CORS preflight, which we never approve, so
        # other websites can't drive this server from the user's browser.
        if not self._allowed() or self.headers.get("X-Osu-Dl") != "1":
            return self._send(403, {"error": "forbidden"})
        name = urlparse(self.path).path.removeprefix("/api/")
        fn = ACTIONS.get(name)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            result = fn(body) or {}
            self._send(200, {"ok": True, **result})
        except Exception as e:
            self._send(400, {"ok": False, "error": str(e) or type(e).__name__})


def already_running(port):
    """True if another copy of this app is already serving on the port."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state?since=999999")
        with urllib.request.urlopen(req, timeout=1) as r:
            state = json.loads(r.read())
            return state.get("app") == APP_ID or "history_count" in state  # (older versions)
    except (OSError, ValueError):
        return False


def free_port(preferred):
    for port in range(preferred, preferred + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found.")


PORT = PREFERRED_PORT
JOB = None  # Windows job handle that closes our Chrome processes when the app exits


_console_handler = None


def unmute_on_exit():
    """Give osu! its sound back however the app ends, including the console window's close button."""
    global _console_handler
    atexit.register(S.muter.restore)
    if os.name == "nt":
        def on_console_event(_event):
            S.muter.restore()
            return False                # let Windows carry on closing us
        _console_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(on_console_event)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)


def main():
    global PORT, JOB
    for stream in (sys.stdout, sys.stderr):
        if stream:  # never let an odd character in a map title crash the log
            stream.reconfigure(errors="replace")
    if "--selftest" in sys.argv:       # confirms a build has everything, including the optional parts
        import osu_browser
        print("modules ok:", ", ".join(m.__name__ for m in (osu_api, mirrors, osu_local, osu_audio, osu_browser)))
        print("mirrors:", ", ".join(m["name"] for m in mirrors.MIRRORS))
        print("chrome:", osu_browser.find_chrome() or "not installed (only needed for the osu! step)")
        return
    if "--pick-folder" in sys.argv:
        i = sys.argv.index("--pick-folder")
        return pick_folder(*sys.argv[i + 1:i + 3])

    open_browser = "--no-browser" not in sys.argv
    preferred = PREFERRED_PORT
    if "--port" in sys.argv:
        preferred = int(sys.argv[sys.argv.index("--port") + 1])
    if already_running(preferred):
        # double-clicking the exe again just brings the existing app back up
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{preferred}/")
        print("Already running, so it was opened in your browser.")
        return

    JOB = osu_local.close_chrome_with_app()
    # we're the only copy running, so anything using our profile or temp is from an earlier session
    stopped = osu_local.close_leftover_chrome(PROFILE_DIR)
    if stopped:
        S.log("info", f"Closed {stopped} leftover Chrome process(es) from an earlier session.")
    for leftover in TEMP_DIR.iterdir():
        try:
            shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
        except OSError:
            pass  # still locked by something; try again next launch

    PORT = free_port(preferred)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    if os.name == "nt":
        os.system("title osu! Beatmap Downloader")
    print("osu! Beatmap Downloader\n"
          f"  Running at {url}\n"
          "  Your browser should open automatically. Keep this window open while downloading;\n"
          "  close it (or press Ctrl+C) to quit.\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    unmute_on_exit()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for job in (S.job, S.osu_job):
            if job:
                job.stop()
        print("Bye!")


if __name__ == "__main__":
    main()
