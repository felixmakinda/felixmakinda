"""Build the profile graphs (light + dark SVGs) from the GitHub GraphQL API.

    GITHUB_TOKEN=... python scripts/build_graphs.py

Outputs, committed by .github/workflows/profile-graphs.yml:
    assets/activity-{light,dark}.svg    contribution heatmap + weekly trend + streaks
    assets/languages-{light,dark}.svg   languages across recently active repositories

With the default Actions token only public repositories feed the language
chart. Set a PROFILE_TOKEN secret (read-only, all repositories) to include
private ones. The contribution calendar already includes private activity
when "Include private contributions" is on in the GitHub profile settings.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import urllib.request
from html import escape

USER = os.environ.get("PROFILE_USER", "felixmakinda")
TOKEN = os.environ["GITHUB_TOKEN"]
OUT = pathlib.Path(__file__).resolve().parent.parent / "assets"

# Palette shared with felixmakinda.com (kale + emerald).
THEMES = {
    "light": {
        "bg": "#ffffff",
        "border": "#e3e7e8",
        "ink": "#132128",
        "muted": "#687176",
        "accent": "#338632",
        "grid": "#eef1f1",
        "levels": ["#eef1f1", "#c6e3c3", "#8fc98b", "#55a952", "#2b6f2a"],
    },
    "dark": {
        "bg": "#101c22",
        "border": "#1f3038",
        "ink": "#f0f5f4",
        "muted": "#a1a6a9",
        "accent": "#7fc87c",
        "grid": "#18272e",
        "levels": ["#18272e", "#1f4a2c", "#2f6f3a", "#4f9e4e", "#7fc87c"],
    },
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
WIDTH = 880

# Generated, vendored or markup languages that say little about the work.
SKIP_LANGUAGES = {
    "Jupyter Notebook", "HTML", "CSS", "SCSS", "Less", "Makefile", "Procfile", "Batchfile",
    "PowerShell", "Smarty", "Mako", "Jinja", "Dockerfile", "Shell", "PLpgSQL", "MDX",
    "Cython", "Fortran", "C", "C++", "Meson", "Roff", "Lua", "EJS", "Nushell",
}
RECENT_DAYS = 730

# Distinct, recognisable colours (GitHub's Python and TypeScript are both blue).
LANGUAGE_COLORS = {
    "TypeScript": "#3178c6",
    "Python": "#f2c12e",
    "Java": "#e76f00",
    "JavaScript": "#8d99a6",
    "Go": "#00add8",
    "HCL": "#7b42bc",
    "C#": "#68217a",
}
LANGUAGE_NAMES = {"HCL": "Terraform (HCL)"}


def graphql(query: str, variables: dict | None = None) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        body = json.load(res)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]


# ── Data ──────────────────────────────────────────────────────────────────


def fetch_calendar() -> dict:
    q = """query($login:String!){ user(login:$login){ contributionsCollection{
        totalCommitContributions totalPullRequestContributions totalPullRequestReviewContributions
        restrictedContributionsCount
        contributionCalendar{ totalContributions weeks{ contributionDays{ date contributionCount weekday } } }
    } } }"""
    return graphql(q, {"login": USER})["user"]["contributionsCollection"]


def fetch_languages() -> tuple[list[tuple[str, float, str]], int]:
    q = """query($login:String!, $cursor:String){ user(login:$login){
        repositories(first:100, after:$cursor, ownerAffiliations:OWNER, isFork:false){
          pageInfo{ hasNextPage endCursor }
          nodes{ pushedAt languages(first:15, orderBy:{field:SIZE, direction:DESC}){
            edges{ size node{ name color } } } }
    } } }"""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    shares: dict[str, float] = {}
    colors: dict[str, str] = {}
    repos, cursor = 0, None
    while True:
        page = graphql(q, {"login": USER, "cursor": cursor})["user"]["repositories"]
        for repo in page["nodes"]:
            if not repo["pushedAt"] or repo["pushedAt"] < cutoff:
                continue
            edges = [e for e in repo["languages"]["edges"] if e["node"]["name"] not in SKIP_LANGUAGES]
            total = sum(e["size"] for e in edges)
            if not total:
                continue
            repos += 1
            # Each repository gets one equal vote, split by its languages, so a
            # single large or vendored codebase can't dominate the chart.
            for e in edges:
                name = e["node"]["name"]
                shares[name] = shares.get(name, 0) + e["size"] / total
                colors[name] = LANGUAGE_COLORS.get(name) or e["node"]["color"] or "#8b949e"
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    whole = sum(shares.values()) or 1
    ranked = sorted(shares.items(), key=lambda kv: -kv[1])
    top = [(LANGUAGE_NAMES.get(n, n), v * 100 / whole, colors[n]) for n, v in ranked[:6]]
    rest = sum(v for _, v in ranked[6:]) * 100 / whole
    if rest >= 0.5:
        top.append(("Other", rest, "#8b949e"))
    return top, repos


def streaks(days: list[dict]) -> tuple[int, int]:
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        longest = max(longest, run)
    current = 0
    # Today may not have activity yet; the streak still counts from yesterday.
    tail = days[:-1] if days and not days[-1]["contributionCount"] else days
    for d in reversed(tail):
        if not d["contributionCount"]:
            break
        current += 1
    return current, longest


# ── SVG helpers ───────────────────────────────────────────────────────────


def card(t: dict, height: int, body: str, title: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(title)}">
<title>{escape(title)}</title>
<style>
  text {{ font-family: {FONT}; }}
  .fade {{ opacity: 0; animation: fade .6s ease-out forwards; }}
  .draw {{ stroke-dasharray: 2400; stroke-dashoffset: 2400; animation: draw 2.2s cubic-bezier(.16,1,.3,1) forwards; }}
  .grow {{ transform-origin: left; transform: scaleX(0); animation: grow 1.4s cubic-bezier(.16,1,.3,1) forwards; }}
  @keyframes fade {{ to {{ opacity: 1; }} }}
  @keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
  @keyframes grow {{ to {{ transform: scaleX(1); }} }}
  @media (prefers-reduced-motion: reduce) {{ .fade, .draw, .grow {{ animation: none; opacity: 1; stroke-dashoffset: 0; transform: none; }} }}
</style>
<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14" fill="{t['bg']}" stroke="{t['border']}"/>
{body}
</svg>"""


def smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom spline through the points, as cubic Béziers."""
    d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d


def quartiles(counts: list[int]) -> list[int]:
    """Thresholds splitting active days into four even bands, like GitHub's graph."""
    active = sorted(c for c in counts if c)
    if not active:
        return [1, 1, 1]
    return [active[min(len(active) - 1, int(len(active) * q))] for q in (0.25, 0.5, 0.75)]


def level(count: int, cuts: list[int]) -> int:
    if count == 0:
        return 0
    return 1 if count < cuts[0] else 2 if count < cuts[1] else 3 if count < cuts[2] else 4


# ── Graphs ────────────────────────────────────────────────────────────────


def activity_svg(t: dict, cal: dict) -> str:
    weeks = cal["contributionCalendar"]["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]
    total = cal["contributionCalendar"]["totalContributions"]
    current, longest = streaks(days)
    by_weekday = [0] * 7
    for d in days:
        by_weekday[d["weekday"]] += d["contributionCount"]
    busiest = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][
        max(range(7), key=lambda i: by_weekday[i])
    ]
    weekly = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]

    x0, cell, gap = 54, 12, 3
    step = cell + gap
    parts = []

    # Headline and stats
    parts.append(
        f'<text x="32" y="46" font-size="22" font-weight="600" fill="{t["ink"]}">'
        f'{total:,} contributions <tspan fill="{t["muted"]}" font-weight="400">in the last year</tspan></text>'
    )
    stats = [
        ("Current streak", f"{current} day{'s' if current != 1 else ''}"),
        ("Longest streak", f"{longest} days"),
        ("Pull requests", f"{cal['totalPullRequestContributions'] + cal['totalPullRequestReviewContributions']}"),
        ("Most active", busiest),
    ]
    for i, (label, value) in enumerate(stats):
        sx = 32 + i * 150
        if i:
            parts.append(f'<line x1="{sx - 18}" y1="74" x2="{sx - 18}" y2="104" stroke="{t["border"]}"/>')
        parts.append(
            f'<text x="{sx}" y="84" font-size="11" fill="{t["muted"]}">{escape(label)}</text>'
            f'<text x="{sx}" y="104" font-size="16" font-weight="600" fill="{t["ink"]}">{escape(value)}</text>'
        )

    # Weekly trend (area + line)
    top, h = 136, 64
    peak_week = max(weekly) or 1
    pts = [(x0 + i * step + cell / 2, top + h - (v / peak_week) * h) for i, v in enumerate(weekly)]
    line = smooth_path(pts)
    area = f"{line} L{pts[-1][0]:.1f},{top + h} L{pts[0][0]:.1f},{top + h} Z"
    parts.append(
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{t["accent"]}" stop-opacity=".28"/>'
        f'<stop offset="1" stop-color="{t["accent"]}" stop-opacity="0"/></linearGradient></defs>'
        f'<line x1="{x0}" y1="{top + h}" x2="{x0 + len(weeks) * step - gap}" y2="{top + h}" stroke="{t["grid"]}"/>'
        f'<path class="fade" d="{area}" fill="url(#g)" style="animation-delay:.6s"/>'
        f'<path class="draw" d="{line}" fill="none" stroke="{t["accent"]}" stroke-width="2" stroke-linecap="round"/>'
        f'<text x="32" y="{top + 12}" font-size="10" fill="{t["muted"]}">Weekly</text>'
    )

    # Heatmap
    hy = top + h + 34
    cuts = quartiles([d["contributionCount"] for d in days])
    # Week index where each month starts; a month shorter than three weeks at the
    # left edge gives way to the next one so labels never collide.
    starts: list[tuple[int, dt.date]] = []
    for wi, w in enumerate(weeks):
        first = dt.date.fromisoformat(w["contributionDays"][0]["date"])
        if not starts or first.month != starts[-1][1].month:
            starts.append((wi, first))
    labels = {wi: d for i, (wi, d) in enumerate(starts) if i + 1 >= len(starts) or starts[i + 1][0] - wi >= 3}
    for wi, w in enumerate(weeks):
        x = x0 + wi * step
        if wi in labels and wi < len(weeks) - 2:
            parts.append(f'<text x="{x}" y="{hy - 8}" font-size="10" fill="{t["muted"]}">{labels[wi].strftime("%b")}</text>')
        for d in w["contributionDays"]:
            y = hy + d["weekday"] * step
            fill = t["levels"][level(d["contributionCount"], cuts)]
            parts.append(
                f'<rect class="fade" style="animation-delay:{wi * 0.012:.3f}s" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="{fill}">'
                f'<title>{d["contributionCount"]} on {d["date"]}</title></rect>'
            )
    for wd, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        parts.append(f'<text x="32" y="{hy + wd * step + 10}" font-size="10" fill="{t["muted"]}">{name}</text>')

    # Legend + footnote
    ly = hy + 7 * step + 18
    lx = WIDTH - 32 - 5 * step - 34
    parts.append(f'<text x="{lx - 8}" y="{ly + 10}" font-size="10" text-anchor="end" fill="{t["muted"]}">Less</text>')
    for i, c in enumerate(t["levels"]):
        parts.append(f'<rect x="{lx + i * step}" y="{ly}" width="{cell}" height="{cell}" rx="3" fill="{c}"/>')
    parts.append(f'<text x="{lx + 5 * step + 4}" y="{ly + 10}" font-size="10" fill="{t["muted"]}">More</text>')
    private = cal["restrictedContributionsCount"]
    note = f"Includes {private:,} contributions to private repositories · " if private else ""
    parts.append(
        f'<text x="32" y="{ly + 10}" font-size="10" fill="{t["muted"]}">{note}Updated {dt.date.today():%d %b %Y}</text>'
    )
    return card(t, ly + 34, "\n".join(parts), f"{USER}: {total} contributions in the last year")


def languages_svg(t: dict, langs: list[tuple[str, float, str]], repos: int) -> str:
    parts = [
        f'<text x="32" y="42" font-size="17" font-weight="600" fill="{t["ink"]}">Languages</text>',
        f'<text x="{WIDTH - 32}" y="42" font-size="11" text-anchor="end" fill="{t["muted"]}">'
        f"{repos} repositories active in the last two years</text>",
    ]
    bx, by, bw, bh = 32, 62, WIDTH - 64, 10
    parts.append(f'<clipPath id="bar"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5"/></clipPath>')
    parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5" fill="{t["grid"]}"/>')
    x = bx
    segs = []
    for name, pct, color in langs:
        w = bw * pct / 100
        segs.append(f'<rect x="{x:.1f}" y="{by}" width="{max(w - 2, 0):.1f}" height="{bh}" fill="{color}"/>')
        x += w
    parts.append(f'<g clip-path="url(#bar)"><g class="grow">{"".join(segs)}</g></g>')
    cols = 4
    for i, (name, pct, color) in enumerate(langs):
        cx = 32 + (i % cols) * ((WIDTH - 64) / cols)
        cy = 104 + (i // cols) * 28
        parts.append(
            f'<g class="fade" style="animation-delay:{0.3 + i * 0.08:.2f}s">'
            f'<circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{color}"/>'
            f'<text x="{cx + 18}" y="{cy}" font-size="13" fill="{t["ink"]}">{escape(name)}'
            f' <tspan fill="{t["muted"]}">{pct:.1f}%</tspan></text></g>'
        )
    rows = (len(langs) + cols - 1) // cols
    fy = 104 + rows * 28 + 8
    parts.append(
        f'<text x="32" y="{fy}" font-size="10" fill="{t["muted"]}">Each repository counts equally; '
        "notebooks, markup and vendored code are excluded.</text>"
    )
    return card(t, fy + 24, "\n".join(parts), f"{USER}: languages")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cal = fetch_calendar()
    langs, repos = fetch_languages()
    for name, theme in THEMES.items():
        (OUT / f"activity-{name}.svg").write_text(activity_svg(theme, cal), encoding="utf-8")
        (OUT / f"languages-{name}.svg").write_text(languages_svg(theme, langs, repos), encoding="utf-8")
    print(f"total={cal['contributionCalendar']['totalContributions']} repos={repos} langs={[(n, round(p, 1)) for n, p, _ in langs]}")


if __name__ == "__main__":
    main()
