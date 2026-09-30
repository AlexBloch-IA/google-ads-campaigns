/**
 * budget-cap-guard.js — monthly spend cap, running OUTSIDE the agent. OPT-IN, LIVE ACCOUNT.
 *
 * Paste into the ad account's native script runner (Tools -> Bulk actions -> Scripts),
 * authorize, schedule HOURLY. It runs in the account, on the vendor's scheduler, with no
 * dependency on your agent runtime. A human installs it, reviews its scope, and switches the
 * pause on. The agent never installs, edits, or schedules it.
 *
 * WHAT IT DOES TO THE LIVE ACCOUNT — all of it:
 *   1. Reads this month's account spend (read-only).
 *   2. When spend >= HARD_CAP and PAUSE_ON_CAP is true: pauses the ENABLED campaigns whose
 *      name contains NAME_CONTAINS. Only those. There is no account-wide mode.
 *   3. Writes to ONE spreadsheet tab it owns: creates the tab if missing, appends one row per
 *      hourly breach (date, status, spend, cap, action, writer marker).
 *   4. Deletes rows from that tab older than RETAIN_DAYS — only rows carrying its own writer
 *      marker, only in a tab whose header row is exactly its own.
 *   It never raises a budget, never enables anything, never deletes a sheet or a tab.
 *
 * FAIL-CLOSED: an invalid or unfilled CONFIG, or a spend value that is not a finite number,
 * makes the run THROW before any pause or any sheet write. The script runner then reports a
 * failed execution. It never falls back to "every campaign", never treats unreadable spend
 * as zero, never writes into a tab it did not create.
 *
 * COSTS YOU ACCEPT BY TURNING THE PAUSE ON:
 *   - The cap is measured on WHOLE-ACCOUNT spend (conservative), the pause hits only the
 *     scoped campaigns. Other campaigns keep running.
 *   - No automatic recovery. Nothing re-enables on the 1st. Re-activation is a human action:
 *     put a calendar reminder next to this script, or the failure is silent.
 *
 * DATA: no PII, no credential, no token, no webhook. The agent reads the tab and relays.
 *
 * TO FILL IN (the run throws until all three are set): HARD_CAP, NAME_CONTAINS, SHEET_URL.
 * Then read the "scope:" line of a run log, and only then set PAUSE_ON_CAP to true.
 */

var CONFIG = {
  HARD_CAP: null,        // REQUIRED. Monthly cap > 0, in the ACCOUNT's currency. No FX.
  WARN_RATIO: 0.9,       // NEAR_CAP alert at 90% of the cap. Must be > 0 and < 1.
  NAME_CONTAINS: '',     // REQUIRED. Naming prefix of the campaigns it may pause, [A-Za-z0-9_.-]{3,120}.
  PAUSE_ON_CAP: false,   // OPT-IN. false = alert rows only. true = pause the scoped campaigns at the cap.
  SHEET_URL: '',         // REQUIRED. A dedicated spreadsheet you own: https://docs.google.com/spreadsheets/d/<id>/edit
  ALERTS_TAB: 'budget-cap-guard',  // tab this script creates and owns. Never point it at an existing tab.
  RETAIN_DAYS: 400       // integer >= 0. Marker rows older than this are deleted every run. 0 = keep forever.
};

var WRITER = 'budget-cap-guard/v2';
var HEADER = ['date', 'status', 'month_spend', 'cap', 'action', 'writer'];
var NAME_RE = /^[A-Za-z0-9_.\-]{3,120}$/;
var SHEET_RE = /^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_\-]{20,}\/edit/;

function isFiniteNumber(v) { return typeof v === 'number' && isFinite(v); }

// Returns a list of problems. Empty list = valid. Every check is a type check first:
// a string "500", a null, or a missing key is a refusal, never a coercion.
function validateConfig(c) {
  if (c === null || typeof c !== 'object') { return ['CONFIG is not an object']; }
  var e = [];
  if (!isFiniteNumber(c.HARD_CAP) || c.HARD_CAP <= 0) { e.push('HARD_CAP must be a number > 0'); }
  if (!isFiniteNumber(c.WARN_RATIO) || c.WARN_RATIO <= 0 || c.WARN_RATIO >= 1) { e.push('WARN_RATIO must be a number in (0, 1)'); }
  if (typeof c.NAME_CONTAINS !== 'string' || !NAME_RE.test(c.NAME_CONTAINS)) { e.push('NAME_CONTAINS must match ' + NAME_RE + ' — there is no account-wide mode'); }
  if (typeof c.PAUSE_ON_CAP !== 'boolean') { e.push('PAUSE_ON_CAP must be true or false'); }
  if (typeof c.SHEET_URL !== 'string' || !SHEET_RE.test(c.SHEET_URL)) { e.push('SHEET_URL must be a Google Sheets edit URL'); }
  if (typeof c.ALERTS_TAB !== 'string' || !NAME_RE.test(c.ALERTS_TAB)) { e.push('ALERTS_TAB must match ' + NAME_RE); }
  if (!isFiniteNumber(c.RETAIN_DAYS) || c.RETAIN_DAYS < 0 || Math.floor(c.RETAIN_DAYS) !== c.RETAIN_DAYS) { e.push('RETAIN_DAYS must be an integer >= 0'); }
  return e;
}

function main() {
  var problems = validateConfig(CONFIG);
  if (problems.length) {
    throw new Error('budget-cap-guard REFUSED to run (nothing paused, nothing written): ' + problems.join('; '));
  }
  var cost = AdsApp.currentAccount().getStatsFor('THIS_MONTH').getCost();
  if (!isFiniteNumber(cost) || cost < 0) {
    throw new Error('budget-cap-guard REFUSED: month spend is not a finite number >= 0 (' + cost + ')');
  }
  var cap = CONFIG.HARD_CAP;
  var status = 'OK';
  var action = '';

  Logger.log('scope: ' + countScoped() + ' ENABLED campaign(s) contain "' + CONFIG.NAME_CONTAINS
             + '"; pause ' + (CONFIG.PAUSE_ON_CAP ? 'ON' : 'OFF (alert only)'));

  if (cost >= cap) {
    status = 'CAP_REACHED';
    action = CONFIG.PAUSE_ON_CAP ? pauseScopedCampaigns() : 'pause OFF — alert only';
  } else if (cost >= cap * CONFIG.WARN_RATIO) {
    status = 'NEAR_CAP';
  }

  Logger.log(status + ' — month spend = ' + cost.toFixed(2) + ' / ' + cap + '. ' + action);
  if (status !== 'OK') { logAlert(status, cost, cap, action); }
  pruneOldAlerts();   // every run, including OK: retention must not depend on breaches
}

function scopedSelector() {
  // NAME_CONTAINS is validated against NAME_RE: it cannot carry a quote into the condition.
  return AdsApp.campaigns()
    .withCondition('campaign.status = "ENABLED"')
    .withCondition('campaign.name CONTAINS "' + CONFIG.NAME_CONTAINS + '"');
}

function countScoped() {
  var it = scopedSelector().get();
  var n = 0;
  while (it.hasNext()) { it.next(); n++; }
  return n;
}

function pauseScopedCampaigns() {
  var it = scopedSelector().get();
  var names = [];
  while (it.hasNext()) {
    var c = it.next();
    c.pause();
    names.push(c.getName());
  }
  return 'PAUSED ' + names.length + ' campaign(s) [' + names.join(', ') + '] — re-enabling is a human action.';
}

function headerIsOurs(sh) {
  if (sh.getLastRow() < 1 || sh.getLastColumn() !== HEADER.length) { return false; }
  var row = sh.getRange(1, 1, 1, HEADER.length).getValues()[0];
  for (var i = 0; i < HEADER.length; i++) { if (row[i] !== HEADER[i]) { return false; } }
  return true;
}

// Returns the tab this script owns, or null. A tab that already exists with any other
// header — including an empty tab someone created — is not ours: refuse to write or prune.
function openAlertsSheet(createIfMissing) {
  var ss = SpreadsheetApp.openByUrl(CONFIG.SHEET_URL);
  var sh = ss.getSheetByName(CONFIG.ALERTS_TAB);
  if (!sh) {
    if (!createIfMissing) { return null; }
    sh = ss.insertSheet(CONFIG.ALERTS_TAB);
    sh.appendRow(HEADER);
    return sh;
  }
  if (!headerIsOurs(sh)) {
    Logger.log('tab "' + CONFIG.ALERTS_TAB + '" exists but its header is not ours — refusing to write or prune it.');
    return null;
  }
  return sh;
}

function logAlert(status, cost, cap, action) {
  var sh = openAlertsSheet(true);
  if (!sh) {
    throw new Error('budget-cap-guard: alert ' + status + ' NOT persisted — point ALERTS_TAB at a tab name that does not exist yet.');
  }
  sh.appendRow([new Date(), status, Math.round(cost * 100) / 100, cap, action, WRITER]);
}

// Enforces RETAIN_DAYS. Deletes bottom-up so surviving row indices stay valid. A row is
// deleted only if its writer column is this script's marker AND its date parses AND it is
// older than the cutoff. Anything else in the tab is kept.
function pruneOldAlerts() {
  if (CONFIG.RETAIN_DAYS === 0) { return; }
  var sh = openAlertsSheet(false);
  if (!sh) { return; }
  var last = sh.getLastRow();
  if (last < 2) { return; }
  var cutoff = new Date().getTime() - CONFIG.RETAIN_DAYS * 24 * 60 * 60 * 1000;
  var rows = sh.getRange(2, 1, last - 1, HEADER.length).getValues();
  var removed = 0;
  for (var i = rows.length - 1; i >= 0; i--) {
    var d = rows[i][0];
    if (rows[i][HEADER.length - 1] !== WRITER) { continue; }             // not ours -> keep
    if (!(d instanceof Date) || isNaN(d.getTime())) { continue; }        // unparseable -> keep
    if (d.getTime() < cutoff) { sh.deleteRow(i + 2); removed++; }
  }
  if (removed) {
    Logger.log('retention: deleted ' + removed + ' alert row(s) older than ' + CONFIG.RETAIN_DAYS + ' days.');
  }
}
