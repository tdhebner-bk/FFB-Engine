# BKN FFB Power Poll: one-time setup

The weekly ballot is a Google Apps Script web app: it runs free under your Google
account, and voters don't need an account. Each manager gets a personal link
that works all season. They drag the 12 teams into order and submit. Voting
opens **Tuesday 12:00 AM ET** and closes **Wednesday 11:59 PM ET**. Starting
with Week 3, `weekly_report.py` reads the ballots straight from it.

Setup takes about 10 minutes, once.

## 1. Create the script

1. Go to <https://script.google.com> and click **New project**. Rename it
   (top left) to `BKN FFB Power Poll`.
2. Replace everything in `Code.gs` with the contents of [`poll/Code.gs`](Code.gs).
3. Click **+** next to Files, choose **HTML**, name it `Ballot` (no `.html`),
   and replace its contents with [`poll/Ballot.html`](Ballot.html).
4. Save (Cmd+S).

## 2. Run `setup` and approve permissions

1. In the toolbar's function dropdown pick **setup**, then click **Run**.
2. Google asks for permission: **Review permissions**, pick your account.
   Because this is your own unverified script, Google shows "Google hasn't
   verified this app". Click **Advanced**, then **Go to BKN FFB Power Poll
   (unsafe)**, then **Allow**. The script asks for:
   - Google Sheets: to create and write its responses spreadsheet
   - external requests: to read team names, logos and records from Sleeper
3. The log shows the new spreadsheet's URL: **BKN FFB Power Poll (Responses)**
   in your Drive. It stays private; nothing needs to be shared.

## 3. Deploy the web app

1. **Deploy**, then **New deployment**. Click the gear and choose **Web app**.
2. Set **Execute as: Me** and **Who has access: Anyone**. "Anyone" is what lets
   leaguemates open their link without signing in. A link only works with a
   valid personal code, and the export needs a secret key.
3. Click **Deploy** and copy the **Web app URL** (it ends in `/exec`).

## 4. Generate the links

1. Pick **makeLinks** in the function dropdown and **Run**. If it says there's
   no `/exec` URL, add a temporary line at the bottom of Code.gs:
   `function tmp() { setWebAppUrl('PASTE_THE_EXEC_URL'); }`, then run `tmp` and
   delete the line.
2. Open the responses spreadsheet and go to the **Links (private)** tab: one
   personal ballot link per manager, plus two private rows for the pipeline.

## 5. Hook up the pipeline

Tell Claude the poll is deployed; it can read the Links tab and set this up.
To do it yourself, copy the **Pipeline export** link into `secrets.local.json`:

```json
"poll_export_url": "https://script.google.com/macros/s/.../exec?export=csv&key=..."
```

## 6. Send the links

Send each manager **only their own** link, once. It's the same link every
week. Before Tuesday the page shows when voting opens. If a link gets
forwarded around, run `resetLink("Name")` (e.g. with a temporary
`function tmp() { resetLink('Pierce'); }`) to issue that person a new one.

## Good to know

- **Anonymous:** ballots are stored against a hash of the voter's link, never
  their name. You hand out the links, so you could in theory work out who's
  who, but nobody else can. Names are only used for the "waiting on ..."
  turnout list, which voters see after submitting (set
  `SHOW_MISSING: false` in `Code.gs` to hide it).
- **Changing your mind:** resubmitting before the deadline replaces that
  voter's ballot for the week.
- **Starting order:** the ballot opens in current Sleeper standings order, or
  the voter's saved ballot if they've already voted this week.
- **Updating the code later:** paste the new files, then **Deploy**, **Manage
  deployments**, the pencil icon, **Version: New version**, **Deploy**. The URL
  and everyone's links stay the same.
- **Settings** (top of `Code.gs`): `FIRST_WEEK` (3), `LAST_WEEK` (17),
  `OPEN_DAYS` (2 = Tue + Wed), and the manager list, which must match
  `league_config.json`.
