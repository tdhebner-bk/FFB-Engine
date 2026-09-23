#!/usr/bin/env python3
"""
rank_chart.py — week-over-week rank chart with team logos, for the report draft.

    python3 rank_chart.py              # chart from every snapshot in data/history
    python3 rank_chart.py --metric consensus_rank

A bump chart: one row per rank (1 at the top), one column per report week, one
line per team, with that team's Sleeper logo as the marker at every week and a
"rank · name" label at the right edge. Lines stay neutral because twelve
colors can't be told apart; the logos and labels carry identity. A dashed rule
marks the playoff line.

Needs matplotlib + Pillow (the rest of the project is stdlib-only, so
weekly_report.py skips the chart with a warning when they're missing).
Logos come from Sleeper (team logo if set, else the manager's avatar) and are
cached in data/logos/; teams with neither get an initials badge.
"""

import argparse, glob, io, json, os, re
from urllib.request import urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = {"bozo_rank": "Bozo Ranking", "consensus_rank": "Consensus Ranking",
           "power_rank": "Power Ranking", "fp_rank": "FantasyPros Ranking", "standing": "Standings"}

# light-surface tokens (the draft is a white Google Doc)
SURFACE, INK, INK2, MUTED, GRID, LINE = "#ffffff", "#0b0b0b", "#52514e", "#8a8984", "#ecebe7", "#b9b8b2"


def logo_urls(user):
    meta = user.get("metadata") or {}
    urls = []
    if meta.get("avatar"): urls.append(meta["avatar"])
    if user.get("avatar"): urls.append(f"https://sleepercdn.com/avatars/thumbs/{user['avatar']}")
    return urls

def fetch_logos(users, uid2name, cache_dir):
    """{manager name: path to a circular 256px PNG}, downloading only what's not cached."""
    from PIL import Image, ImageDraw
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for u in users:
        name = uid2name.get(u["user_id"])
        if not name: continue
        urls = logo_urls(u)
        key = re.sub(r"\W", "", (urls[0].rsplit("/", 1)[-1] if urls else "none"))
        path = os.path.join(cache_dir, f"{u['user_id']}_{key}.png")
        if not os.path.exists(path):
            img = None
            for url in urls:
                try:
                    with urlopen(url, timeout=20) as r:
                        img = Image.open(io.BytesIO(r.read())).convert("RGBA"); break
                except Exception:
                    continue
            if img is None: continue                       # initials badge drawn at render time
            s = min(img.size)                              # center-crop square, then circle-mask
            img = img.crop(((img.width-s)//2, (img.height-s)//2, (img.width+s)//2, (img.height+s)//2)).resize((256, 256), Image.LANCZOS)
            mask = Image.new("L", (256, 256), 0); ImageDraw.Draw(mask).ellipse((0, 0, 255, 255), fill=255)
            circ = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); circ.paste(img, (0, 0), mask)
            circ.save(path)
        out[name] = path
    return out


def load_snaps(data_dir):
    snaps = [json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(data_dir, "history", "week_*.json"))]
    return sorted(snaps, key=lambda s: s["report_week"])

def rank_value(v):
    if v is None: return None
    return int(str(v).lstrip("t"))


def render(snaps, out_png, logos=None, metric="bozo_rank", playoff_teams=6):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from PIL import Image

    logos = logos or {}
    weeks = [s["report_week"] for s in snaps]
    names = list(snaps[-1]["teams"])
    n = len(names)
    series = {nm: [rank_value(s["teams"].get(nm, {}).get(metric)) for s in snaps] for nm in names}

    fig_w = min(10.0, max(6.5, 2.6 + 1.1 * len(weeks)))      # capped so a full season still reads at page width
    fig, ax = plt.subplots(figsize=(fig_w, 3.9), dpi=200)       # short enough to sit under the table on page 1
    fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)
    xs = list(range(len(weeks)))

    ax.set_ylim(n + 0.6, 0.4)
    ax.set_xlim(-0.45, len(weeks) - 1 + 0.45)
    for r in range(1, n + 1):
        ax.axhline(r, color=GRID, lw=0.8, zorder=0)
    ax.axhline(playoff_teams + 0.5, color=MUTED, lw=0.9, ls=(0, (3, 3)), zorder=0)

    for nm in names:
        pts = [(x, y) for x, y in zip(xs, series[nm]) if y is not None]
        if len(pts) > 1:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=LINE, lw=2, solid_capstyle="round",
                    solid_joinstyle="round", zorder=1)

    fig.subplots_adjust(left=0.15, right=0.83, top=0.90, bottom=0.08)
    # logo markers sized to ~80% of a row, with a white ring so crossings stay legible
    row_pt = fig.get_figheight() * (0.90 - 0.08) * 72 / (n + 0.2)
    logo_pt = row_pt * 0.8
    cache = {}
    def marker(nm):
        if nm not in cache:
            cache[nm] = Image.open(logos[nm]) if nm in logos and os.path.exists(logos[nm]) else None
        return cache[nm]
    for nm in names:
        img = marker(nm)
        for x, y in zip(xs, series[nm]):
            if y is None: continue
            ax.scatter([x], [y], s=(logo_pt + 3) ** 2, color=SURFACE, zorder=2, linewidths=0)    # surface ring
            if img is not None:
                ax.add_artist(AnnotationBbox(OffsetImage(img, zoom=logo_pt / img.width), (x, y),
                                             frameon=False, zorder=3))
            else:                                                                           # initials badge
                ax.scatter([x], [y], s=logo_pt ** 2, color=INK2, zorder=3, linewidths=0)
                initials = "".join(w[0] for w in nm.split())[:2].upper()
                ax.text(x, y, initials, color="white", fontsize=logo_pt * 0.35, fontweight="bold",
                        ha="center", va="center", zorder=4)

    # direct labels: name + rank at the left edge (first week), rank + name + move at the right edge
    last_i = len(weeks) - 1
    renderer = fig.canvas.get_renderer()
    for nm in names:
        first = next(((x, y) for x, y in zip(xs, series[nm]) if y is not None), None)
        if first and first[0] < last_i:
            ax.annotate(f"{nm}  {first[1]}", first, xytext=(-(logo_pt / 2 + 6), 0), textcoords="offset points",
                        color=INK2, fontsize=8, va="center", ha="right", zorder=4)
    import matplotlib.transforms as mtransforms
    ax.text(1.0, playoff_teams + 0.5, " playoff line", color=MUTED, fontsize=7, va="center", ha="left",
            transform=mtransforms.blended_transform_factory(ax.transAxes, ax.transData))
    for nm in names:
        y = series[nm][last_i]
        if y is None: continue
        lab = ax.annotate(f"{y}  {nm}", (last_i, y), xytext=(logo_pt / 2 + 6, 0), textcoords="offset points",
                          color=INK, fontsize=8.5, va="center", ha="left", zorder=4)
        prev = series[nm][last_i - 1] if last_i > 0 else None
        if prev is not None and prev != y:
            w_pt = lab.get_window_extent(renderer).width * 72 / fig.dpi
            ax.annotate(f"{'▲' if prev > y else '▼'}{abs(prev - y)}", (last_i, y),
                        xytext=(logo_pt / 2 + 6 + w_pt + 5, 0), textcoords="offset points",
                        color=("#006300" if prev > y else "#b42d2d"), fontsize=7.5, va="center", ha="left", zorder=4)

    ax.set_xticks(xs, ["Preseason" if w == 0 else f"Week {w}" for w in weeks], color=INK2, fontsize=8.5)
    ax.set_yticks([])
    ax.tick_params(length=0)
    for s in ax.spines.values(): s.set_visible(False)
    ax.set_title(f"{METRICS.get(metric, metric)}, week over week", loc="left", color=INK, fontsize=11,
                 fontweight="bold", pad=10)
    fig.savefig(out_png, facecolor=SURFACE)
    plt.close(fig)
    return out_png


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="bozo_rank", choices=list(METRICS))
    a = ap.parse_args()
    import ffb_engine as E
    cfg = json.load(open(os.path.join(HERE, "league_config.json"), encoding="utf-8"))
    B = E.load_bundle()
    users = E.get_json(E.API + f"league/{B['CFG']['leagueId']}/users")
    uid2name = {uid: m["name"] for uid, m in cfg["managers"].items()}
    logos = fetch_logos(users, uid2name, os.path.join(HERE, "data", "logos"))
    snaps = load_snaps(os.path.join(HERE, "data"))
    print(render(snaps, os.path.join(HERE, "reports", f"week_{snaps[-1]['report_week']}_chart.png"), logos, a.metric,
                 B["CFG"]["playoffTeams"]))
