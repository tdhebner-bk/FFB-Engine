# BKN FFB — Fantasy Football Tools

Draft prep and in-season decision tools for the BKN FFB Sleeper league (12-team,
half-PPR, superflex). Two tiers: a **static** pre-season draft reference, and a
**live** engine that re-blends rankings from multiple sources on demand.

## Architecture

| Tier | Files | What it does |
|---|---|---|
| Static (frozen pre-season) | `BKN FFB 2026 Draft Guide.html`, `2026 1QB Draft Board (Pick 5).html` | Self-contained draft-day tools with rankings baked in as of early September 2026. Never regenerated — a snapshot of the board on draft day. |
| Live (all season) | `build_board_data.py` → `bundle.json` → `ffb_engine.py` / `BKN FFB 2026 Command Center.html` | Re-fetches and re-blends rankings from live sources every time you run it. This is what you use week-to-week. |

The static files are plain HTML with the data embedded in a `<script>` tag —
open them in a browser, no server needed. The live side is a small Python
pipeline: `build_board_data.py` fetches fresh rankings and writes `bundle.json`;
`ffb_engine.py` reads that bundle to project lineups, matchups, and standings
from the terminal. The Command Center HTML can be re-fed the same `bundle.json`
to get a live in-browser dashboard (paste its contents over the `const BUNDLE=`
line in that file).

## Requirements

Python 3.8+, standard library only — no `pip install` needed.

## Usage

```bash
python3 build_board_data.py     # fetch + blend live rankings -> bundle.json
python3 ffb_engine.py           # this week's lineup, matchups, power rankings, odds
python3 ffb_engine.py --week 5  # a specific week
python3 ffb_engine.py --sims 20000   # tighter Monte Carlo season simulation
```

Re-run `build_board_data.py` any time during the season to pull fresh numbers —
there's no caching or scheduling built in, just run it before you check lineups.

## Where the rankings come from

Each player's positional rank is a weighted blend, matching how the Draft Board
itself is built:

- **Primary analyst (Yahoo)** — 50% — public, no login (FantasyPros partner API)
- **FantasyPros consensus ECR** — 30% — public, no login (146-expert panel, superflex half-PPR)
- **The Athletic** — 20% — public, no login (the same widget The Athletic embeds on its own site)

Weights renormalize when a player is missing from one source. The original
board also folds in a 10% Reception Perception overlay for charted WRs — that
data is subscriber-only with no public feed, so it isn't reproduced here.
Validated against the frozen pre-season board, this 3-source blend lands on
its exact per-position rank ~57% of the time and within one spot ~86% of the
time.

D/ST uses a simpler blend: straight average of the primary analyst's D/ST rank
and FantasyPros' D/ST consensus ECR.

**Reference-only fields** (carried through per player, never affecting the
blended rank or the point projection):

- **PFF grades** — needs your own premium.pff.com session (paywalled). See
  [Optional: PFF grades](#optional-pff-grades) below. Skipped gracefully if not configured.
- **ESPN O-line win rates** — ESPN's analytics article sits behind bot-detection
  that blocks plain scripts outright (not a login wall). `espn_winrates_2025.json`
  is a manually-refreshed snapshot of their team pass/run block win rates —
  ask Claude to re-browse the source URL in that file's header to update it.

## Optional: PFF grades

1. Log into [premium.pff.com](https://premium.pff.com).
2. Open DevTools → Network tab, visit any `/nfl/positions/.../passing` (or
   rushing/receiving) page, and click a request to `facet/.../summary`.
3. Copy the `x-pff-token` request header value.
4. Copy `secrets.example.json` to `secrets.local.json` and paste it in as `pff_token`.

This token is short-lived (tied to your active session) — when `build_board_data.py`
starts printing "PFF ... unavailable", repeat the steps above to refresh it.
`secrets.local.json` is gitignored and never leaves your machine.

## League config

The Sleeper league ID and your Sleeper user ID are hardcoded near the top of
both `build_board_data.py` and `ffb_engine.py` (`LID`, `MY_USER_ID`) — swap
those to point at a different league. Both are public Sleeper identifiers,
not secrets.

## Files

- `build_board_data.py` — live rankings fetch + blend, writes `bundle.json`
- `ffb_engine.py` — projections, optimal lineups, matchups, Monte Carlo standings
- `bundle.json` — generated output, gitignored (regenerate any time)
- `espn_winrates_2025.json` — cached ESPN team win-rate snapshot (see above)
- `secrets.example.json` — template for the optional PFF token; copy to `secrets.local.json`
- `BKN FFB 2026 Draft Guide.html`, `2026 1QB Draft Board (Pick 5).html` — static pre-season draft tools
- `BKN FFB 2026 Command Center.html` — live in-browser dashboard (fed by `bundle.json`)
