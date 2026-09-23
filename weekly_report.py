#!/usr/bin/env python3
"""
weekly_report.py — one command that assembles everything the weekly BKN FFB
report needs, so the only manual part left is writing the blurbs.

    python3 weekly_report.py                  # report for the week that just finished
    python3 weekly_report.py --week 2         # a specific report week (0 = preseason)
    python3 weekly_report.py --refresh-bundle # re-run build_board_data.py first

INPUTS
    league_config.json        manager names, Google Form label aliases, poll sheets
    Power poll ballots        from week 3 on, the Apps Script ballot in poll/ (its
                              private export URL is in secrets.local.json); weeks 0-2
                              came from Google Form response sheets (public CSV
                              export). Any data/polls/week_N.csv is also picked up.
    data/fantasypros/week_N.json
                              FantasyPros League Analyzer power rankings. That page
                              needs your FantasyPros login, so Claude captures it in
                              Chrome (see WEEKLY_RUNBOOK.md); skipped if absent
    api.sleeper.app           scores, lineups, standings (public, no auth)
    ffb_engine.py             power rankings, next-week spreads/win probs, Monte
                              Carlo playoff odds (with completed weeks locked in)
    data/history/week_{N-1}.json
                              last week's snapshot, for movement + grading last
                              week's predictions

OUTPUTS
    data/history/week_N.json  this week's snapshot (feeds next week + the dashboard)
    reports/week_N_draft.html the draft for previewing locally: the rankings table,
                              the logo rank chart, every team's header line, and a
                              shaded "notes" box of stats per team. Writeups are
                              left blank on purpose.
    reports/week_N_gdoc.html  the same draft for uploading to Google Docs, with a
                              "[[RANK CHART]]" line where the chart gets pasted
    reports/week_N_chart.png  week-over-week rank chart with team logos (rank_chart.py)
    reports/dashboard.html    season-to-date charts (rank trends, playoff odds, poll
                              spread), rebuilt from every snapshot on disk

Power rankings and projections always use CURRENT rosters and the current
bundle.json, so re-running an old week gives today's model numbers for it.
"""

import argparse, csv, datetime, html, io, json, math, os, statistics, subprocess, sys
from urllib.request import urlopen

import ffb_engine as E

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPORTS = os.path.join(HERE, "reports")


# =============================================================================
# SECTION 1 — SMALL HELPERS
# =============================================================================
def ordinal(n):
    if 10 <= n % 100 <= 20: suf = "th"
    else: suf = {1:"st", 2:"nd", 3:"rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def times(n):
    return {1:"", 2:" twice", 3:" thrice"}.get(n, f" {n} times")

def competition_rank(values, reverse):
    """{name: value} -> {name: (rank, tied)} using 1-2-2-4 style ranking.
    reverse=True means bigger value is better."""
    order = sorted(values, key=lambda k: values[k], reverse=reverse)
    out = {}
    for i, k in enumerate(order):
        prev = order[i-1] if i else None
        if prev is not None and values[prev] == values[k]:
            out[k] = (out[prev][0], True); out[prev] = (out[prev][0], True)
        else:
            out[k] = (i+1, False)
    return out

def fmt_rank(rank, tied):
    return f"t{rank}" if tied else str(rank)

def fmt_spread(s):
    return "PK" if s == 0 else f"{s:+.1f}"

def load_json(p, default=None):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


# =============================================================================
# SECTION 2 — POWER POLL (Google Form ballots -> "Bozo Ranking")
# Each ballot ranks all 12 teams 1st..12th. Borda count: 1st = 12 pts ... 12th =
# 1 pt, summed across ballots — identical to the old spreadsheet math. Borda
# ties are broken by 1st-place votes, then by the better worst-case ranking.
# =============================================================================
def week_of(ts, week1_opens):
    """Report week a ballot belongs to, from its timestamp: anything before
    week 1's poll opens is preseason (0), then one bucket per 7 days."""
    d = datetime.datetime.strptime(ts.strip(), "%m/%d/%Y %H:%M:%S").date()
    return 0 if d < week1_opens else (d - week1_opens).days // 7 + 1

def read_poll_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows: return []
    hdr = rows[0]
    cols = {i: h.split("[",1)[1].rstrip("]").strip() for i, h in enumerate(hdr) if "[" in h}
    week_col = next((i for i, h in enumerate(hdr) if h.strip().lower() == "week"), None)   # the Apps Script ballot tags each row
    out = []
    for r in rows[1:]:
        # only real ballot rows — response sheets often have tally formulas below them
        try: datetime.datetime.strptime(r[0].strip(), "%m/%d/%Y %H:%M:%S")
        except (ValueError, IndexError): continue
        ranks = {}
        for i, lab in cols.items():
            digits = "".join(ch for ch in (r[i] if i < len(r) else "") if ch.isdigit())
            if digits: ranks[lab] = int(digits)
        b = {"ts": r[0], "ranks": ranks}
        if week_col is not None and week_col < len(r) and r[week_col].strip().isdigit():
            b["week"] = int(r[week_col])
        out.append(b)
    return out

def poll_ballots(cfg, week, label2name):
    """Ballots for one report week from every configured source. Sources are Google
    Form response sheets ({"sheet": id}, read through their public CSV export) or the
    Apps Script ballot ({"secret": name}: its private export URL lives in
    secrets.local.json). Returns (ballots, sources, missing voters or None)."""
    w1 = datetime.date.fromisoformat(cfg["week1_poll_opens"])
    secrets = load_json(os.path.join(HERE, "secrets.local.json"), {})
    ballots, sources, missing = [], [], None
    for src in cfg.get("poll_sources", []):
        if week < src.get("from_week", 0): continue
        label = src.get("note", src.get("sheet") or src.get("secret"))
        if "secret" in src:
            url = secrets.get(src["secret"])
            if not url:
                print(f"  ! {label}: no '{src['secret']}' in secrets.local.json yet (see poll/SETUP.md)"); continue
        else:
            url = f"https://docs.google.com/spreadsheets/d/{src['sheet']}/export?format=csv"
        try:
            with urlopen(url, timeout=30) as r:
                text = r.read().decode("utf-8")
        except Exception as e:
            print(f"  ! poll source {label} unreadable: {e}"); continue
        if text.lstrip().startswith("<") or text.strip() == "forbidden":
            print(f"  ! poll source {label} refused the request — check it's link-viewable / the export key"); continue
        def bucket(b):
            if "week" in src: return src["week"]
            return b["week"] if "week" in b else week_of(b["ts"], w1)
        mine = [b for b in read_poll_csv(text) if bucket(b) == week]
        if mine: ballots += mine; sources.append(label)
        if "secret" in src:                                   # the ballot app also knows who hasn't voted
            try:
                status_url = url.replace("export=csv", "status=json") + f"&week={week}"
                with urlopen(status_url, timeout=30) as r:
                    missing = json.load(r).get("missing")
            except Exception as e:
                print(f"  ! couldn't read turnout from {label}: {e}")
    local = os.path.join(DATA, "polls", f"week_{week}.csv")
    if os.path.exists(local):
        ballots += read_poll_csv(open(local, encoding="utf-8").read()); sources.append(os.path.relpath(local, HERE))
    n_teams = len(set(label2name.values()))
    good = []
    for b in ballots:
        named = {label2name[l]: v for l, v in b["ranks"].items() if l in label2name}
        if sorted(named.values()) != list(range(1, n_teams+1)):
            print(f"  ! skipping incomplete/duplicate ballot from {b['ts']}"); continue
        good.append({"ts": b["ts"], "ranks": named})
    return good, sources, missing

def tally(ballots, names):
    n_teams = len(names)
    t = {}
    for n in names:
        rs = [b["ranks"][n] for b in ballots]
        t[n] = {"points": sum(n_teams+1-r for r in rs), "first": rs.count(1),
                "best": min(rs) if rs else None, "best_ct": rs.count(min(rs)) if rs else 0,
                "worst": max(rs) if rs else None, "worst_ct": rs.count(max(rs)) if rs else 0,
                "mean": statistics.mean(rs) if rs else None,
                "sd": statistics.pstdev(rs) if len(rs) > 1 else 0.0, "ballots": rs}
    key = {n: (t[n]["points"], t[n]["first"], -(t[n]["worst"] or 0)) for n in names}
    for n, (rk, tied) in competition_rank(key, reverse=True).items():
        t[n]["rank"], t[n]["tied"] = rk, tied
    return t


# =============================================================================
# SECTION 3 — SLEEPER RESULTS (what actually happened)
# Per-team box score for a completed week: score, opponent, top starter, and
# "points left on the bench" = best possible lineup (same optimizer the engine
# uses, fed actual points instead of projections) minus what was started.
# =============================================================================
def week_box(lid, week, B, idx, rid2name):
    rows = E.get_json(E.API + f"league/{lid}/matchups/{week}")
    by_mid = {}
    for e in rows: by_mid.setdefault(e.get("matchup_id"), []).append(e["roster_id"])
    box = {}
    for e in rows:
        rid = e["roster_id"]; pp = e.get("players_points") or {}
        pool = []
        for pid in e.get("players") or []:
            p = E.resolve_player(pid, B, idx)
            if p: p = dict(p, base=pp.get(pid, 0.0)); pool.append(p)
        _, optimal = E.optimize(pool, week, B["SCHED"], ignore_bye=True)
        starters = [(pid, sp) for pid, sp in zip(e.get("starters") or [], e.get("starters_points") or []) if pid != "0"]
        def pname(pid):
            p = E.resolve_player(pid, B, idx); return p["n"] if p else pid
        top = max(starters, key=lambda x: x[1]) if starters else None
        dud = min(starters, key=lambda x: x[1]) if starters else None
        bench = [(pid, pp.get(pid, 0.0)) for pid in (e.get("players") or []) if pid not in dict(starters)]
        best_bench = max(bench, key=lambda x: x[1]) if bench else None
        pair = by_mid.get(e.get("matchup_id"), [])
        opp = next((r for r in pair if r != rid), None)
        box[rid2name[rid]] = {
            "points": round(e.get("points") or 0.0, 2), "opp": rid2name.get(opp),
            "optimal": round(optimal, 2), "bench_left": round(max(0.0, optimal - (e.get("points") or 0.0)), 2),
            "top": top and {"name": pname(top[0]), "pts": top[1]},
            "dud": dud and {"name": pname(dud[0]), "pts": dud[1]},
            "best_bench": best_bench and {"name": pname(best_bench[0]), "pts": best_bench[1]},
        }
    for n, b in box.items():
        b["opp_points"] = box[b["opp"]]["points"] if b["opp"] else None
        b["won"] = b["opp_points"] is not None and b["points"] > b["opp_points"]
        b["week_rank"] = 1 + sum(1 for o in box.values() if o["points"] > b["points"])
        b["allplay_w"] = sum(1 for o in box.values() if o["points"] < b["points"])
    return box

def season_table(boxes, names):
    """Standings through the given weeks: W-L, PF, PA, all-play, luck.
    Ranked the way Sleeper ranks: wins, then points for."""
    st = {n: {"w":0, "l":0, "pf":0.0, "pa":0.0, "ap_w":0, "ap_g":0} for n in names}
    for box in boxes:
        for n, b in box.items():
            s = st[n]; s["pf"] += b["points"]; s["pa"] += b["opp_points"] or 0
            s["w" if b["won"] else "l"] += 1
            s["ap_w"] += b["allplay_w"]; s["ap_g"] += len(box) - 1
    for n, s in st.items():
        games = s["w"] + s["l"]
        s["allplay_pct"] = s["ap_w"] / s["ap_g"] if s["ap_g"] else None
        s["luck"] = s["w"] - (s["allplay_pct"] or 0) * games      # wins above all-play expectation
    order = sorted(names, key=lambda n: (-st[n]["w"], -st[n]["pf"]))
    for i, n in enumerate(order, 1): st[n]["rank"] = i
    pf_rank = competition_rank({n: st[n]["pf"] for n in names}, reverse=True)
    for n in names: st[n]["pf_rank"] = pf_rank[n][0]
    return st


# =============================================================================
# SECTION 4 — FANTASYPROS LEAGUE POWER RANKINGS (captured via Chrome)
# =============================================================================
def load_fantasypros(week, fp_label2name):
    d = load_json(os.path.join(DATA, "fantasypros", f"week_{week}.json"))
    if not d: return None, None
    def conv(view):
        out = {}
        for row in (view or {}).get("teams", []):
            n = fp_label2name.get(row["team"].strip())
            if n: out[n] = {"rank": row["rank"], "score": row["score"]}
            else: print(f"  ! FantasyPros team '{row['team']}' not matched to a manager")
        return out
    return conv(d.get("week_view")), conv(d.get("ros_view"))


# =============================================================================
# SECTION 5 — BUILD THE WEEK
# =============================================================================
def build(week, sims, cfg):
    B = E.load_bundle(); lcfg = B["CFG"]; lid = lcfg["leagueId"]; idx = E.build_index(B)
    teams, matchups = E.load_league(B)
    uid2name = {uid: m["name"] for uid, m in cfg["managers"].items()}
    rid2name = {rid: uid2name[t["owner"]] for rid, t in teams.items()}
    name2rid = {v: k for k, v in rid2name.items()}
    names = list(name2rid)
    label2name = {lab: m["name"] for m in cfg["managers"].values() for lab in m["poll_labels"] + [m["name"]]}
    users = E.get_json(E.API + f"league/{lid}/users")
    fp_label2name = {}
    for u in users:
        n = uid2name.get(u["user_id"])
        if not n: continue
        for lab in ((u.get("metadata") or {}).get("team_name"), u.get("display_name")):
            if lab: fp_label2name[lab.strip()] = n

    print(f"Report week {week}")
    # --- poll ---
    ballots, poll_srcs, no_ballot = poll_ballots(cfg, week, label2name)
    print(f"  poll: {len(ballots)} ballots from {', '.join(poll_srcs) or 'nowhere'}"
          + (f"; no ballot from {', '.join(no_ballot)}" if no_ballot else ""))
    poll = tally(ballots, names) if ballots else None

    # --- results through this week ---
    boxes = [week_box(lid, w, B, idx, rid2name) for w in range(1, week+1)]
    table = season_table(boxes, names) if week >= 1 else None
    last = boxes[-1] if boxes else None
    results = {w: {name2rid[n]: b["points"] for n, b in box.items()} for w, box in enumerate(boxes, 1)}

    # --- model: power, next week, playoff odds ---
    power = {rid2name[r]: t["power"] for r, t in teams.items()}
    power_rank = competition_rank(power, reverse=True)
    nxt = week + 1
    proj = {}
    if nxt <= lcfg["regWeeks"]:
        for a, b in matchups.get(nxt, []):
            pa = E.optimize(teams[a]["players"], nxt, B["SCHED"])[1]
            pb = E.optimize(teams[b]["players"], nxt, B["SCHED"])[1]
            for x, y, px, py in ((a, b, pa, pb), (b, a, pb, pa)):
                proj[rid2name[x]] = {"opp": rid2name[y], "proj": round(px, 1), "opp_proj": round(py, 1),
                                     "spread": round((py - px) * 2) / 2, "win_prob": E.win_prob(px - py)}
    print(f"  Monte Carlo: {sims} seasons, weeks 1-{week} locked to real results")
    E.random.seed(f"bkn-{lcfg['season']}-week-{week}")     # same inputs -> same odds on every re-run
    po, ti = E.monte_carlo(teams, matchups, B["SCHED"], lcfg, sims, results)
    ew = E.expected_wins(teams, matchups, B["SCHED"], results)

    # --- FantasyPros ---
    fp, fp_ros = load_fantasypros(week, fp_label2name)
    print("  FantasyPros: " + (f"{len(fp)} teams" if fp else "MISSING (data/fantasypros/week_%d.json)" % week))

    # --- consensus: mean of every rank we have (poll, power, standings, FantasyPros) ---
    comp = {}
    for n in names:
        parts = [poll and poll[n]["rank"], power_rank[n][0], table and table[n]["rank"], fp and fp.get(n, {}).get("rank")]
        parts = [p for p in parts if p]
        comp[n] = sum(parts) / len(parts)
    cons = competition_rank(comp, reverse=False)

    snap = {"report_week": week, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "ballots": len(ballots), "no_ballot": no_ballot, "teams": {}, "next_week": nxt, "predictions": []}
    for n in names:
        rid = name2rid[n]
        snap["teams"][n] = {
            "bozo_rank": poll and poll[n]["rank"], "bozo_points": poll and poll[n]["points"],
            "first_votes": poll and poll[n]["first"], "poll_best": poll and poll[n]["best"],
            "poll_best_ct": poll and poll[n]["best_ct"], "poll_worst": poll and poll[n]["worst"],
            "poll_worst_ct": poll and poll[n]["worst_ct"], "poll_sd": poll and round(poll[n]["sd"], 2),
            "poll_ballots": poll and poll[n]["ballots"],
            "power_rank": power_rank[n][0], "power_pts": round(power[n], 1),
            "standing": table and table[n]["rank"],
            "record": table and f"{table[n]['w']}-{table[n]['l']}",
            "pf": table and round(table[n]["pf"], 2), "pf_rank": table and table[n]["pf_rank"],
            "pa": table and round(table[n]["pa"], 2),
            "allplay_pct": table and table[n]["allplay_pct"], "luck": table and round(table[n]["luck"], 2),
            "fp_rank": fp and fp.get(n, {}).get("rank"), "fp_score": fp and fp.get(n, {}).get("score"),
            "fp_ros_rank": fp_ros and fp_ros.get(n, {}).get("rank"), "fp_ros_score": fp_ros and fp_ros.get(n, {}).get("score"),
            "consensus_rank": fmt_rank(*cons[n]), "consensus_avg": round(comp[n], 2),
            "playoff_odds": round(po[rid], 3), "title_odds": round(ti[rid], 3),
            "proj_wins": round(ew[rid], 1),
            "last_week": last and last[n],
        }
        if n in proj:
            p = proj[n]
            snap["predictions"].append({"team": n, "opp": p["opp"], "spread": p["spread"],
                                        "win_prob": round(p["win_prob"], 3), "proj": p["proj"], "opp_proj": p["opp_proj"]})
    return snap


# =============================================================================
# SECTION 6 — WRITEUP FODDER (the "notes" boxes)
# Pure observations from the numbers; the jokes are yours.
# =============================================================================
def grade_predictions(prev, snap):
    """Last week's published predictions vs what happened."""
    if not prev or not snap["teams"][next(iter(snap["teams"]))].get("last_week"): return None
    graded, seen = [], set()
    for p in prev.get("predictions", []):
        lw = snap["teams"][p["team"]]["last_week"]
        if not lw or lw["opp"] != p["opp"] or frozenset((p["team"], p["opp"])) in seen: continue
        seen.add(frozenset((p["team"], p["opp"])))
        fav, dog = (p["team"], p["opp"]) if p["spread"] <= 0 else (p["opp"], p["team"])
        fp_ = next(x for x in prev["predictions"] if x["team"] == fav)
        flw = snap["teams"][fav]["last_week"]
        margin = flw["points"] - flw["opp_points"]
        graded.append({"fav": fav, "dog": dog, "spread": fp_["spread"], "fav_wp": fp_["win_prob"],
                       "fav_won": margin > 0, "margin": round(margin, 2),
                       "miss": round(abs(margin - (-fp_["spread"])), 2)})
    return graded

def team_notes(n, snap, prev, graded):
    t = snap["teams"][n]; pt = (prev or {}).get("teams", {}).get(n, {})
    notes = []
    def delta(cur, old, lower_better=True):
        if cur is None or old is None: return ""
        d = (old - cur) if lower_better else (cur - old)
        return " (—)" if d == 0 else (f" (▲{abs(d)})" if d > 0 else f" (▼{abs(d)})")
    lw = t.get("last_week")
    if lw:
        res = "W" if lw["won"] else "L"
        place = "the highest" if lw["week_rank"] == 1 else f"{ordinal(lw['week_rank'])}-highest"
        notes.append(f"<b>Last week:</b> {res} {lw['points']:.2f}–{lw['opp_points']:.2f} vs {lw['opp']} "
                     f"({place} score of the week; would've gone {lw['allplay_w']}-{len(snap['teams'])-1-lw['allplay_w']} vs the whole league)")
        bits = []
        if lw.get("top"): bits.append(f"top starter {lw['top']['name']} {lw['top']['pts']:.1f}")
        if lw.get("dud"): bits.append(f"worst starter {lw['dud']['name']} {lw['dud']['pts']:.1f}")
        if lw["bench_left"] >= 0.5 and lw.get("best_bench"):
            bits.append(f"left {lw['bench_left']:.1f} on the bench (best bench guy: {lw['best_bench']['name']} {lw['best_bench']['pts']:.1f})")
        elif lw["bench_left"] < 0.5:
            bits.append("started the optimal lineup")
        line = "; ".join(bits); notes.append(line[:1].upper() + line[1:])
        g = next((x for x in (graded or []) if n in (x["fav"], x["dog"])), None)
        if g:
            was_fav = g["fav"] == n
            wp = g["fav_wp"] if was_fav else 1 - g["fav_wp"]
            called = g["fav_won"] == was_fav
            notes.append(f"<b>Model last week:</b> had you {fmt_spread(g['spread'] if was_fav else -g['spread'])} "
                         f"({wp*100:.0f}%) — {'called it' if called else 'got it wrong'}")
    if t.get("record"):
        luck = t["luck"]
        luck_s = "" if abs(luck) < 0.5 else (f"; {luck:+.1f} wins vs all-play — {'lucky' if luck > 0 else 'unlucky'}")
        notes.append(f"<b>Season:</b> {t['record']} ({ordinal(t['standing'])}), {t['pf']:.1f} PF ({ordinal(t['pf_rank'])}), "
                     f"{t['pa']:.1f} PA; all-play {t['allplay_pct']*100:.0f}%{luck_s}")
    if t.get("bozo_rank"):
        spread_s = "divisive" if t["poll_sd"] >= 3 else ("near-unanimous" if t["poll_sd"] <= 1.5 else "")
        notes.append(f"<b>Poll:</b> {ordinal(t['bozo_rank'])}{delta(t['bozo_rank'], pt.get('bozo_rank'))} with {t['bozo_points']} pts; "
                     f"ballots ranged {ordinal(t['poll_best'])}–{ordinal(t['poll_worst'])}, sd {t['poll_sd']:.1f}"
                     + (f" ({spread_s})" if spread_s else ""))
    notes.append(f"<b>Model:</b> power {ordinal(t['power_rank'])}{delta(t['power_rank'], pt.get('power_rank'))} ({t['power_pts']}); "
                 f"playoff odds {t['playoff_odds']*100:.0f}%"
                 + (f" ({round((t['playoff_odds']-pt['playoff_odds'])*100):+d} pts)".replace("+0 pts", "no change")
                    if pt.get("playoff_odds") is not None else "")
                 + f", title {t['title_odds']*100:.0f}%, projected {t['proj_wins']:.1f} wins")
    if t.get("fp_rank"):
        ros = f"; rest-of-season {ordinal(t['fp_ros_rank'])} ({t['fp_ros_score']})" if t.get("fp_ros_rank") else ""
        notes.append(f"<b>FantasyPros:</b> {ordinal(t['fp_rank'])}{delta(t['fp_rank'], pt.get('fp_rank'))} ({t['fp_score']}){ros}")
    if t.get("bozo_rank"):
        gap = t["power_rank"] - t["bozo_rank"]
        if abs(gap) >= 4:
            notes.append(f"<b>Bozos vs model:</b> poll has you {abs(gap)} spots {'higher' if gap > 0 else 'lower'} than the model does")
    return notes

def league_notes(snap, prev, graded):
    T = snap["teams"]; names = list(T); out = []
    lw = {n: T[n]["last_week"] for n in names if T[n].get("last_week")}
    if lw:
        hi = max(lw, key=lambda n: lw[n]["points"]); lo = min(lw, key=lambda n: lw[n]["points"])
        out.append(f"High score: {hi} {lw[hi]['points']:.2f} · Low score: {lo} {lw[lo]['points']:.2f}")
        games = {frozenset((n, b["opp"])): (n, b) for n, b in lw.items() if b["won"]}
        margins = sorted(((b["points"] - b["opp_points"], n, b["opp"]) for n, b in games.values()))
        if margins:
            m, w, l = margins[0];  out.append(f"Closest game: {w} over {l} by {m:.2f}")
            m, w, l = margins[-1]; out.append(f"Biggest blowout: {w} over {l} by {m:.2f}")
        bl = max(lw, key=lambda n: lw[n]["bench_left"])
        out.append(f"Most points left on the bench: {bl} ({lw[bl]['bench_left']:.1f})")
        heartbreak = [(n, b) for n, b in lw.items() if not b["won"] and b["allplay_w"] >= 7]
        if heartbreak:
            n, b = max(heartbreak, key=lambda x: x[1]["points"])
            out.append(f"Tough beat: {n} scored {ordinal(b['week_rank'])}-most and still lost")
        cheap = [(n, b) for n, b in lw.items() if b["won"] and b["allplay_w"] <= 4]
        if cheap:
            n, b = min(cheap, key=lambda x: x[1]["points"])
            out.append(f"Stole one: {n} won with only the {ordinal(b['week_rank'])}-highest score of the week")
    if graded:
        right = sum(g["fav_won"] for g in graded)
        out.append(f"Model went {right}-{len(graded)-right} picking winners last week")
        ups = [g for g in graded if not g["fav_won"]]
        if ups:
            g = max(ups, key=lambda g: g["fav_wp"])
            out.append(f"Biggest upset: {g['dog']} beat {g['fav']} ({g['fav']} was {fmt_spread(g['spread'])}, {g['fav_wp']*100:.0f}%)")
        best = min(graded, key=lambda g: g["miss"])
        out.append(f"Best call: {best['fav']} vs {best['dog']} — predicted margin off by {best['miss']:.1f}")
    if snap.get("no_ballot"):
        out.append(f"Didn't vote: {', '.join(snap['no_ballot'])}")
    rk = [n for n in names if T[n].get("bozo_rank")]
    if rk:
        div = max(rk, key=lambda n: T[n]["poll_sd"]); agree = min(rk, key=lambda n: T[n]["poll_sd"])
        out.append(f"Most divisive in the poll: {div} (ballots {ordinal(T[div]['poll_best'])}–{ordinal(T[div]['poll_worst'])}) · "
                   f"most agreed-on: {agree} (sd {T[agree]['poll_sd']:.1f})")
        if prev:
            mv = {n: prev["teams"][n]["bozo_rank"] - T[n]["bozo_rank"] for n in rk if prev["teams"].get(n, {}).get("bozo_rank")}
            if mv:
                up = max(mv, key=mv.get); dn = min(mv, key=mv.get)
                if mv[up] > 0: out.append(f"Biggest poll riser: {up} (▲{mv[up]})")
                if mv[dn] < 0: out.append(f"Biggest poll faller: {dn} (▼{-mv[dn]})")
        gaps = {n: T[n]["power_rank"] - T[n]["bozo_rank"] for n in rk}
        hi = max(gaps, key=gaps.get); lo = min(gaps, key=gaps.get)
        if gaps[hi] >= 3: out.append(f"Bozos love {hi} more than the model does (poll {ordinal(T[hi]['bozo_rank'])}, model {ordinal(T[hi]['power_rank'])})")
        if gaps[lo] <= -3: out.append(f"Model likes {lo} way more than the Bozos (poll {ordinal(T[lo]['bozo_rank'])}, model {ordinal(T[lo]['power_rank'])})")
    preds = [p for p in snap["predictions"] if p["spread"] < 0]
    if preds:
        gotw = min(preds, key=lambda p: -p["spread"]); lock = max(preds, key=lambda p: -p["spread"])
        out.append(f"Week {snap['next_week']} game of the week: {gotw['team']} {fmt_spread(gotw['spread'])} vs {gotw['opp']} "
                   f"· lock of the week: {lock['team']} {fmt_spread(lock['spread'])} vs {lock['opp']} ({lock['win_prob']*100:.0f}%)")
    return out


# =============================================================================
# SECTION 7 — RENDER THE GOOGLE-DOC DRAFT
# Plain HTML with inline styles only, because that's what survives Google Docs'
# HTML import. Layout mirrors the hand-built reports: rankings table (top 6 —
# the playoff line — in bold), then one section per team in poll order.
# =============================================================================
NOTE = 'bgcolor="#fff4d6" style="font-size:9pt;color:#444;"'

def rankings_table(snap, title_row=True):
    T = snap["teams"]; wk = snap["report_week"]
    order = sorted(T, key=lambda n: (T[n]["bozo_rank"] or 99, T[n]["power_rank"]))
    hdr = ["Team", "Bozo Ranking", "Power Ranking (proj pts)", "Standings (Sleeper)" if wk else "Actual Standings",
           "FantasyPros Ranking", "Consensus Ranking", "Playoff Odds (Monte Carlo)"]
    h = ['<table border="1" cellpadding="4" cellspacing="0">', "<tr>" + "".join(f'<td align="center"><b><i>{x}</i></b></td>' for x in hdr) + "</tr>"]
    for i, n in enumerate(order):
        t = T[n]
        cells = [n, t["bozo_rank"] or "—", f"{t['power_rank']} ({t['power_pts']:.1f})", t["standing"] or "n/a",
                 f"{t['fp_rank']} ({t['fp_score']})" if t.get("fp_rank") else "—", t["consensus_rank"],
                 f"{t['playoff_odds']*100:.0f}%"]
        wrap = (lambda s: f"<b><i>{s}</i></b>") if i < 6 else (lambda s: f"<i>{s}</i>")
        h.append("<tr>" + "".join(f'<td align="center">{wrap(html.escape(str(c)))}</td>' for c in cells) + "</tr>")
    h.append("</table>")
    return "\n".join(h)

CHART_PLACEHOLDER = "[[RANK CHART]]"

def render_doc(snap, prev, cfg, chart=None):
    """chart: None (no chart), an image filename to embed (local preview), or
    CHART_PLACEHOLDER — a text line where the chart gets pasted in Google Docs,
    since the Drive upload can't carry the image itself."""
    wk = snap["report_week"]; T = snap["teams"]
    graded = grade_predictions(prev, snap)
    title = cfg["preseason_title"] if wk == 0 else cfg["report_title"].format(week=wk)
    out = [f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head><body style='font-family:Arial;'>",
           f"<p><b>{html.escape(title)}</b></p>", f"<p><i>{html.escape(cfg['tagline'])}</i></p>",
           rankings_table(snap),
           ("" if not chart else f"<p>{chart}</p>" if chart == CHART_PLACEHOLDER
            else f'<p><img src="{html.escape(chart)}" width="624" alt="Week-over-week rankings chart"></p>'),
           f'<p></p><table border="1" cellpadding="6" cellspacing="0"><tr><td {NOTE}><b>📊 League notes — delete before publishing</b> '
           f'({snap["ballots"]} ballots)<br>' + "<br>".join("• " + html.escape(x) for x in league_notes(snap, prev, graded)) + "</td></tr></table>",
           "<p><b>A brief note on methodology</b></p>", "<p><i>[methodology bit]</i></p>",
           "<p><i>Ordered by Bozo ranking</i></p>"]
    order = sorted(T, key=lambda n: (T[n]["bozo_rank"] or 99, T[n]["power_rank"]))
    preds = {p["team"]: p for p in snap["predictions"]}
    for i, n in enumerate(order, 1):
        t = T[n]; tags = []
        if t.get("bozo_rank"):
            if t["first_votes"]:
                own = " — obviously their own?" if t["first_votes"] == 1 and t["bozo_rank"] >= 9 else ""
                tags.append(f"1st Place Votes: {t['first_votes']}{own}")
            else:
                tags.append(f"Highest Bozo Ranking: {ordinal(t['poll_best'])}{times(t['poll_best_ct'])}")
            tags.append(f"Lowest Bozo Ranking: {ordinal(t['poll_worst'])}{times(t['poll_worst_ct'])}")
        out.append(f"<p><b>{i}. {html.escape(n)}</b> " + " ".join(f"<i>({html.escape(x)})</i>" for x in tags) + "</p>")
        p = preds.get(n)
        if p:
            out.append(f"<p><b><i>Proj. Spread vs {html.escape(p['opp'])}: {fmt_spread(p['spread'])}, "
                       f"{p['win_prob']*100:.0f}% win probability</i></b></p>")
        out.append(f'<table border="1" cellpadding="6" cellspacing="0"><tr><td {NOTE}>'
                   + "<br>".join(team_notes(n, snap, prev, graded)) + "</td></tr></table>")
        out.append("<p><i>[writeup]</i></p><p></p>")
    out.append("<p><i>Love you guys! Happy football. Football! Bozo out.</i></p>")
    if prev:
        out.append("<p></p><p><i>Last week’s rankings:</i></p>")
        out.append(rankings_table(prev))
    out.append("</body></html>")
    return "\n".join(out)


def make_chart(week, metric, cfg):
    """Week-over-week rank chart with Sleeper logos (rank_chart.py). Optional:
    needs matplotlib + Pillow, and the report builds fine without it."""
    try:
        import rank_chart
        B = E.load_bundle()
        users = E.get_json(E.API + f"league/{B['CFG']['leagueId']}/users")
        uid2name = {uid: m["name"] for uid, m in cfg["managers"].items()}
        logos = rank_chart.fetch_logos(users, uid2name, os.path.join(DATA, "logos"))
        snaps = [s for s in rank_chart.load_snaps(DATA) if s["report_week"] <= week]
        return rank_chart.render(snaps, os.path.join(REPORTS, f"week_{week}_chart.png"), logos, metric,
                                 B["CFG"]["playoffTeams"])
    except ImportError as e:
        print(f"  ! rank chart skipped ({e.name} not installed: pip install matplotlib pillow)")
    except Exception as e:
        print(f"  ! rank chart failed: {e}")
    return None


# =============================================================================
# SECTION 8 — ENTRY POINT
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="report week (default: the last completed week)")
    ap.add_argument("--sims", type=int, default=10000, help="Monte Carlo seasons (default 10000)")
    ap.add_argument("--refresh-bundle", action="store_true", help="run build_board_data.py first")
    ap.add_argument("--chart-metric", default="bozo_rank",
                    help="ranking the logo chart tracks: bozo_rank (default), consensus_rank, power_rank, fp_rank, standing")
    ap.add_argument("--copy-chart", action="store_true", help="put the chart PNG on the macOS clipboard, ready to paste into the doc")
    a = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "league_config.json"))
    if a.refresh_bundle:
        subprocess.run([sys.executable, os.path.join(HERE, "build_board_data.py")], check=True)
    if a.week is None:
        st = E.get_json(E.API + "state/nfl")
        a.week = max(0, int(st.get("week") or 1) - 1) if st.get("season_type") == "regular" else 0

    snap = build(a.week, a.sims, cfg)
    prev = load_json(os.path.join(DATA, "history", f"week_{a.week-1}.json")) if a.week > 0 else None
    os.makedirs(os.path.join(DATA, "history"), exist_ok=True); os.makedirs(REPORTS, exist_ok=True)
    sp = os.path.join(DATA, "history", f"week_{a.week}.json")
    json.dump(snap, open(sp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    chart = make_chart(a.week, a.chart_metric, cfg)
    dp = os.path.join(REPORTS, f"week_{a.week}_draft.html")
    open(dp, "w", encoding="utf-8").write(render_doc(snap, prev, cfg, chart and os.path.basename(chart)))
    gp = os.path.join(REPORTS, f"week_{a.week}_gdoc.html")      # what gets uploaded to Drive
    open(gp, "w", encoding="utf-8").write(render_doc(snap, prev, cfg, chart and CHART_PLACEHOLDER))
    if chart and a.copy_chart:
        subprocess.run(["osascript", "-e", f'set the clipboard to (read (POSIX file "{chart}") as «class PNGf»)'], check=True)
    try:
        import dashboard; hp = dashboard.write(DATA, REPORTS)
    except ImportError:
        hp = None
    wrote = [sp, dp, gp] + [x for x in (chart, hp) if x]
    print("\nWrote " + "\n      ".join(os.path.relpath(x, HERE) for x in wrote)
          + ("\n(chart copied to clipboard)" if chart and a.copy_chart else ""))

    T = snap["teams"]
    print(f"\n{'Team':8} {'Bozo':>4} {'Power':>13} {'Stand':>5} {'FP':>8} {'Cons':>4} {'PO%':>4}  Next")
    for n in sorted(T, key=lambda n: (T[n]["bozo_rank"] or 99, T[n]["power_rank"])):
        t = T[n]; p = next((x for x in snap["predictions"] if x["team"] == n), None)
        print(f"{n:8} {t['bozo_rank'] or '—':>4} {str(t['power_rank'])+' ('+format(t['power_pts'],'.1f')+')':>13} "
              f"{t['standing'] or 'n/a':>5} {(str(t['fp_rank'])+' ('+str(t['fp_score'])+')') if t.get('fp_rank') else '—':>8} "
              f"{t['consensus_rank']:>4} {t['playoff_odds']*100:3.0f}%  "
              + (f"{fmt_spread(p['spread'])} vs {p['opp']} ({p['win_prob']*100:.0f}%)" if p else ""))

if __name__ == "__main__":
    main()
