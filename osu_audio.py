"""Mute osu! while maps import, the way the Windows volume mixer does it.

Every app that plays sound gets an audio session per output device. We find the sessions that belong
to osu!.exe (stable and lazer share the name) and mute just those, so the rest of the PC keeps its
sound. Talks to Windows Core Audio through ctypes, so there is nothing extra to install.

A background thread keeps osu! muted while the switch is on, which also covers osu! restarting (it
does when it updates) and sessions that only appear once it starts playing. Only sessions we muted
are unmuted again, so a mute the user set themselves is left alone.
"""
import ctypes
import os
import threading
import uuid
from ctypes import HRESULT, POINTER, WINFUNCTYPE, byref, c_int, c_uint, c_void_p, wintypes

TARGETS = {"osu!.exe"}
POLL_SECONDS = 0.5


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.memmove(byref(self), uuid.UUID(text).bytes_le, ctypes.sizeof(self))


CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_ISimpleAudioVolume = GUID("87CE5498-68D6-44E5-9215-6DA47EF883D8")
E_RENDER, DEVICE_STATE_ACTIVE, CLSCTX_ALL = 0, 1, 23


def _method(index, *argtypes):
    """A COM method by its vtable slot: call it as m(interface_pointer, *args)."""
    return WINFUNCTYPE(HRESULT, *argtypes)(index, f"slot{index}")  # "this" is passed implicitly


# vtable slots (IUnknown takes 0-2)
_release = WINFUNCTYPE(wintypes.ULONG)(2, "Release")
_query = _method(0, POINTER(GUID), POINTER(c_void_p))
_enum_endpoints = _method(3, c_int, wintypes.DWORD, POINTER(c_void_p))       # IMMDeviceEnumerator
_collection_count = _method(3, POINTER(c_uint))                               # IMMDeviceCollection
_collection_item = _method(4, c_uint, POINTER(c_void_p))
_activate = _method(3, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))  # IMMDevice
_session_enumerator = _method(5, POINTER(c_void_p))                           # IAudioSessionManager2
_sessions_count = _method(3, POINTER(c_int))                                  # IAudioSessionEnumerator
_session = _method(4, c_int, POINTER(c_void_p))
_instance_id = _method(13, POINTER(c_void_p))                                # IAudioSessionControl2
_process_id = _method(14, POINTER(wintypes.DWORD))
_set_mute = _method(5, wintypes.BOOL, c_void_p)                               # ISimpleAudioVolume
_get_mute = _method(6, POINTER(wintypes.BOOL))


def _process_name(pid):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _session_key(control2):
    """Windows' own id for this session instance, stable while the session lives."""
    raw = c_void_p()
    _instance_id(control2, byref(raw))
    try:
        return ctypes.wstring_at(raw.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(raw)


def _osu_sessions():
    """[(key, ISimpleAudioVolume pointer)] for every osu! audio session on every output device.

    The caller must release each pointer. Must run on a thread that called CoInitializeEx.
    """
    found = []
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.restype = HRESULT    # raise instead of carrying on with a null pointer
    enum = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                           byref(IID_IMMDeviceEnumerator), byref(enum))
    names = {}
    try:
        devices = c_void_p()
        _enum_endpoints(enum, E_RENDER, DEVICE_STATE_ACTIVE, byref(devices))
        try:
            count = c_uint()
            _collection_count(devices, byref(count))
            for d in range(count.value):
                device, manager, sessions = c_void_p(), c_void_p(), c_void_p()
                try:
                    _collection_item(devices, d, byref(device))
                    _activate(device, byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(manager))
                    _session_enumerator(manager, byref(sessions))
                    n = c_int()
                    _sessions_count(sessions, byref(n))
                    for s in range(n.value):
                        control, control2, volume = c_void_p(), c_void_p(), c_void_p()
                        try:
                            _session(sessions, s, byref(control))
                            _query(control, byref(IID_IAudioSessionControl2), byref(control2))
                            pid = wintypes.DWORD()
                            _process_id(control2, byref(pid))
                            if not pid.value:
                                continue
                            if pid.value not in names:
                                names[pid.value] = _process_name(pid.value)
                            if names[pid.value].lower() not in TARGETS:
                                continue
                            key = _session_key(control2)
                            _query(control, byref(IID_ISimpleAudioVolume), byref(volume))
                            found.append((key, c_void_p(volume.value)))
                            volume = c_void_p()          # ownership passed to the caller
                        except OSError:
                            continue                     # a session that vanished mid-scan
                        finally:
                            for p in (volume, control2, control):
                                if p:
                                    _release(p)
                except OSError:
                    continue                             # a device that went away
                finally:
                    for p in (sessions, manager, device):
                        if p:
                            _release(p)
        finally:
            _release(devices)
    finally:
        _release(enum)
    return found


class OsuMuter:
    """Keeps osu! muted while `muted` is True; restores exactly what it changed."""

    def __init__(self):
        self.muted = False
        self.supported = os.name == "nt"
        self.error = ""
        self._ours = set()                 # sessions we muted (so we only unmute those)
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = None

    def set(self, muted):
        if not self.supported:
            raise ValueError("Muting osu! is only available on Windows.")
        self.muted = bool(muted)
        self._idle.clear()
        if not self._thread:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        self._wake.set()

    def restore(self, timeout=2.0):
        """Unmute what we muted, e.g. when the app closes. Safe to call more than once."""
        if self._thread and (self.muted or self._ours):
            self.muted = False
            self._idle.clear()
            self._wake.set()
            self._idle.wait(timeout)

    def _run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        while True:
            try:
                self._apply()
                self.error = ""
            except OSError as e:
                self.error = f"Couldn't change osu!'s volume: {e.strerror or e}"
            if not self.muted and not self._ours:
                self._idle.set()
            self._wake.wait(POLL_SECONDS if self.muted or self._ours else None)
            self._wake.clear()

    def _apply(self):
        live = set()
        sessions = _osu_sessions()
        for i, (key, volume) in enumerate(sessions):
            live.add(key)
            try:
                if self.muted:
                    if key not in self._ours:
                        current = wintypes.BOOL()
                        _get_mute(volume, byref(current))
                        if not current.value:          # leave a mute the user set alone
                            _set_mute(volume, True, None)
                            self._ours.add(key)
                elif key in self._ours:
                    _set_mute(volume, False, None)
                    self._ours.discard(key)
            except OSError:
                for _, rest in sessions[i + 1:]:       # don't leak the ones we won't get to
                    _release(rest)
                raise
            finally:
                _release(volume)
        self._ours &= live                             # forget sessions that closed
