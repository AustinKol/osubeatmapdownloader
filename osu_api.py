"""Talking to osu.ppy.sh over plain HTTP: profile lists, id parsing, map lookups.

Nothing here needs an account or a browser.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OSU = "https://osu.ppy.sh"
# Keep this plain: mirrors behind Cloudflare reject user agents containing URLs.
UA = "osu-beatmap-downloader/1.0"

LIST_KINDS = ("most_played", "favourite", "ranked", "loved", "graveyard", "guest", "nominated", "pending")
# statuses every mirror carries vs. the ones only full-archive mirrors have
WIDELY_MIRRORED = ("ranked", "approved", "loved", "qualified")


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


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
    """[{id, title, artist, map_status, cover}] from a profile list, deduplicated."""
    uid = resolve_user_id(user)
    out, seen, offset = [], set(), 0
    while len(out) < limit:
        page = get_json(f"{OSU}/users/{uid}/beatmapsets/{kind}?offset={offset}&limit=100")
        if not page:
            break
        for item in page:
            s = item["beatmapset"] if kind == "most_played" else item
            sid = str(s["id"])
            if sid in seen:
                continue
            seen.add(sid)
            out.append({"id": sid, "title": s.get("title", ""), "artist": s.get("artist", ""),
                        # the map's ranked status; the queue uses "status" for download state
                        "map_status": (s.get("status") or "").lower(),
                        "cover": (s.get("covers") or {}).get("list", "")})
            if len(out) >= limit:
                break
        offset += len(page)
        if on_progress:
            on_progress(len(out))
        if len(page) < 100:
            break
    return out


def lookup_beatmapset(sid, timeout=30):
    """What osu! itself says about a set: {exists, status, download_disabled, title, artist}.

    Used only for the handful of maps no mirror had, so the 200 KB page is fine.
    """
    url = f"{OSU}/beatmapsets/{sid}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return {"exists": False, "reason": "deleted"}
        return {"exists": None, "reason": f"http {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"exists": None, "reason": type(e).__name__}
    m = re.search(r'id="json-beatmapset"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {"exists": None, "reason": "unreadable page"}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {"exists": None, "reason": "unreadable page"}
    availability = data.get("availability") or {}
    return {
        "exists": True,
        "status": (data.get("status") or "").lower(),
        "download_disabled": bool(availability.get("download_disabled")),
        "more_information": availability.get("more_information"),
        "title": data.get("title", ""),
        "artist": data.get("artist", ""),
    }


BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def osz_name(sid, artist="", title=""):
    """The name osu! itself uses: '<id> Artist - Title.osz'."""
    label = " - ".join(p for p in (artist.strip(), title.strip()) if p)
    label = BAD_FILENAME_CHARS.sub("", label).strip(" .")
    name = f"{sid} {label}".strip() if label else str(sid)
    return f"{name[:150]}.osz"


def find_osz(folder, sid):
    """A finished .osz for this set in the folder, if any."""
    try:
        for entry in os.scandir(folder):
            if entry.name.endswith(".osz") and re.match(rf"{sid}(\D|$)", entry.name):
                return entry.path
    except (FileNotFoundError, NotADirectoryError):
        pass
    return None


def scan_songs_folder(path):
    """Beatmapset IDs in an osu!stable Songs folder (folders are named '<id> Artist - Title')."""
    ids = set()
    p = Path(path)
    if not p.is_dir():
        raise ValueError("That Songs folder doesn't exist.")
    for entry in os.scandir(p):
        m = re.match(r"(\d+)\s", entry.name)
        if m:
            ids.add(m.group(1))
    return ids
