// Hostile-fixture tests for budget-cap-guard.js. Run: node tests/budget-cap-guard.test.js
// Mocks the vendor runtime (AdsApp, SpreadsheetApp, Logger). Not part of the published artifact.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'budget-cap-guard.js'), 'utf8');
const DAY = 24 * 3600 * 1000;
const GOOD = {
  HARD_CAP: 500, WARN_RATIO: 0.9, NAME_CONTAINS: 'acme_', PAUSE_ON_CAP: true,
  SHEET_URL: 'https://docs.google.com/spreadsheets/d/AAAAAAAAAAAAAAAAAAAAAAAA/edit',
  ALERTS_TAB: 'budget-cap-guard', RETAIN_DAYS: 400,
};

function makeSheet(rows) {
  const s = {
    rows: rows.map((r) => r.slice()), deleted: 0, appended: 0,
    getLastRow() { return s.rows.length; },
    getLastColumn() { return s.rows.reduce((m, r) => Math.max(m, r.length), 0); },
    getRange(r, c, nr, nc) { return { getValues: () => s.rows.slice(r - 1, r - 1 + nr).map((x) => { const y = x.slice(c - 1, c - 1 + nc); while (y.length < nc) y.push(''); return y; }) }; },
    appendRow(row) { s.rows.push(row); if (row[row.length - 1] === 'budget-cap-guard/v2') s.appended++; },
    deleteRow(i) { s.rows.splice(i - 1, 1); s.deleted++; },
  };
  return s;
}

function run(opts) {
  const { config, campaigns, tab } = opts;
  const cost = 'cost' in opts ? opts.cost : 600;
  const camps = (campaigns || [
    { name: 'acme_search_a', status: 'ENABLED' }, { name: 'acme_search_b', status: 'ENABLED' },
    { name: 'brand_core', status: 'ENABLED' }, { name: 'acme_old', status: 'PAUSED' },
  ]).map((c) => ({ ...c, paused: false }));
  const tabs = {};
  const logs = [];
  const ctx = {
    Logger: { log: (m) => logs.push(String(m)) },
    AdsApp: {
      currentAccount: () => ({ getStatsFor: () => ({ getCost: () => cost }) }),
      campaigns: () => {
        const conds = [];
        const sel = {
          withCondition(c) { conds.push(c); return sel; },
          get() {
            const m = camps.filter((c) => conds.every((cond) => {
              let x = cond.match(/campaign\.status = "(\w+)"/); if (x) return c.status === x[1];
              x = cond.match(/campaign\.name CONTAINS "(.*)"/); if (x) return c.name.includes(x[1]);
              throw new Error('unexpected condition ' + cond);
            }));
            let i = 0;
            return { hasNext: () => i < m.length, next: () => { const c = m[i++]; return { pause() { c.paused = true; }, getName: () => c.name }; } };
          },
        };
        return sel;
      },
    },
    SpreadsheetApp: { openByUrl: () => ({
      getSheetByName: (n) => tabs[n] || null,
      insertSheet: (n) => { tabs[n] = makeSheet([]); return tabs[n]; },
    }) },
  };
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  // Sheets returns Date objects from the script's own realm: rebuild fixture dates there.
  const CtxDate = vm.runInContext('Date', ctx);
  if (tab !== undefined) tabs[GOOD.ALERTS_TAB] = makeSheet(tab.map((r) => r.map((v) => (v instanceof Date ? new CtxDate(v.getTime()) : v))));
  if (config !== '__KEEP__') ctx.CONFIG = config;
  let error = null;
  try { ctx.main(); } catch (e) { error = e.message; }
  const t = tabs[GOOD.ALERTS_TAB];
  return { error, paused: camps.filter((c) => c.paused).map((c) => c.name), appended: t ? t.appended : 0, deleted: t ? t.deleted : 0, tab: t, logs };
}

let fail = 0;
function check(label, res, expect) {
  const problems = [];
  if (expect.refused !== undefined && Boolean(res.error) !== expect.refused) problems.push(`refused=${Boolean(res.error)}`);
  if (expect.paused !== undefined && JSON.stringify(res.paused) !== JSON.stringify(expect.paused)) problems.push(`paused=${JSON.stringify(res.paused)}`);
  if (expect.appended !== undefined && res.appended !== expect.appended) problems.push(`appended=${res.appended}`);
  if (expect.deleted !== undefined && res.deleted !== expect.deleted) problems.push(`deleted=${res.deleted}`);
  const ok = problems.length === 0;
  if (!ok) fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${res.error ? '  -> ' + res.error.slice(0, 110) : ''}${ok ? '' : '  [' + problems.join(', ') + ']'}`);
}
const NOTHING = { refused: true, paused: [], appended: 0, deleted: 0 };
const cfg = (patch) => ({ ...GOOD, ...patch });
const without = (k) => { const c = { ...GOOD }; delete c[k]; return c; };

console.log('--- shipped CONFIG, unfilled ---');
check('shipped defaults (HARD_CAP null, NAME_CONTAINS "", SHEET_URL "")', run({ config: '__KEEP__' }), NOTHING);

console.log('--- CONFIG absent / empty / null / wrong type ---');
check('CONFIG undefined', run({ config: undefined }), NOTHING);
check('CONFIG null', run({ config: null }), NOTHING);
check('CONFIG {}', run({ config: {} }), NOTHING);
check('CONFIG "string"', run({ config: 'HARD_CAP=500' }), NOTHING);
check('CONFIG []', run({ config: [] }), NOTHING);

console.log('--- HARD_CAP ---');
for (const v of [undefined, null, 0, -1, '500', NaN, Infinity, true]) check(`HARD_CAP=${String(v)}`, run({ config: cfg({ HARD_CAP: v }) }), NOTHING);
check('HARD_CAP key missing', run({ config: without('HARD_CAP') }), NOTHING);

console.log('--- NAME_CONTAINS (no account-wide mode) ---');
for (const v of [undefined, null, '', '  ', 'ab', 123, ['acme_'], 'acme" OR campaign.name CONTAINS "', 'a'.repeat(121)]) check(`NAME_CONTAINS=${JSON.stringify(v)}`, run({ config: cfg({ NAME_CONTAINS: v }) }), NOTHING);
check('NAME_CONTAINS key missing', run({ config: without('NAME_CONTAINS') }), NOTHING);

console.log('--- PAUSE_ON_CAP / WARN_RATIO / SHEET_URL / ALERTS_TAB / RETAIN_DAYS ---');
for (const v of [undefined, null, 'true', 1]) check(`PAUSE_ON_CAP=${JSON.stringify(v)}`, run({ config: cfg({ PAUSE_ON_CAP: v }) }), NOTHING);
for (const v of [undefined, null, 0, 1, 1.5, '0.9']) check(`WARN_RATIO=${JSON.stringify(v)}`, run({ config: cfg({ WARN_RATIO: v }) }), NOTHING);
for (const v of [undefined, null, '', 'https://docs.google.com/spreadsheets/d/sheet-id-example/edit', 'https://evil.example/x', 42]) check(`SHEET_URL=${JSON.stringify(v)}`, run({ config: cfg({ SHEET_URL: v }) }), NOTHING);
for (const v of [undefined, null, '', 'Sheet1"', 7]) check(`ALERTS_TAB=${JSON.stringify(v)}`, run({ config: cfg({ ALERTS_TAB: v }) }), NOTHING);
for (const v of [undefined, null, -1, 1.5, '400', NaN]) check(`RETAIN_DAYS=${JSON.stringify(v)}`, run({ config: cfg({ RETAIN_DAYS: v }) }), NOTHING);

console.log('--- spend value from the vendor ---');
for (const v of [undefined, null, NaN, '600', -1, Infinity, {}]) check(`cost=${String(v)}`, run({ config: GOOD, cost: v }), NOTHING);

console.log('--- spreadsheet ownership ---');
check('tab exists with foreign header -> refuse write, no pause lost', run({ config: GOOD, tab: [['name', 'email'], ['x', 'y']] }), { refused: true, paused: ['acme_search_a', 'acme_search_b'], appended: 0, deleted: 0 });
check('tab exists but empty (someone else\'s) -> refuse write', run({ config: GOOD, tab: [] }), { refused: true, appended: 0, deleted: 0 });
const old = new Date(Date.now() - 500 * DAY), fresh = new Date(Date.now() - 10 * DAY);
const H = ['date', 'status', 'month_spend', 'cap', 'action', 'writer'];
const r = run({ config: GOOD, cost: 100, tab: [H, [old, 'NEAR_CAP', 460, 500, '', 'budget-cap-guard/v2'], [old, 'foreign row', 1, 1, '', ''], [old, 'forged', 1, 1, '', 'someone-else'], ['not a date', 'X', 1, 1, '', 'budget-cap-guard/v2'], [fresh, 'NEAR_CAP', 470, 500, '', 'budget-cap-guard/v2']] });
check('prune: deletes only old rows with our marker (1 of 5)', r, { refused: false, paused: [], appended: 0, deleted: 1 });
check('prune: foreign-header tab is never pruned', run({ config: GOOD, cost: 100, tab: [['date', 'x'], [old, 'y']] }), { refused: false, deleted: 0, appended: 0 });
check('RETAIN_DAYS=0 keeps everything', run({ config: cfg({ RETAIN_DAYS: 0 }), cost: 100, tab: [H, [old, 'NEAR_CAP', 1, 1, '', 'budget-cap-guard/v2']] }), { refused: false, deleted: 0 });

console.log('--- valid behaviour ---');
check('cap reached, pause ON -> only ENABLED campaigns containing prefix', run({ config: GOOD, cost: 600 }), { refused: false, paused: ['acme_search_a', 'acme_search_b'], appended: 1 });
check('cap reached, pause OFF (shipped default) -> alert row, no pause', run({ config: cfg({ PAUSE_ON_CAP: false }), cost: 600 }), { refused: false, paused: [], appended: 1 });
check('near cap -> alert row, no pause', run({ config: GOOD, cost: 460 }), { refused: false, paused: [], appended: 1 });
check('under cap -> nothing written', run({ config: GOOD, cost: 100 }), { refused: false, paused: [], appended: 0 });
check('cost exactly 0 -> OK', run({ config: GOOD, cost: 0 }), { refused: false, paused: [], appended: 0 });

console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL PASS');
process.exit(fail ? 1 : 0);
