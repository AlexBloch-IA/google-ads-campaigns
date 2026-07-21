/**
 * budget-cap-guard.js — hard monthly spend cap, running OUTSIDE the agent.
 *
 * Paste into the ad account's native script runner (Tools -> Bulk actions -> Scripts),
 * authorize, schedule HOURLY. It runs in the account, on the vendor's scheduler, with no
 * dependency on your agent runtime. If the agent is down, looping, or wrong, this still fires.
 *
 * WHY THIS IS THE ONLY AUTOMATIC ACTION ALLOWED:
 *   It only ever SUBTRACTS. Pausing is safe to automate because a false positive costs one
 *   click to undo. Nothing whose worst case is "we spent more" belongs in an automatic path.
 *
 * READ BEFORE SCHEDULING — the two things this costs you:
 *
 *   1. BLAST RADIUS = THE WHOLE ACCOUNT. When the cap fires it pauses every ENABLED campaign
 *      in the account: brand, shopping, and anything a colleague created by hand — not just
 *      campaigns built from your specs. On a shared account, set CONFIG.NAME_CONTAINS to a
 *      naming prefix to scope it.
 *
 *   2. NO AUTOMATIC RECOVERY. Month spend resets on the 1st, but nothing here re-enables
 *      anything: re-activation is deliberately a human action. The failure mode is silent —
 *      "we stopped spending for nine days and nobody noticed". Put a calendar reminder next
 *      to this script. Recoverable in one click is only true if somebody knows to click.
 *
 * DATA IT WRITES: one row per hourly breach — timestamp, status, month spend, cap, action —
 * appended to a spreadsheet YOU own. No PII, no credential, no personal data. It appends
 * nothing while status is OK.
 *
 * RETENTION IS ENFORCED, NOT ANNOUNCED: every run, pruneOldAlerts() deletes alert rows older
 * than CONFIG.RETAIN_DAYS (default 400). A retention figure nothing executes is not a
 * retention policy. Set RETAIN_DAYS to 0 to keep rows forever — then the retention is yours
 * to run, and you have opted out deliberately rather than by omission.
 *
 * NO SECRETS HERE. The agent reads the sheet and relays the alert to whatever channel.
 * Never paste a token or webhook into a third-party script UI.
 *
 * TO FILL IN: CONFIG.SHEET_URL, CONFIG.HARD_CAP. Set your media budget UNDER the cap so the
 * buffer absorbs normal variance and the cap only fires on something genuinely wrong.
 */

var CONFIG = {
  HARD_CAP: 500,         // hard monthly cap, in the ACCOUNT's currency. No FX conversion.
  WARN_RATIO: 0.9,       // warn at 90%
  PAUSE_ON_CAP: true,    // cut at the cap
  NAME_CONTAINS: '',     // '' = every ENABLED campaign in the account. Set a prefix to scope.
  SHEET_URL: 'https://docs.google.com/spreadsheets/d/sheet-id-example/edit',
  ALERTS_TAB: 'alerts',
  RETAIN_DAYS: 400       // alert rows older than this are deleted every run. 0 = keep forever.
};

function main() {
  var cost = AdsApp.currentAccount().getStatsFor('THIS_MONTH').getCost();
  var cap = CONFIG.HARD_CAP;
  var status = 'OK';
  var action = '';

  if (cost >= cap) {
    status = 'CAP_REACHED';
    if (CONFIG.PAUSE_ON_CAP) { action = pauseAllEnabledCampaigns(); }
  } else if (cost >= cap * CONFIG.WARN_RATIO) {
    status = 'NEAR_CAP';
  }

  Logger.log(status + ' — month spend = ' + cost.toFixed(2) + ' / ' + cap + '. ' + action);
  if (status !== 'OK') { logAlert(status, cost, cap, action); }
  pruneOldAlerts();   // runs every hour, including on OK: retention must not depend on breaches
}

function pauseAllEnabledCampaigns() {
  // Scoped by name when NAME_CONTAINS is set; otherwise this is the entire account.
  var sel = AdsApp.campaigns().withCondition('campaign.status = "ENABLED"');
  if (CONFIG.NAME_CONTAINS) {
    sel = sel.withCondition('campaign.name CONTAINS "' + CONFIG.NAME_CONTAINS + '"');
  }
  var it = sel.get();
  var n = 0;
  while (it.hasNext()) { it.next().pause(); n++; }
  return 'PAUSED ' + n + ' campaign(s) — cap reached. Re-enabling is a human action.';
}

// Returns the alerts sheet, or null when SHEET_URL is still the placeholder. Never creates a
// spreadsheet — only a tab inside the one you already own and pointed it at.
function openAlertsSheet(createIfMissing) {
  if (!CONFIG.SHEET_URL || CONFIG.SHEET_URL.indexOf('sheet-id-example') !== -1) {
    return null;
  }
  var ss = SpreadsheetApp.openByUrl(CONFIG.SHEET_URL);
  var sh = ss.getSheetByName(CONFIG.ALERTS_TAB);
  if (!sh && createIfMissing) { sh = ss.insertSheet(CONFIG.ALERTS_TAB); }
  return sh || null;
}

function logAlert(status, cost, cap, action) {
  var sh = openAlertsSheet(true);
  if (!sh) {
    Logger.log('SHEET_URL not configured — alert not persisted.');
    return;
  }
  if (sh.getLastRow() === 0) {
    sh.appendRow(['date', 'status', 'month_spend', 'cap', 'action']);
  }
  sh.appendRow([new Date(), status, Math.round(cost * 100) / 100, cap, action]);
}

// Enforces CONFIG.RETAIN_DAYS. Deletes bottom-up so surviving row indices stay valid, and
// only ever touches rows below the header of the alerts tab it wrote itself. It never
// deletes a sheet, a tab, or anything it did not append.
function pruneOldAlerts() {
  if (!CONFIG.RETAIN_DAYS || CONFIG.RETAIN_DAYS <= 0) { return; }
  var sh = openAlertsSheet(false);
  if (!sh) { return; }
  var last = sh.getLastRow();
  if (last < 2) { return; }   // header only, or empty
  var cutoff = new Date().getTime() - CONFIG.RETAIN_DAYS * 24 * 60 * 60 * 1000;
  var dates = sh.getRange(2, 1, last - 1, 1).getValues();
  var removed = 0;
  for (var i = dates.length - 1; i >= 0; i--) {
    var d = dates[i][0];
    if (!(d instanceof Date) || isNaN(d.getTime())) { continue; }  // unparseable -> keep
    if (d.getTime() < cutoff) { sh.deleteRow(i + 2); removed++; }
  }
  if (removed) {
    Logger.log('retention: deleted ' + removed + ' alert row(s) older than '
               + CONFIG.RETAIN_DAYS + ' days.');
  }
}
