"""Draw docs/images/scheduler-{dark,light}.svg: how the mirror load balancer works.

One layout, two colour schemes, so the README can show the right one per GitHub theme.
"""
from pathlib import Path

W, H = 1180, 680

THEMES = {
    "dark": dict(bg="#17141a", panel="#211d25", panel2="#2a2530", line="#3a3342", text="#f1edf5",
                 muted="#a79fb0", dim="#756d7e", pink="#ff66aa", green="#6fdc8c", yellow="#ffd166",
                 red="#ff6b7a", blue="#7cc4ff"),
    "light": dict(bg="#ffffff", panel="#f6f3f8", panel2="#efe9f3", line="#ded5e6", text="#211b26",
                  muted="#5f5768", dim="#8a8193", pink="#e23d8a", green="#1f9d4c", yellow="#a9740f",
                  red="#d63a4a", blue="#2b7fd1"),
}

MIRRORS = [
    ("catboy.best", "full archive", "cap 4", "quota aware", "pink"),
    ("osudl.org", "ranked / loved only", "cap 5", "fastest", "green"),
    ("mirror.nekoha.moe", "full archive", "cap 3", "widest coverage", "blue"),
    ("osu.direct", "full archive", "cap 3", "120 req/min", "yellow"),
]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(theme):
    c = THEMES[theme]
    p = []
    add = p.append

    def text(x, y, s, size=13, fill="text", weight="400", anchor="start", family="Segoe UI, system-ui, sans-serif"):
        add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{c[fill]}" text-anchor="{anchor}">{esc(s)}</text>')

    def box(x, y, w, h, fill="panel", stroke="line", r=12, dash=None, width=1.5):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{c[fill]}" '
            f'stroke="{c[stroke]}" stroke-width="{width}"{d}/>')

    def arrow(x1, y1, x2, y2, stroke="dim", dash=None, width=1.8, marker="arrow"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        add(f'<path d="M {x1} {y1} L {x2} {y2}" stroke="{c[stroke]}" stroke-width="{width}" '
            f'fill="none" marker-end="url(#{marker}-{theme})"{d}/>')

    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="How the mirror load balancer works">')
    add(f'<defs>'
        f'<marker id="arrow-{theme}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="{c["dim"]}"/></marker>'
        f'<marker id="pink-{theme}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="{c["pink"]}"/></marker>'
        f'</defs>')
    add(f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>')

    # ---------------------------------------------------------------- 1. queue
    text(40, 42, "1. Queue", 15, "text", "700")
    text(40, 62, "from a profile list or pasted IDs", 12, "muted")
    box(30, 76, 250, 150)
    text(46, 104, "1,000 beatmapsets", 14, "text", "600")
    box(46, 120, 218, 44, "panel2", "green", 9)
    text(58, 140, "ranked / approved / loved", 12, "text", "600")
    text(58, 156, "any mirror can serve these", 11, "muted")
    box(46, 170, 218, 44, "panel2", "yellow", 9)
    text(58, 190, "graveyard / pending / unknown", 12, "text", "600")
    text(58, 206, "full archives only", 11, "muted")

    # ---------------------------------------------------------------- 2. scheduler
    text(330, 42, "2. Scheduler", 15, "text", "700")
    text(330, 62, "picks a mirror per map, never waits", 12, "muted")
    box(320, 76, 300, 330)
    rules = [
        ("Water filling", "serve whichever class is furthest", "behind, so both finish together"),
        ("Merit order", "measured MB/s x success rate", "x free slots, no fixed ranking"),
        ("Self tuning", "+1 stream after 5 clean downloads,", "halved on a refusal (AIMD)"),
        ("Instant failover", "429/403 parks that mirror only;", "404 means try the next one"),
    ]
    y = 106
    for title, l1, l2 in rules:
        box(336, y, 268, 68, "panel2", "line", 9)
        text(350, y + 22, title, 13, "pink", "700")
        text(350, y + 39, l1, 11, "muted")
        text(350, y + 53, l2, 11, "muted")
        y += 76
    arrow(280, 142, 316, 180)
    arrow(280, 192, 316, 205, "dim")

    # ---------------------------------------------------------------- 3. mirrors
    text(672, 42, "3. Mirror pool", 15, "text", "700")
    text(672, 62, "about 10 downloads in flight, all mirrors at once", 12, "muted")
    y = 82
    for name, coverage, cap, note, colour in MIRRORS:
        box(662, y, 300, 62, "panel", colour, 10)
        add(f'<circle cx="684" cy="{y + 31}" r="6" fill="{c[colour]}"/>')
        text(700, y + 26, name, 13, "text", "700")
        text(700, y + 44, f"{coverage} · {cap} · {note}", 11, "muted")
        arrow(624, 220, 656, y + 31)
        y += 72

    # ---------------------------------------------------------------- 4. checks and output
    text(672, 420, "4. Every file is checked", 15, "text", "700")
    box(662, 436, 300, 96, "panel", "line")
    text(678, 462, "a zip holding at least one .osu", 12, "text")
    text(678, 482, "the .osu names this beatmapset", 12, "text")
    text(678, 508, "anything else is thrown away and the", 11, "muted")
    text(678, 522, "map is fetched from another mirror", 11, "muted")
    arrow(930, 370, 930, 430, "dim")

    box(320, 436, 300, 96, "panel", "green")
    text(336, 462, "downloads\\ folder", 13, "text", "700")
    text(336, 482, "<id> Artist - Title.osz", 12, "muted", family="ui-monospace, Consolas, monospace")
    text(336, 508, "imported into osu!stable or lazer,", 11, "muted")
    text(336, 522, "in one click or as each map lands", 11, "muted")
    arrow(658, 484, 624, 484, "green")

    box(30, 436, 250, 96, "panel", "yellow", dash="6 5")
    text(46, 462, "Not on any mirror", 13, "yellow", "700")
    text(46, 482, "checked against osu! itself,", 11, "muted")
    text(46, 496, "then optionally downloaded", 11, "muted")
    text(46, 510, "there with your account", 11, "muted")
    text(46, 524, "(the only step needing Chrome)", 11, "dim")
    arrow(316, 484, 286, 484, "yellow", dash="6 5")

    # ---------------------------------------------------------------- footnote band
    box(30, 560, 932, 86, "panel2", "line")
    text(48, 588, "Why it is fast", 13, "text", "700")
    notes = [
        "Mirrors complement each other: one holds maps another lacks, so a 404 costs a retry, not a loss.",
        "A rate-limited mirror parks itself and the map moves on, so the pool never idles (catboy cycles ~60 maps, sits out ~50 s, returns).",
        "Measured on a real 1,000-map library: 2.5 minutes, 7.3 GB, about 49 MB/s, nothing missing.",
    ]
    yy = 608
    for n in notes:
        add(f'<circle cx="54" cy="{yy - 4}" r="2.5" fill="{c["pink"]}"/>')
        text(66, yy, n, 11.5, "muted")
        yy += 16

    add("</svg>")
    return "\n".join(p)


out = Path(__file__).resolve().parent.parent / "docs" / "images"
out.mkdir(parents=True, exist_ok=True)
for theme in THEMES:
    (out / f"scheduler-{theme}.svg").write_text(svg(theme), encoding="utf-8")
    print("wrote", out / f"scheduler-{theme}.svg")
