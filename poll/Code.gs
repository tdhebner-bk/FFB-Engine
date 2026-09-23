/**
 * BKN FFB Power Poll — the weekly drag-to-rank ballot that replaces the Google Form.
 *
 * A Google Apps Script web app (free, runs under your Google account, voters need
 * no account). Each manager gets a personal link; they drag the 12 teams into
 * order and submit. One ballot per manager per week, editable until the deadline.
 * Ballots land in a private Google Sheet in the same layout the old form produced
 * ("Timestamp, Rankings [Pierce], ..."), plus a Week column, so weekly_report.py
 * reads them unchanged. Voters are stored only as a hash of their link, never by
 * name, so ballots stay anonymous; names are used only for the turnout list.
 *
 * Voting window: opens Tuesday 12:00 AM ET after each week's games, closes
 * Wednesday 11:59 PM ET. See poll/SETUP.md for the one-time setup.
 *
 * Endpoints (all on the web app URL):
 *   ?v=<token>                    a manager's ballot
 *   ?export=csv&key=<EXPORT_KEY>  every ballot as CSV (what weekly_report.py reads)
 *   ?status=json&key=<EXPORT_KEY>[&week=N]  turnout (who has / hasn't voted) for week N,
 *                                 or for the current/next poll if no week is given
 */

const CONFIG = {
  LEAGUE_ID: '1392331238445973504',
  WEEK1_POLL_OPENS: '2026-09-15',   // the Tuesday after Week 1 (report week 1)
  OPEN_DAYS: 2,                      // Tuesday + Wednesday
  FIRST_WEEK: 3,                     // first report week this ballot runs (weeks 0-2 used Google Forms)
  LAST_WEEK: 17,                     // through the fantasy playoffs
  SHOW_MISSING: true,                // show "still waiting on ..." names to voters
  // Sleeper user_id -> the name used in the report (same as league_config.json)
  MANAGERS: {
    '1392338811781926912': 'Thomas',
    '1264327218952142848': 'Josh',
    '647633216160788480': 'Tyler',
    '1002607743489466368': 'Blaise',
    '1391531353698230272': 'Owen',
    '755881537492422656': 'Luke O',
    '1397649927386996736': 'Ryan',
    '758113255997849600': 'Matt',
    '1268003048408481792': 'Kevin',
    '1395881766551572480': 'Pierce',
    '1397613556593143808': 'Ben',
    '1398039557844279296': 'Luke N',
  },
};
const TZ = 'America/New_York';
const SHEET_NAME = 'Responses';
const LINKS_SHEET = 'Links (private)';
const PROPS = PropertiesService.getScriptProperties();


// ---------------------------------------------------------------------------
// One-time setup (run from the editor; see SETUP.md)
// ---------------------------------------------------------------------------

/** Creates the responses spreadsheet, one private token per manager, and the export key. Safe to re-run. */
function setup() {
  let ss;
  const id = PROPS.getProperty('SHEET_ID');
  if (id) ss = SpreadsheetApp.openById(id);
  else {
    ss = SpreadsheetApp.create('BKN FFB Power Poll (Responses)');
    PROPS.setProperty('SHEET_ID', ss.getId());
  }
  ss.setSpreadsheetTimeZone(TZ);
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.getSheets()[0];
    sh.setName(SHEET_NAME);
  }
  if (sh.getLastRow() === 0) {
    sh.appendRow(header_());
    sh.setFrozenRows(1);
  }
  sh.getRange('A:A').setNumberFormat('@');          // keep timestamps as plain text
  if (!PROPS.getProperty('TOKENS')) {
    const tokens = {};
    Object.values(CONFIG.MANAGERS).forEach(name => { tokens[newToken_()] = name; });
    PROPS.setProperty('TOKENS', JSON.stringify(tokens));
  }
  if (!PROPS.getProperty('EXPORT_KEY')) PROPS.setProperty('EXPORT_KEY', newToken_() + newToken_());
  Logger.log('Setup done. Responses sheet: ' + ss.getUrl());
  Logger.log('Next: Deploy > New deployment > Web app, then run makeLinks().');
}

/** After deploying: writes everyone's personal link (and the pipeline's export link) to a private tab. */
function makeLinks() {
  const url = PROPS.getProperty('WEB_APP_URL') || ScriptApp.getService().getUrl();
  if (!url || !/\/exec$/.test(url)) {
    throw new Error('No /exec URL yet. Deploy as a web app first, or run setWebAppUrl("https://script.google.com/macros/s/.../exec").');
  }
  const ss = SpreadsheetApp.openById(PROPS.getProperty('SHEET_ID'));
  const sh = ss.getSheetByName(LINKS_SHEET) || ss.insertSheet(LINKS_SHEET);
  sh.clear();
  const tokens = JSON.parse(PROPS.getProperty('TOKENS'));
  const rows = [['Manager', 'Personal ballot link (send each person only their own)']];
  Object.keys(tokens).sort((a, b) => tokens[a].localeCompare(tokens[b]))
    .forEach(t => rows.push([tokens[t], url + '?v=' + t]));
  rows.push(['', '']);
  rows.push(['Pipeline export (keep private)', url + '?export=csv&key=' + PROPS.getProperty('EXPORT_KEY')]);
  rows.push(['Turnout (keep private)', url + '?status=json&key=' + PROPS.getProperty('EXPORT_KEY')]);
  sh.getRange(1, 1, rows.length, 2).setValues(rows);
  sh.setColumnWidth(1, 220); sh.setColumnWidth(2, 720);
  Logger.log('Links written to the "' + LINKS_SHEET + '" tab of ' + ss.getUrl());
}

/** Use if ScriptApp can't see the deployment URL: paste the /exec URL from Deploy > Manage deployments. */
function setWebAppUrl(url) { PROPS.setProperty('WEB_APP_URL', url); makeLinks(); }

/** Give one manager a fresh link (e.g. theirs got forwarded). Their old link stops working. */
function resetLink(name) {
  const tokens = JSON.parse(PROPS.getProperty('TOKENS'));
  Object.keys(tokens).forEach(t => { if (tokens[t] === name) delete tokens[t]; });
  tokens[newToken_()] = name;
  PROPS.setProperty('TOKENS', JSON.stringify(tokens));
  makeLinks();
}


// ---------------------------------------------------------------------------
// Web app
// ---------------------------------------------------------------------------

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.export || p.status) {
    if (!p.key || p.key !== PROPS.getProperty('EXPORT_KEY')) return text_('forbidden', ContentService.MimeType.TEXT);
    if (p.export) return text_(exportCsv_(), ContentService.MimeType.CSV);
    const win = p.week ? { week: Number(p.week), open: false } : pollWindow_(new Date());
    return text_(JSON.stringify(turnout_(win)), ContentService.MimeType.JSON);
  }
  const t = HtmlService.createTemplateFromFile('Ballot');
  t.token = String(p.v || '').replace(/[^a-z0-9]/gi, '');
  return t.evaluate()
    .setTitle('BKN FFB Power Poll')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** Called by the ballot page: everything it needs to render. */
function getBallot(token) {
  const voter = voterFor_(token);
  const win = pollWindow_(new Date());
  if (!voter) return { error: 'bad_link' };
  const teams = teams_();
  let saved = null;
  if (win.open) {
    const row = findRow_(win.week, hash_(token));
    if (row) saved = rowToOrder_(row.values);
  }
  return { voter, window: win, teams, saved, turnout: CONFIG.SHOW_MISSING ? turnout_(win) : null };
}

/** Called by the ballot page on submit. order = the 12 manager names, 1st to 12th. */
function submitBallot(token, order) {
  const voter = voterFor_(token);
  if (!voter) return { error: 'bad_link' };
  const win = pollWindow_(new Date());
  if (!win.open) return { error: 'closed', window: win };
  const names = Object.values(CONFIG.MANAGERS);
  if (!Array.isArray(order) || order.length !== names.length ||
      new Set(order).size !== names.length || order.some(n => names.indexOf(n) < 0)) {
    return { error: 'invalid' };
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = sheet_();
    const rank = {};
    order.forEach((n, i) => { rank[n] = ordinal_(i + 1); });
    const vals = [Utilities.formatDate(new Date(), TZ, 'M/d/yyyy H:mm:ss')]
      .concat(names.map(n => rank[n]), [win.week, hash_(token)]);
    const existing = findRow_(win.week, hash_(token));
    if (existing) sh.getRange(existing.row, 1, 1, vals.length).setValues([vals]);   // resubmit = replace
    else sh.appendRow(vals);
  } finally {
    lock.releaseLock();
  }
  return { ok: true, window: win, turnout: CONFIG.SHOW_MISSING ? turnout_(win) : null };
}



// ---------------------------------------------------------------------------
// Poll window (all dates in ET)
// ---------------------------------------------------------------------------

/** Which report week is (or is next) up for voting, and whether it's open right now. */
function pollWindow_(now) {
  const today = Utilities.formatDate(now, TZ, 'yyyy-MM-dd');
  const days = dayDiff_(CONFIG.WEEK1_POLL_OPENS, today);
  const cur = days >= 0 ? Math.floor(days / 7) + 1 : 0;
  const dow = ((days % 7) + 7) % 7;                                  // 0 = Tuesday
  const open = days >= 0 && dow < CONFIG.OPEN_DAYS && cur >= CONFIG.FIRST_WEEK && cur <= CONFIG.LAST_WEEK;
  let week = open ? cur : Math.max(CONFIG.FIRST_WEEK, cur + 1);
  const seasonOver = !open && week > CONFIG.LAST_WEEK;
  if (seasonOver) week = CONFIG.LAST_WEEK;
  const opens = addDays_(CONFIG.WEEK1_POLL_OPENS, (week - 1) * 7);
  const closes = addDays_(opens, CONFIG.OPEN_DAYS - 1);
  return {
    week, open, seasonOver,
    opensLabel: prettyDate_(opens) + ', 12:00 AM ET',
    closesLabel: prettyDate_(closes) + ', 11:59 PM ET',
  };
}

function dayDiff_(a, b) { return Math.round((isoUtc_(b) - isoUtc_(a)) / 86400000); }
function isoUtc_(s) { const p = s.split('-').map(Number); return Date.UTC(p[0], p[1] - 1, p[2]); }
function addDays_(iso, n) { return new Date(isoUtc_(iso) + n * 86400000).toISOString().slice(0, 10); }
function prettyDate_(iso) { return Utilities.formatDate(new Date(isoUtc_(iso) + 12 * 3600000), 'UTC', 'EEE MMM d'); }


// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

function header_() {
  return ['Timestamp'].concat(Object.values(CONFIG.MANAGERS).map(n => 'Rankings [' + n + ']'), ['Week', 'Voter']);
}

function sheet_() {
  return SpreadsheetApp.openById(PROPS.getProperty('SHEET_ID')).getSheetByName(SHEET_NAME);
}

function findRow_(week, voterHash) {
  const sh = sheet_();
  const n = sh.getLastRow();
  if (n < 2) return null;
  const width = header_().length;
  const values = sh.getRange(2, 1, n - 1, width).getValues();
  for (let i = values.length - 1; i >= 0; i--) {
    if (Number(values[i][width - 2]) === week && values[i][width - 1] === voterHash) return { row: i + 2, values: values[i] };
  }
  return null;
}

function rowToOrder_(values) {
  const names = Object.values(CONFIG.MANAGERS);
  return names.slice().sort((a, b) => parseInt(values[1 + names.indexOf(a)], 10) - parseInt(values[1 + names.indexOf(b)], 10));
}

function turnout_(win) {
  const tokens = JSON.parse(PROPS.getProperty('TOKENS') || '{}');
  const sh = sheet_();
  const n = sh.getLastRow();
  const width = header_().length;
  const voted = new Set();
  if (n >= 2) {
    sh.getRange(2, width - 1, n - 1, 2).getValues()
      .forEach(r => { if (Number(r[0]) === win.week) voted.add(r[1]); });
  }
  const names = Object.keys(tokens).map(t => ({ name: tokens[t], voted: voted.has(hash_(t)) }));
  return {
    week: win.week, open: win.open, total: names.length,
    voted: names.filter(x => x.voted).map(x => x.name).sort(),
    missing: names.filter(x => !x.voted).map(x => x.name).sort(),
  };
}

function exportCsv_() {
  const sh = sheet_();
  const values = sh.getDataRange().getDisplayValues();
  return values.map(r => r.map(c => /[",\n]/.test(c) ? '"' + String(c).replace(/"/g, '""') + '"' : c).join(',')).join('\n');
}

/** Teams in current Sleeper standings order, with logo and record. Cached 15 min. */
function teams_() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get('teams');
  if (hit) return JSON.parse(hit);
  const api = 'https://api.sleeper.app/v1/league/' + CONFIG.LEAGUE_ID;
  const users = JSON.parse(UrlFetchApp.fetch(api + '/users').getContentText());
  const rosters = JSON.parse(UrlFetchApp.fetch(api + '/rosters').getContentText());
  const byUser = {};
  users.forEach(u => { byUser[u.user_id] = u; });
  const teams = rosters.filter(r => CONFIG.MANAGERS[r.owner_id]).map(r => {
    const u = byUser[r.owner_id] || {};
    const meta = u.metadata || {};
    const s = r.settings || {};
    return {
      name: CONFIG.MANAGERS[r.owner_id],
      team: meta.team_name || u.display_name || '',
      logo: meta.avatar || (u.avatar ? 'https://sleepercdn.com/avatars/thumbs/' + u.avatar : ''),
      record: (s.wins || 0) + '-' + (s.losses || 0) + (s.ties ? '-' + s.ties : ''),
      wins: s.wins || 0,
      pf: (s.fpts || 0) + (s.fpts_decimal || 0) / 100,
    };
  }).sort((a, b) => (b.wins - a.wins) || (b.pf - a.pf));
  cache.put('teams', JSON.stringify(teams), 900);
  return teams;
}

function voterFor_(token) {
  if (!token) return null;
  const tokens = JSON.parse(PROPS.getProperty('TOKENS') || '{}');
  return tokens[token] || null;
}

function hash_(token) {
  const d = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, 'bkn-poll:' + token);
  return d.slice(0, 8).map(b => ((b + 256) % 256).toString(16).padStart(2, '0')).join('');
}

function newToken_() { return Utilities.getUuid().replace(/-/g, '').slice(0, 16); }

function ordinal_(n) {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function text_(body, mime) { return ContentService.createTextOutput(body).setMimeType(mime); }
