# Weekly report runbook

What happens each week to produce the "Week N 2026 Brooklyn FFB Report" draft.
A scheduled Claude task (`bkn-weekly-report`) runs these steps automatically every
Thursday morning, after voting closes Wednesday night; this file is also the
manual fallback. "Report week N" = the week whose games just
finished; the draft projects week N+1.

## 1. Poll ballots

**Week 3 on: the Apps Script ballot (`poll/`).** Managers vote Tuesday through
Wednesday 11:59 PM ET on their personal links, and `weekly_report.py` reads
the ballots (and who didn't vote) through the private export URL in
`secrets.local.json → poll_export_url`. Nothing to collect by hand. One-time
setup: [poll/SETUP.md](poll/SETUP.md). If the report prints
`no 'poll_export_url'`, that setup hasn't been finished.

**Weeks 0-2: Google Forms** (kept for history and reruns). These are
listed in `league_config.json → poll_sources` as response sheets read through
their public CSV export. Week 2's form was never linked to a sheet, so its
ballots were read out of the form editor in Chrome into
`data/polls/week_2.csv`; any `data/polls/week_N.csv` is picked up
automatically. The process: Responses, then Individual, read each response's
checked radio per team, and write the CSV in the response-sheet layout
(`Timestamp, Rankings [Pierce], Rankings [Owen], ...`).

## 2. FantasyPros League Analyzer power rankings (Chrome, needs your login)

1. Open https://www.fantasypros.com/nfl/myplaybook/league-analyzer.php with the
   **BKN FFB** league selected, then the **Power Rankings** tab.
2. The dropdown defaults to **Rest of Season**. Run the snippet below, then pick
   **Week {N+1}** in the dropdown and run it again.
3. Save both to `data/fantasypros/week_N.json` as
   `{"report_week": N, "week_view": {"label": "Week N+1", "teams": [...]}, "ros_view": {"label": "Rest of Season", "teams": [...]}}`.
   The report's "FantasyPros Ranking" column uses `week_view` (what the
   hand-built reports used); `ros_view` shows up in the notes.

```js
// run in the page (javascript tool); returns [{rank, team, score}, ...]
(() => {
  const t = [...document.querySelectorAll('table')].find(t => /\bSCORE\b/.test(t.innerText.split('\n')[0]));
  if (!t) return 'NO POWER RANKINGS TABLE - click the Power Rankings tab';
  const k = t.innerText.trim().split('\n').map(s => s.trim()).filter(Boolean), out = [];
  for (let i = 0; i < k.length; i++)
    if (/^\d+\.$/.test(k[i])) out.push({rank: parseInt(k[i]), team: k[i+1], score: parseInt(k[i+2])});
  return JSON.stringify(out);
})()
```

The page opens on Projected Standings; if clicking the "Power Rankings" tab by
element ref doesn't switch it, click it by screen coordinates.

Team names are Sleeper team names or handles; `weekly_report.py` maps them to
managers and warns about any it can't match.

## 3. Build

```bash
python3 weekly_report.py --refresh-bundle --copy-chart
```

Writes `data/history/week_N.json`, `reports/week_N_draft.html` (local preview),
`reports/week_N_gdoc.html` (the version to upload), `reports/week_N_chart.png`,
and `reports/dashboard.html`, and prints the table to the terminal. It is safe to
re-run as late ballots arrive; the Monte Carlo is seeded per week, so the same
inputs give the same odds.

The chart (`rank_chart.py`) tracks the Bozo Ranking week over week, with every
team's Sleeper logo as its marker. Use `--chart-metric consensus_rank` (or
`power_rank`, `fp_rank`, `standing`) to chart a different ranking. It needs
matplotlib + Pillow; without them the report still builds, just with no chart.
`--copy-chart` puts the PNG on the macOS clipboard for step 4.

## 4. Publish the draft

1. Upload `reports/week_N_gdoc.html` to Google Drive as a Google Doc titled
   `Week N 2026 BK FFB Report — DRAFT` (the HTML converts cleanly, tables and
   shaded boxes included).
2. The Drive upload can't carry the chart image (it would have to go through
   the connector call as ~600KB of base64), so the doc has a `[[RANK CHART]]`
   line right under the rankings table. Paste the chart over it: open the doc
   in Chrome, click at the end of the `[[RANK CHART]]` line, press End then
   Shift+Home to select it, confirm in a screenshot that only that line is
   highlighted, and press Cmd+V (the chart is on the clipboard from
   `--copy-chart`; re-copy with
   `osascript -e 'set the clipboard to (read (POSIX file "<abs path to png>") as «class PNGf»)'`).
   - Don't try Docs' Cmd+F to find the line: in Claude in Chrome, typed text
     goes into the document instead of the find box.
   - Click coordinates must come from a screenshot taken with `scale` set
     (its reported coordinate frame is the one clicks use); the unscaled
     screenshot's frame doesn't match.
   - If the image lands inside the yellow notes box, Cmd+Z and reselect.
3. Write the blurbs, delete the yellow notes boxes, fill the methodology line,
   and publish.

## Reference IDs

| What | ID |
|---|---|
| Sleeper league | `1392331238445973504` |
| Preseason responses sheet | `1F21LZlPNULX-Fr2qf0AYGcN0rwaqGlyhPjd-4Ze7_B8` |
| Week 1 responses sheet | `1PGHajDeUTi2SVaIqWNXOiGDRUyZBnJpgHodf4sjwzMY` |
| Week 2 form (no sheet linked) | `1-b7-AXeyIlMgU7cbE38i1D5e0ks5eFT9xxXk54QYHGo` |
| Week 3+ ballot | Apps Script web app, see `poll/SETUP.md` (URL + export key in `secrets.local.json`) |
