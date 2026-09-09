#!/usr/bin/env python3
"""
INPUTS
    bundle.json           built by build_board_data.py — blended positional ranks
                          (primary analyst 50% + FantasyPros superflex ECR 30% +
                          The Athletic 20%, +10% Reception Perception overlay
                          for charted WRs; PFF grades + ESPN O-line win rates carried
                          through as reference context, same inputs the Draft Board
                          uses), D/ST, Sleeper id->player map, NFL schedule, league config
    api.sleeper.app       live rosters + weekly matchups (public, no auth)

"""

import json, os, sys, math, random, argparse
from urllib.request import urlopen

HERE = os.path.dirname(os.path.abspath(__file__))   # so it finds bundle.json next to itself
API  = "https://api.sleeper.app/v1/"


# =============================================================================
# SECTION 1 — MODEL CONSTANTS
# The whole projection is "Draft Board rank -> points," where the rank is the
# same composite the Draft Board uses per position: primary analyst 50% +
# FantasyPros superflex ECR 30% + The Athletic 20%, plus a 10% Reception
# Perception overlay for charted WRs (PFF grades and ESPN O-line win rates ride
# along as reference context, not blend weight). These tables define rank -> pts.
# Each CURVE is a list of [positional_rank, points_per_game] anchor points for
# half-PPR in a superflex league; interp() draws straight lines between anchors.
# Values were calibrated to typical half-PPR output.
# Keep these identical to CURVE in engine.js or the two tools will disagree.
# =============================================================================
CURVE = {
    "QB":[[1,23.5],[3,22],[6,20.5],[9,19],[12,18],[16,16.5],[20,15],[24,14],[28,12.5],[32,11.5],[40,10]],
    "RB":[[1,19.5],[3,17.5],[6,15.5],[9,14],[12,13],[18,11.5],[24,10],[30,9],[36,8],[45,7],[60,6],[80,5]],
    "WR":[[1,18.5],[3,16.5],[6,15],[9,13.5],[12,12.5],[18,11],[24,10],[30,9],[36,8],[45,7],[60,6],[80,5]],
    "TE":[[1,14],[2,12.5],[3,11.5],[5,10],[8,8.5],[12,7.5],[16,6.5],[20,6],[26,5],[32,4.5],[40,4]],
    "DEF":[[1,9.5],[3,8.7],[5,8.2],[8,7.6],[12,7],[16,6.5],[20,6.2],[32,6]],
}
# Fallback points for a rostered player who is BEYOND the Draft Board's ranked pool
# (deep bench, waiver fliers, kickers) — i.e. "replacement level."
REPL = {"QB":8.5,"RB":5.5,"WR":5.5,"TE":4.0,"DEF":6.3,"K":6.0}

LEAGUE_AVG_TOTAL = 44.5   # baseline NFL game total; used for the Vegas environment tilt
MARGIN_SD = 30.0          # spread of the head-to-head margin -> sets how "sure" a win prob is.
                          # Per-team weekly SD = MARGIN_SD / sqrt(2) ≈ 21.2 pts (used by Monte Carlo).


def interp(anchors, x):
    """Piecewise-linear lookup. Clamps below the first / above the last anchor,
    otherwise linearly interpolates between the two surrounding anchors."""
    if x is None: return None
    if x <= anchors[0][0]:  return anchors[0][1]
    if x >= anchors[-1][0]: return anchors[-1][1]
    for (x0,y0),(x1,y1) in zip(anchors, anchors[1:]):
        if x0 <= x <= x1:
            return y0 + (y1-y0)*(x-x0)/(x1-x0)
    return anchors[-1][1]


# =============================================================================
# SECTION 2 — DATA LOADING & NAME MATCHING
# The blended-board data and Sleeper's data use different spellings. We normalize
# names (strip accents, Jr/Sr suffixes, punctuation) so a Sleeper roster id can be
# matched to a ranking. build_index() prepares those lookups once.
# =============================================================================
def get_json(url):
    with urlopen(url, timeout=30) as r:
        return json.load(r)

def load_bundle():
    p = os.path.join(HERE, "bundle.json")
    if not os.path.exists(p):
        sys.exit("bundle.json not found — run build_board_data.py first.")
    return json.load(open(p, encoding="utf-8"))

def build_index(B):
    """Return (norm, board_by_name_pos, dst_by_team):
       - norm(name)            -> canonical match key
       - board_by_name_pos     -> {"norm|POS": Draft Board record}
       - dst_by_team           -> {"PHI": Draft Board D/ST record}"""
    import re, unicodedata
    def norm(s):
        if not s: return ""
        s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode().lower()
        s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",s); s = re.sub(r"[^a-z ]","",s)
        return re.sub(r"\s+"," ",s).strip()
    by_name = {}
    for pl in B["BOARD"]:
        by_name.setdefault(norm(pl["n"]) + "|" + pl["p"], pl)   # first (best) rank wins on dup names
    dst = {d["t"]: d for d in B["DST"]}
    return norm, by_name, dst


# =============================================================================
# SECTION 3 — PLAYER PROJECTIONS
# base_points(): a player's season-long value (rank -> curve).
# resolve_player(): turn a raw Sleeper player_id into an enriched record with
#   its blended Draft Board rank + base points (handles defenses, kickers, and
#   unmatched deep players via REPL). The individual source ranks (primary
#   analyst, FantasyPros ECR, Athletic, PFF grade, ESPN win rate) ride along on
#   the record as reference fields even though only the blended "pr"/"rk"
#   drives the projection curve.
# week_points(): the per-week number — base, but 0 on a bye, nudged slightly by
#   the Vegas game total in weeks that have betting lines (1-4 in the bundle).
# =============================================================================
def base_points(pl):
    if pl["p"] == "DEF":
        return interp(CURVE["DEF"], pl["rk"]) if pl.get("rk") is not None else REPL["DEF"]
    if pl.get("pr") is None:                 # no blended rank match -> replacement level
        return REPL.get(pl["p"], 4.0)
    return interp(CURVE[pl["p"]], pl["pr"])  # pl["pr"] = blended positional rank

def resolve_player(pid, B, idx):
    norm, by_name, dstByTeam = idx
    # Sleeper encodes a defense as the team abbreviation itself (e.g. "PHI").
    if len(pid) <= 3 and pid.isalpha() and pid.isupper():
        d = dstByTeam.get(pid)
        o = {"id":pid,"n":(d and d.get("n")) or pid+" D/ST","p":"DEF","t":pid,
             "pr":None,"rk":(d and d.get("rk")),"fp":(d and d.get("fp")),
             "bye":(d and d.get("bye")),"matched":bool(d)}
        o["base"] = base_points(o); return o
    meta = B["SLED"].get(pid)                # id -> {name, pos, team, match-key}
    if not meta: return None
    if meta["p"] == "K":                     # no kicker slot in this league; flat value
        return {"id":pid,"n":meta["n"],"p":"K","t":meta["t"],"pr":None,"bye":None,"base":REPL["K"],"matched":False}
    b = by_name.get(meta["k"] + "|" + meta["p"])   # look up the blended rank by normalized name+pos
    o = {"id":pid,"n":meta["n"],"p":meta["p"],"t":meta["t"],
         "pr":(b and b["pr"]),"ov":(b and b["ov"]),"tier":(b and b.get("tier")),
         "primary":(b and b.get("primary")),"ecr":(b and b.get("ecr")),
         "athletic":(b and b.get("athletic")),"pff":(b and b.get("pff")),
         "espn":(b and b.get("espn")),
         "bye":(b and b["bye"]),"matched":bool(b)}
    o["base"] = base_points(o); return o

def week_points(pl, week, SCHED, ignore_bye=False):
    if not ignore_bye and pl.get("bye") == week:   # on bye -> 0 (optimizer will bench them)
        return 0.0
    base = pl["base"]; tilt = 1.0
    sc = SCHED.get(pl["t"])                          # this player's NFL team's schedule
    if sc:
        g = next((x for x in sc if x["w"] == week), None)
        if g and g.get("tot") is not None:          # only weeks with a Vegas total
            # higher-total game -> small boost, lower-total -> small cut, capped ±(10-12%)
            tilt = max(0.90, min(1.12, 1 + ((g["tot"]/LEAGUE_AVG_TOTAL)-1)*0.35))
    return round(base*tilt, 2)


# =============================================================================
# SECTION 4 — OPTIMAL LINEUP
# Fill the fixed slots (QB, 2 RB, 3 WR, TE, DEF) with the best player at each
# spot, then choose the FLEX (RB/WR/TE) and SUPER_FLEX (QB/RB/WR/TE) as the best
# DISJOINT pair from what's left. We brute-force every eligible pair (cheap, and
# guaranteed optimal — greedy can misfire when a QB belongs in the superflex).
# ignore_bye=True gives "roster strength" (used for power rankings).
# =============================================================================
def optimize(players, week, SCHED, ignore_bye=False):
    pool = []
    for p in players:
        q = dict(p); q["proj"] = p["base"] if ignore_bye else week_points(p, week, SCHED)
        pool.append(q)
    used = set()
    def take(pos):                          # grab the best unused player at a position
        cands = sorted((p for p in pool if p["p"]==pos and p["id"] not in used),
                       key=lambda p:-p["proj"])
        if cands: used.add(cands[0]["id"]); return cands[0]
        return None
    lineup = [("QB",take("QB")),("RB",take("RB")),("RB",take("RB")),
              ("WR",take("WR")),("WR",take("WR")),("WR",take("WR")),
              ("TE",take("TE")),("DEF",take("DEF"))]
    rem = [p for p in pool if p["id"] not in used]
    flex = sorted((p for p in rem if p["p"] in ("RB","WR","TE")), key=lambda p:-p["proj"])[:12]
    sf   = sorted((p for p in rem if p["p"] in ("QB","RB","WR","TE")), key=lambda p:-p["proj"])[:12]
    best = None
    for f in flex:                          # try every FLEX + SUPER_FLEX combination
        for s in sf:
            if f["id"]==s["id"]: continue
            v = f["proj"]+s["proj"]
            if best is None or v>best[0]: best=(v,f,s)
    f = best[1] if best else (flex[0] if flex else None)
    s = best[2] if best else next((x for x in sf if not f or x["id"]!=f["id"]), None)
    lineup += [("FLEX",f),("SF",s)]
    total = round(sum(x[1]["proj"] for x in lineup if x[1]), 2)
    return lineup, total


# =============================================================================
# SECTION 5 — WIN PROBABILITY
# Two projected team totals differ by some margin. Real fantasy weeks are noisy,
# so we don't just say "higher total wins" — we convert the margin into a
# probability with a normal curve. phi() is the normal CDF; a 30-pt SD means a
# ~10-pt projected edge is only ~63% to win.
# =============================================================================
def phi(z):  # normal CDF (Abramowitz-Stegun approximation), matches engine.js
    t = 1/(1+0.2316419*abs(z)); d = 0.3989423*math.exp(-z*z/2)
    p = d*t*(0.3193815+t*(-0.3565638+t*(1.781478+t*(-1.821256+t*1.330274))))
    return 1-p if z>0 else p

def win_prob(margin): return phi(margin/MARGIN_SD)


# =============================================================================
# SECTION 6 — LEAGUE ASSEMBLY
# Pull live users/rosters/matchups from Sleeper and turn them into:
#   teams[rid]   = {name, owner, resolved players, power}   (power = bye-free optimum)
#   matchups[w]  = [(rosterA, rosterB), ...]                (the real weekly schedule)
# =============================================================================
def load_league(B):
    idx = build_index(B); lid = B["CFG"]["leagueId"]
    users   = get_json(API+"league/"+lid+"/users")
    rosters = get_json(API+"league/"+lid+"/rosters")
    # team display name: custom team_name if set, else the owner's handle
    tn = {u["user_id"]: (u.get("metadata",{}).get("team_name") or u.get("display_name")) for u in users}
    teams = {}
    for r in rosters:
        players = [rp for rp in (resolve_player(pid,B,idx) for pid in (r.get("players") or [])) if rp]
        teams[r["roster_id"]] = {
            "rid":r["roster_id"], "owner":r["owner_id"],
            "name":tn.get(r["owner_id"], f"Team {r['roster_id']}"), "players":players,
            "power":optimize(players,1,B["SCHED"],ignore_bye=True)[1],   # roster strength
        }
    # weekly matchups: Sleeper lists two rows sharing a matchup_id -> pair them
    matchups = {}
    for w in range(1, B["CFG"]["regWeeks"]+1):
        m = get_json(API+"league/"+lid+"/matchups/"+str(w))
        bm = {}
        for e in m:
            if e.get("matchup_id") is not None:
                bm.setdefault(e["matchup_id"], []).append(e["roster_id"])
        matchups[w] = [p for p in bm.values() if len(p)==2]
    return teams, matchups


# =============================================================================
# SECTION 7 — WEEKLY MATCHUPS + WIN PROBABILITIES
# For one week: each pairing's projected optimal-lineup scores and the favorite's
# win probability. Your game is sorted first and marked with '*'.
# =============================================================================
def week_matchups(teams, matchups, SCHED, cfg, week):
    ridname = {r: t["name"] for r,t in teams.items()}
    my = next((t["rid"] for t in teams.values() if t["owner"]==cfg["myUserId"]), None)
    rows = []
    for a,b in matchups.get(week, []):
        pa = optimize(teams[a]["players"], week, SCHED)[1]   # team A optimal total
        pb = optimize(teams[b]["players"], week, SCHED)[1]   # team B optimal total
        wpa = win_prob(pa-pb)
        rows.append((a,b,pa,pb,wpa, my in (a,b)))
    rows.sort(key=lambda r:(not r[5], -(r[2]+r[3])))         # my game first, then biggest totals
    print(f"\n=== WEEK {week} MATCHUPS ===")
    for a,b,pa,pb,wpa,mine in rows:
        star = " *" if mine else "  "
        fav  = ridname[a] if pa>=pb else ridname[b]          # favorite = higher projection
        favp = max(wpa, 1-wpa)
        print(f"{star}{ridname[a]:28} {pa:6.1f}  vs {pb:6.1f}  {ridname[b]:28}"
              f" | {fav.split(' ')[0]:12} {favp*100:4.0f}%")


# =============================================================================
# SECTION 8 — SEASON PROJECTIONS
# expected_wins(): deterministic — sum each week's win probability. This is the
#   "projected record" (e.g. 8.6-5.4), NOT a simulation.
# monte_carlo(): the simulation — replay the real schedule N times, adding a
#   random gaussian shock to each team's weekly projection, then tally how often
#   each team makes the top-6 (playoff%) and wins it all (title%).
# =============================================================================
def expected_wins(teams, matchups, SCHED):
    ew = {r:0.0 for r in teams}
    for w, pairs in matchups.items():
        proj = {r: optimize(teams[r]["players"], w, SCHED)[1] for r in teams}
        for a,b in pairs:
            p = win_prob(proj[a]-proj[b]); ew[a]+=p; ew[b]+=(1-p)
    return ew

def monte_carlo(teams, matchups, SCHED, cfg, N):
    rids = list(teams)
    # precompute every team's projected total for every week once (byes included)
    week_proj = {w: {rid: optimize(teams[rid]["players"], w, SCHED)[1] for rid in rids}
                 for w in matchups}
    SD = MARGIN_SD/math.sqrt(2)             # per-team weekly noise (~21 pts)
    playoff = {r:0 for r in rids}; title = {r:0 for r in rids}
    for _ in range(N):                      # one simulated season per iteration
        wins = {r:0 for r in rids}; pf = {r:0.0 for r in rids}
        for w, pairs in matchups.items():
            for a,b in pairs:
                sa = week_proj[w][a] + random.gauss(0,SD)   # projection + random swing
                sb = week_proj[w][b] + random.gauss(0,SD)
                pf[a]+=sa; pf[b]+=sb                         # accumulate points-for (tiebreaker)
                if sa>=sb: wins[a]+=1
                else:      wins[b]+=1
        # seed by wins, then points-for; top N teams make the playoffs
        seeded = sorted(rids, key=lambda r:(-wins[r], -pf[r]))
        po = seeded[:cfg["playoffTeams"]]
        for r in po: playoff[r]+=1
        # champion (approximation): lottery among playoff teams weighted by points-for^2.
        # NOTE: this is not a real reseeded bracket — good enough for a title% ballpark.
        tot = sum(pf[r]**2 for r in po); rnd = random.random()*tot; acc=0; champ=po[0]
        for r in po:
            acc += pf[r]**2
            if rnd <= acc: champ=r; break
        title[champ]+=1
    return ({r:playoff[r]/N for r in rids}, {r:title[r]/N for r in rids})


# =============================================================================
# SECTION 9 — REPORT (command-line entry point)
# Figures out the target week (current NFL week unless --week given), then prints:
# your optimal lineup, the week's matchups, power rankings, and Monte Carlo
# projected standings.
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="target week (default = current NFL week)")
    ap.add_argument("--sims", type=int, default=4000, help="Monte Carlo seasons (default 4000)")
    a = ap.parse_args()

    B = load_bundle(); cfg = B["CFG"]; SCHED = B["SCHED"]
    print(f"Loading {cfg['leagueId']} ...")
    teams, matchups = load_league(B)

    # current NFL week drives the default lineup/matchups view; --week overrides
    try:
        cur = int(get_json(API+"state/nfl").get("display_week") or 1)
    except Exception:
        cur = 1
    week = a.week or max(1, min(cur, cfg["regWeeks"]))

    # --- your optimal lineup for the target week ---
    mine = next((t for t in teams.values() if t["owner"]==cfg["myUserId"]), None)
    if mine:
        lineup,total = optimize(mine["players"], week, SCHED)
        print(f"\n=== {mine['name']} — Week {week} optimal lineup ===")
        for slot,p in lineup:
            print(f"  {slot:5} {(p['n']+' ('+p['p']+'-'+(p['t'] or '')+')') if p else '—':32} "
                  f"{p['proj'] if p else 0:5}")
        print(f"  TOTAL {total}")

    # --- this week's matchups + win probabilities ---
    week_matchups(teams, matchups, SCHED, cfg, week)

    # --- power rankings (roster strength, bye-free) ---
    print("\n=== POWER RANKINGS ===")
    for i,t in enumerate(sorted(teams.values(), key=lambda t:-t["power"]), 1):
        print(f"{i:2} {t['name']:30} {t['power']:.1f}")

    # --- projected final standings via Monte Carlo ---
    print(f"\n=== PROJECTED STANDINGS (Monte Carlo, {a.sims} seasons) ===")
    ew = expected_wins(teams, matchups, SCHED)
    po, ti = monte_carlo(teams, matchups, SCHED, cfg, a.sims)
    rows = sorted(teams.values(), key=lambda t:(-po[t["rid"]], -ew[t["rid"]]))
    reg = cfg["regWeeks"]
    print(f"{'#':>2}  {'Team':30} {'Rec':>9}  {'Playoff':>7}  {'Title':>6}")
    for i,t in enumerate(rows,1):
        w = ew[t["rid"]]
        flag = "  <- PLAYOFF" if po[t["rid"]]>=0.5 else ""
        print(f"{i:>2}  {t['name']:30} {w:4.1f}-{reg-w:<4.1f}  "
              f"{po[t['rid']]*100:6.0f}%  {ti[t['rid']]*100:5.1f}%{flag}")

if __name__ == "__main__":
    main()
