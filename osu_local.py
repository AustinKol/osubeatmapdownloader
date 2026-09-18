"""Launching things on this PC: osu!stable/lazer detection, importing maps, process cleanup."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def close_chrome_with_app():
    """Put this process in a Windows job so every Chrome it starts dies with it.

    Without this, closing the console window leaves headless Chrome running, and that
    Chrome keeps the profile locked so the next launch can't start a browser.
    Programs that should outlive the app (osu!) are started with CREATE_BREAKAWAY_FROM_JOB.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800  # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    if not (job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            and k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())):
        return None
    return job  # the caller must keep this handle alive for the life of the app


def close_leftover_chrome(profile_dir):
    """Stop any Chrome still using our profile (e.g. from a copy of the app that crashed)."""
    lock = Path(profile_dir) / "lockfile"
    if os.name != "nt" or not lock.exists():
        return 0
    try:
        lock.unlink()  # succeeds only if no Chrome has the profile open
        return 0
    except OSError:
        pass
    import subprocess
    needle = str(Path(profile_dir)).replace("'", "''")
    script = ("$n = 0; Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='chromedriver.exe'\" | "
              f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{needle}') }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; $n")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                         timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(1)
    try:
        return int(out.stdout.strip() or 0)
    except ValueError:
        return 0


def friendly_error(e):
    """A readable one-line message for an exception (Selenium's include whole stack traces)."""
    text = str(e).strip()
    if "session not created" in text and ("crashed" in text or "DevToolsActivePort" in text):
        return ("Chrome couldn't start. Another copy of this app (or a leftover Chrome) may still be using "
                "its Chrome profile. Close other copies and try again.")
    if "session not created" in text and "version" in text.lower():
        return "Chrome couldn't start: ChromeDriver doesn't match your Chrome. Update Chrome and try again."
    first = text.removeprefix("Message: ").splitlines()[0] if text else type(e).__name__
    return first


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
            flags = CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0  # osu! keeps running after the app
            subprocess.Popen([exe, str(path)], env=_child_env(), cwd=str(Path(exe).parent), creationflags=flags)
            return c
    open_file(path)
    return "default"


def open_file(path):
    """Hand a file or folder to the OS (.osz files open in whichever osu! owns the file type)."""
    import subprocess, sys
    if os.name == "nt":
        # explorer hands the file to its default app from the desktop shell, so that app
        # doesn't inherit our redirected TEMP the way os.startfile's children would
        subprocess.Popen(["explorer", str(Path(path))], creationflags=CREATE_BREAKAWAY_FROM_JOB)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
