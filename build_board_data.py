#!/usr/bin/env python3
"""
build_board_data.py  —  rebuild the data bundle for the FFB tools, LIVE all season.

What it does:
  1. Pulls three ranking feeds and blends them into a per-position rank the
     same way the Draft Board does:
       - Primary analyst (Yahoo)     50%  — sports.yahoo.com/api/fanPro, expert
                                             id 317, fetched directly from Yahoo
       - FantasyPros consensus ECR   30%  — fantasypros.com/nfl/rankings (146
                                             experts, superflex half-PPR; the
                                             WEEKLY page once the season starts,
                                             the draft/cheatsheet page before it)
       - The Athletic                20%  — partners.fantasypros.com, expert id
                                             3701 (the same public widget The
                                             Athletic embeds on its own site)
     Weighted average of each source's POSITIONAL rank -> re-sorted into a new
     positional rank ("pr"). If a player is missing from one source the other
     weights renormalize to fill the gap. The Draft Board also folds in a 10%
     Reception Perception overlay for charted WRs; RP is subscriber-only with no
     public feed, so it isn't reproduced here — validated against the 2026 board,
     this 3-source blend lands on the board's exact posRk ~57% of the time and
     within one spot ~86% of the time.
     D/ST uses the board's own simpler blend: a straight average of the primary
     analyst's D/ST rank and FantasyPros' D/ST consensus ECR (no Athletic input).

     IMPORTANT — why primary analyst moved off the FantasyPros partner API:
     that endpoint (still used for Athletic) only mirrors an expert's PRESEASON
     DRAFT board and stops getting refreshed once the season starts — confirmed
     stuck on its Sept-9 snapshot for BOTH experts even weeks into the season,
     while Boone's own Yahoo board kept moving with real results. So primary
     analyst now goes straight to Yahoo, which stays genuinely live. Athletic
     still comes through the FantasyPros mirror and is automatically dropped
     from the blend (renormalizing to primary+ECR) whenever it's stale — see
     is_stale() — rather than silently blending outdated numbers in-season.
  2. Optionally pulls PFF player grades (2025 REG season) and ESPN O-line win
     rates as REFERENCE fields — these were context in the original board, not
     blend weight, so they never affect "pr" or the projection curve:
       - PFF: needs your own premium.pff.com session (paywalled). Reads
         secrets.local.json's "pff_token" (the X-PFF-Token request header —
         see README for how to grab it). Skipped gracefully if absent/expired.
       - ESPN: their analytics article sits behind bot-detection that blocks
         plain scripts entirely (not a login wall — cookies don't help). Reads
         a locally cached snapshot (espn_winrates_2025.json) instead; refresh
         that file by asking Claude to re-browse the source URL in it.
  3. Reads the 2026 NFL schedule + Vegas game-total tilt from the STATIC Draft
     Guide's embedded DATA blob — this file is a frozen pre-season snapshot and
     is only ever read here, never regenerated (the schedule itself doesn't
     change week to week, and Vegas totals would need a paid odds API).
  4. Pulls the live Sleeper player map + your league config from api.sleeper.app.
  5. Writes bundle.json  =  { BOARD, DST, SLED, SCHED, CFG } — this is the LIVE
     side of the tool; the Draft Guide/Draft Board HTML files stay static.

Run this any time during the season — the primary/Athletic analysts revise
their boards and the 146-expert ECR panel shifts continuously, so re-running
re-blends against whatever is live right now. Once an expert starts publishing
rest-of-season (ROS) updates instead of their preseason board, this picks that
up automatically.

Requires: Python 3.8+  (standard library only — no pip installs).
Run it from this folder (it finds files next to itself).
"""

import json, re, os, sys, gzip, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.request import urlopen, Request

# ---------------------------------------------------------------- config
HERE   = os.path.dirname(os.path.abspath(__file__))
GUIDE  = os.path.join(HERE, "BKN FFB 2026 Draft Guide.html")   # static — schedule/Vegas source only
OUT    = os.path.join(HERE, "bundle.json")
SECRETS_FILE = os.path.join(HERE, "secrets.local.json")         # gitignored, local only
ESPN_SNAPSHOT = os.path.join(HERE, "espn_winrates_2025.json")
LID    = "1392331238445973504"                                  # Sleeper league id
MY_USER_ID = "1392338811781926912"                              # thombus
API    = "https://api.sleeper.app/v1/"

SEASON      = 2026
PRIMARY_ID  = 317    # primary analyst (50% weight) — Yahoo's own expert id for him
ATHLETIC_ID = 3701   # The Athletic  (20% weight) — FantasyPros expert id
FP_ECR_DRAFT_URL  = "https://www.fantasypros.com/nfl/rankings/superflex-cheatsheets.php"      # pre-season only
FP_ECR_WEEKLY_URL = "https://www.fantasypros.com/nfl/rankings/half-point-ppr-superflex.php"   # live all season
FP_DST_URL  = "https://www.fantasypros.com/nfl/rankings/dst.php"   # already a live weekly page year-round
PARTNER_API = "https://partners.fantasypros.com/api/v1/expert-rankings.php"
YAHOO_API   = "https://sports.yahoo.com/api/fanPro/"
STALE_DAYS  = 10     # a source's self-reported "published" date older than this -> drop it from the blend
PFF_SEASON  = 2025    # most recent completed season — PFF grades feed 2026 preseason evaluation
UA = {"User-Agent": "Mozilla/5.0 (compatible; ffb-engine/1.0; personal use)"}

def get_json(url):
    with urlopen(url, timeout=30) as r:
        return json.load(r)

def fetch_text(url, extra_headers=None):
    headers = {**UA, **(extra_headers or {})}
    with urlopen(Request(url, headers=headers), timeout=30) as r:
        raw = r.read()
        if r.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

def load_secrets():
    if os.path.exists(SECRETS_FILE):
        try:
            return json.load(open(SECRETS_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}

# ---------------------------------------------------------------- helpers
def norm(s):
    """Normalize a name so different sources' spellings match (accents, Jr/Sr, punctuation)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"[^a-z ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def extract_braces(text, marker):
    """Pull the JS object literal after `marker` by walking matching braces."""
    i = text.find(marker)
    if i < 0:
        return None
    i += len(marker)
    depth, start = 0, None
    for k in range(i, len(text)):
        c = text[k]
        if c == "{":
            if start is None:
                start = k
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:k + 1])
    return None

def pos_rank_int(pos_rank):
    """'RB12' -> 12"""
    if not pos_rank:
        return None
    m = re.search(r"(\d+)\s*$", str(pos_rank))
    return int(m.group(1)) if m else None

def to_int_bye(v):
    return int(v) if str(v or "").strip().isdigit() else None

def is_stale(published, max_days=STALE_DAYS):
    """True if a source's self-reported publish date is older than max_days.
    Used to drop a source that has stopped updating (e.g. a synced board that
    only refreshes pre-draft, which is exactly what happened to every expert
    we tried on the FantasyPros partner API once the season started) instead
    of silently blending in outdated numbers."""
    if not published:
        return True
    try:
        pub = datetime.strptime(published[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return False   # can't parse -> don't assume stale
    return (datetime.now(timezone.utc) - pub).days > max_days

# ---------------------------------------------------------------- 1a) FantasyPros consensus ECR (public, no login)
def fetch_consensus_ecr(url):
    """Scrape the ecrData blob FantasyPros embeds server-side on its public rankings
    pages. Keyed by (normalized name, position) so it joins cleanly against the
    other two sources regardless of which platform they came from."""
    html = fetch_text(url)
    data = extract_braces(html, "var ecrData = ")
    if not data:
        sys.exit(f"ERROR: could not find ecrData on {url}")
    out = {}
    for p in data["players"]:
        pos = p.get("player_position_id") or p.get("player_positions")
        out[(norm(p["player_name"]), pos)] = {
            "n": p["player_name"], "t": p.get("player_team_id"), "p": pos,
            "bye": p.get("player_bye_week"),
            "pr": pos_rank_int(p.get("pos_rank")),
            "ov": p.get("rank_ecr"),
        }
    return out

# ---------------------------------------------------------------- 1b) Yahoo — primary analyst's own live board
def fetch_yahoo_expert(expert_id, position):
    """An expert's board straight from Yahoo, their native publishing platform.
    Unlike the FantasyPros partner mirror (fetch_expert, below), this stays
    genuinely live all season — confirmed by diffing snapshots taken days apart
    and seeing real rank movement that tracked actual results. Yahoo doesn't
    expose a positional rank directly, so for skill players we derive one by
    grouping on position and re-ordering by Yahoo's overall rank."""
    url = (f"{YAHOO_API}?sport=NFL&position={position}&filters={expert_id}&experts=show"
           f"&expert={expert_id}&scoring=HALF&type=ST&week=0&wtype=PRESEASON&year={SEASON}")
    data = json.loads(fetch_text(url))
    players = data.get("players") or []
    if not players:
        return {}, None
    src = f"Yahoo, live (last updated {data.get('lastUpdated')})"
    if position == "DST":
        out = {p["team"]: {"n": p["name"], "pr": p["rank"], "bye": p.get("byeWeek")}
               for p in players if p.get("team")}
        return out, src
    byPos = defaultdict(list)
    for p in players:
        pos = p.get("position")
        if pos in ("QB", "RB", "WR", "TE"):
            byPos[pos].append(p)
    out = {}
    for pos, plist in byPos.items():
        plist.sort(key=lambda x: x["rank"])
        for i, p in enumerate(plist, 1):
            out[(norm(p["name"]), pos)] = {
                "n": p["name"], "t": p.get("team"), "p": pos,
                "bye": p.get("byeWeek"), "pr": i,
            }
    return out, src

# ---------------------------------------------------------------- 1c) FantasyPros partner API (Athletic only)
def fetch_expert(expert_id, position, cur_week):
    """One expert's rankings via FantasyPros' public partner-widget API (the same
    endpoint The Athletic's own rankings page embeds). Tries rest-of-season
    first once the season is underway, falling back to the preseason/draft
    board. If nothing found is fresh (is_stale), returns empty rather than
    blending in a board that stopped updating."""
    attempts = ([(cur_week, "ROS")] if cur_week and cur_week >= 1 else []) + [(0, "PRESEASON")]
    stale_seen = None
    for wk, kind in attempts:
        url = (f"{PARTNER_API}?callback=FPW.cb&position={position}&sport=NFL"
               f"&year={SEASON}&week={wk}&id={expert_id}&scoring=HALF&type={kind}")
        raw = fetch_text(url)
        m = re.search(r"FPW\.cb\((.*)\);?\s*$", raw.strip(), re.S)
        data = json.loads(m.group(1)) if m else {}
        if not data.get("count"):
            continue
        if is_stale(data.get("published")):
            stale_seen = data.get("published")
            continue
        src = f"{data.get('expert_name','?')} ({kind}, wk{wk}, published {data.get('published')})"
        out = {}
        for p in data["players"]:
            pos = p.get("player_positions")
            out[(norm(p["player_name"]), pos)] = {
                "n": p["player_name"], "t": p.get("player_team_id"), "p": pos,
                "bye": p.get("bye_week"), "pr": pos_rank_int(p.get("pos_rank")),
            }
        return out, src
    if stale_seen:
        return {}, f"stale (last published {stale_seen}, >{ STALE_DAYS }d old — excluded from blend)"
    return {}, "no data available"

# ---------------------------------------------------------------- 1c) PFF grades (optional, needs your own session)
def fetch_pff_grades(secrets):
    """Player PFF grades for PFF_SEASON via premium.pff.com's facet API. Needs
    secrets['pff_token'] (the X-PFF-Token request header from your logged-in
    session — see README). Returns {} and prints a note if unavailable/expired;
    this is reference data only, never required for the projection math."""
    token = (secrets or {}).get("pff_token")
    if not token:
        print("  PFF: no pff_token in secrets.local.json — skipping (reference field only)")
        return {}
    out = {}
    for facet in ("passing", "rushing", "receiving"):
        url = f"https://premium.pff.com/api/v1/facet/{facet}/summary?league=nfl&season={PFF_SEASON}&week=REG"
        try:
            raw = fetch_text(url, extra_headers={"X-PFF-Token": token, "Accept": "application/json"})
            data = json.loads(raw)
        except Exception as e:
            print(f"  PFF {facet} grades unavailable ({e}) — token likely expired, skipping")
            continue
        for row in data.get(f"{facet}_summary", []):
            name = row.get("player")
            grade = row.get("grades_offense")
            if name and grade is not None:
                out.setdefault(norm(name), grade)
    if out:
        print(f"  PFF: {len(out)} player grades ({PFF_SEASON} REG season)")
    return out

def load_espn_winrates():
    """Team pass/run block win rates — cached snapshot (ESPN's article sits behind
    bot-detection that blocks plain scripts; see file header for how to refresh)."""
    if not os.path.exists(ESPN_SNAPSHOT):
        return {}
    data = json.load(open(ESPN_SNAPSHOT, encoding="utf-8"))
    print(f"  ESPN: {len(data.get('teams', {}))} team win rates (cached snapshot, {data.get('fetched')})")
    return data.get("teams", {})

# ---------------------------------------------------------------- 2) blend skill players
def blend_skill(primary_map, ecr_map, athletic_map):
    """Weighted average of each source's POSITIONAL rank (primary 50 / ECR 30 /
    Athletic 20, renormalized when a source is missing a player), re-sorted
    into a new blended positional rank per position."""
    all_ids = set(primary_map) | set(ecr_map) | set(athletic_map)
    byPos = defaultdict(list)
    for pid in all_ids:
        pr, e, a = primary_map.get(pid), ecr_map.get(pid), athletic_map.get(pid)
        src = pr or e or a
        pos = src["p"]
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        parts = []
        if pr and pr.get("pr"): parts.append((0.5, pr["pr"]))
        if e and e.get("pr"): parts.append((0.3, e["pr"]))
        if a and a.get("pr"): parts.append((0.2, a["pr"]))
        if not parts:
            continue
        wsum = sum(w for w, _ in parts)
        score = sum(w * r for w, r in parts) / wsum
        byPos[pos].append({
            "n": src["n"], "t": src["t"], "p": pos,
            "bye": to_int_bye(src.get("bye")),
            "score": score,
            "primary": pr["pr"] if pr else None,
            "ecr": e["pr"] if e else None,
            "athletic": a["pr"] if a else None,
            "ov": (e or {}).get("ov"),
        })
    out = []
    for pos, players in byPos.items():
        players.sort(key=lambda p: p["score"])
        for i, p in enumerate(players, 1):
            p["pr"] = i
            out.append(p)
    return out

def blend_dst(primary_dst, ecr_dst):
    """D/ST: straight average of the primary analyst's D/ST rank + FantasyPros
    D/ST consensus ECR, matched by team."""
    teams = set(primary_dst) | set(ecr_dst)
    rows = []
    for t in teams:
        pr, e = primary_dst.get(t), ecr_dst.get(t)
        parts = [x["pr"] for x in (pr, e) if x and x.get("pr")]
        if not parts:
            continue
        src = pr or e
        rows.append({
            "t": t, "n": src["n"], "bye": to_int_bye(src.get("bye")),
            "score": sum(parts) / len(parts),
            "primary": pr["pr"] if pr else None, "fp": e["pr"] if e else None,
        })
    rows.sort(key=lambda r: r["score"])
    for i, r in enumerate(rows, 1):
        r["rk"] = i
    return rows

# ================================================================== run
secrets = load_secrets()

try:
    cur_week = int(get_json(API + "state/nfl").get("display_week") or 0)
except Exception:
    cur_week = 0

print("Fetching live rankings (FantasyPros consensus ECR, primary analyst, Athletic) ...")
ecr_url    = FP_ECR_WEEKLY_URL if cur_week >= 1 else FP_ECR_DRAFT_URL
ecr_skill  = fetch_consensus_ecr(ecr_url)
ecr_dst_raw = fetch_consensus_ecr(FP_DST_URL)
ecr_dst    = {v["t"]: v for v in ecr_dst_raw.values()}
print(f"  FantasyPros consensus ECR ({'weekly wk'+str(cur_week) if cur_week>=1 else 'draft'}): "
      f"{len(ecr_skill)} skill players, {len(ecr_dst)} D/ST")

primary_skill, primary_src = fetch_yahoo_expert(PRIMARY_ID, "ALL")
primary_dst, primary_dst_src = fetch_yahoo_expert(PRIMARY_ID, "DST")
print(f"  Primary analyst: {len(primary_skill)} skill players from {primary_src}")
print(f"  Primary analyst D/ST: {len(primary_dst)} teams from {primary_dst_src}")

athletic_skill, athletic_src = fetch_expert(ATHLETIC_ID, "ALL", cur_week)
print(f"  Athletic: {len(athletic_skill)} skill players from {athletic_src}")

print("Fetching PFF grades + ESPN O-line win rates (reference fields) ...")
pff_grades   = fetch_pff_grades(secrets)
espn_winrate = load_espn_winrates()

blended = blend_skill(primary_skill, ecr_skill, athletic_skill)
BOARD = [{
    "n": p["n"], "t": p["t"], "p": p["p"], "bye": p["bye"],
    "tier": None,
    "ov": p["ov"],              # FantasyPros consensus superflex overall rank (reference)
    "pr": p["pr"],               # BLENDED positional rank -> drives the projection curve
    "ecr": p["ecr"],             # FantasyPros ECR positional rank (reference)
    "primary": p["primary"],     # primary analyst's raw positional rank (reference)
    "athletic": p["athletic"],   # The Athletic positional rank (reference)
    "pff": pff_grades.get(norm(p["n"])),          # PFF offensive grade, reference only
    "espn": espn_winrate.get(p["t"]),              # {pbwr, rbwr, ...} team win rates, reference only
    "d": None,
} for p in blended]
print(f"  Blended board: {len(BOARD)} players  {dict(Counter(p['p'] for p in BOARD))}")

dst_blended = blend_dst(primary_dst, ecr_dst)
DST = [{
    "t": d["t"], "n": d["n"],
    "ov": d["primary"],  # primary analyst's raw D/ST rank (reference)
    "fp": d["fp"],       # FantasyPros D/ST ECR (reference)
    "rk": d["rk"],       # BLENDED D/ST rank -> drives the curve
    "bye": d["bye"],
    "espn": espn_winrate.get(d["t"]),
} for d in dst_blended]
print(f"  D/ST: {len(DST)} teams")

# ---------------------------------------------------------------- schedule (static Draft Guide snapshot)
print("Reading NFL schedule + Vegas tilt from (static):", os.path.basename(GUIDE))
html = open(GUIDE, encoding="utf-8").read()
GDATA = extract_braces(html, "const DATA = ")
SCHED = GDATA["sched"]
print(f"  schedule teams: {len(SCHED)}")

# ---------------------------------------------------------------- live Sleeper data
print("Fetching Sleeper player map + league config ...")
sp = get_json(API + "players/nfl")     # ~12k players
keep = {"QB", "RB", "WR", "TE", "K", "DEF"}
SLED = {}
for pid, p in sp.items():
    pos = p.get("position")
    if pos not in keep:
        continue
    if pos == "DEF":
        team = p.get("team")
        name = (team and f"{team} D/ST") or p.get("full_name")
        key  = norm(team or "")
    else:
        name = p.get("full_name")
        team = p.get("team")
        key  = norm(name)
    if not name:
        continue
    SLED[pid] = {"n": name, "p": pos, "t": team, "k": key}
print(f"  Sleeper players kept: {len(SLED)}")

L = get_json(API + "league/" + LID)
CFG = {
    "leagueId": LID, "season": L["season"],
    "regWeeks": 14, "playoffStart": 15, "playoffTeams": 6, "teams": L["total_rosters"],
    "slots": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "DEF"],
    "myUserId": MY_USER_ID,
    "scoring": "Half-PPR · Superflex",
}
print(f"  League: {L['name']} ({L['season']}, {L['total_rosters']} teams)")

# ---------------------------------------------------------------- match-rate sanity check
rosters = get_json(API + "league/" + LID + "/rosters")
bidx = {}
for b in BOARD:
    bidx.setdefault(norm(b["n"]) + "|" + b["p"], b)
tot = matched = 0
for r in rosters:
    for pid in (r.get("players") or []):
        m = SLED.get(pid)
        if not m or m["p"] in ("DEF", "K"):
            continue
        tot += 1
        if bidx.get(m["k"] + "|" + m["p"]):
            matched += 1
print(f"  Roster match rate: {matched}/{tot} skill players ({100*matched//max(tot,1)}%)")

# ---------------------------------------------------------------- write bundle.json
bundle = {"BOARD": BOARD, "DST": DST, "SLED": SLED, "SCHED": SCHED, "CFG": CFG}
with open(OUT, "w", encoding="utf-8") as f:
    f.write(json.dumps(bundle, separators=(",", ":")))
print(f"\nWrote {os.path.basename(OUT)}  (~{os.path.getsize(OUT)//1024} KB)")

print("""
Wiring it back in:
  The live tool embeds this data inside one <script> tag:  const BUNDLE= ... ;
  To update "BKN FFB 2026 Command Center.html", find
      const BUNDLE=
  and replace the JSON object after it with the contents of bundle.json
  (keep the leading `const BUNDLE=` and the trailing `;`). Save, reopen.
  Or just ask Claude to re-embed it for you.

  The Draft Guide / Draft Board HTML files are a static pre-season snapshot —
  this script only reads their schedule data and never overwrites them.
""")
