---
name: google-ads-campaigns
description: Ship Google Ads search campaigns as reviewable JSON specs — offline dry-run, atomic create, PAUSED in code. Use when an agent builds or edits a campaign. Trigger on "create a Google Ads campaign", "search campaign spec", "ads dry-run".
metadata: {"clawdbot":{"emoji":"💸","requires":{"bins":["python3"]}}}
---

# Google Ads Search Campaigns as Code

**The only automatic action tolerable on a system that spends money is the one that PREVENTS spending. Everything else is a proposal a human reads before it runs.**

A campaign is a versioned artifact: an agent writes the spec, a human reads the diff, one command creates it paused. Reference implementation: Google Ads API (`google-ads-python`). The shape is vendor-agnostic; "vendor" below means whichever ad network you point it at.

## Access, data, and network

| Item | Reality |
|---|---|
| Default mode | `--dry-run` — no client built, **no environment variable read at all**, no network call |
| Dry-run output | Carries no account id and no credential. Safe to paste into a ticket for review |
| Credentials | Env vars, read in `load_env()` on the mutating path only. Never hardcoded, never printed, never logged |
| Persisted by `ads-search.py` | Nothing. Specs are files you already own; the script writes no state |
| Persisted by `budget-cap-guard.js` (opt-in, runs in your ad account) | Appends one alert row per hourly breach to a spreadsheet **you** own: timestamp, status, spend, cap, action. No PII, no credential. Nothing is written while spend is under the cap |
| Retention (enforced, not announced) | `pruneOldAlerts()` deletes alert rows older than `CONFIG.RETAIN_DAYS` (default **400**) on every run, breach or not. `0` = keep forever, an explicit opt-out. A figure nothing executes is not a retention policy |
| Leaves the machine | Only on the mutating path: the campaign tree, to the ad network you authenticated against |
| Spend authority | None. Every campaign is created PAUSED. Going live is a human click in the vendor UI |
| Prerequisites (mutating path) | `pip install google-ads`; a manager (MCC) account; a developer token with **basic or standard** API access approved by the vendor — the default *test* level cannot write to a production account and approval takes days; an OAuth desktop client and refresh token generated elsewhere |
| Your obligations | Vendor ad policies, consumer-protection and advertising law apply to everything you publish. Regulated verticals (health, finance, legal, licensed trades) carry statutory constraints on top of vendor policy — clear the copy with the accountable human, not with this skill |

Setup for the mutating path only: `pip install google-ads`, then export `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID`. That is the whole list: the script declares no env var it does not consume. Conversion goals are an **account-level** setting — configure them in the vendor UI before you enable. The dry-run needs none of this.

## When to Use

| Trigger | Action |
|---|---|
| "build me a search campaign for X" | Write `<segment>.json` spec → dry-run → hand the plan to a human |
| "review the campaign before launch" | `--dry-run`, paste the output, wait for an explicit go |
| "create it / it's approved" | Run without `--dry-run`. Lands PAUSED. Say so out loud |
| "make it live" / "unpause" | **Refuse.** Point to the vendor UI. This skill has no enable path |
| "raise the budget" | Propose a spec diff. Never mutate budget on your own authority |
| "we're overspending" | Budget cap script (§10), not the agent |

## 1. The spec is the artifact

One JSON file per segment. One segment = one campaign = one ad group cluster = one landing page.

| Field | Type | Notes |
|---|---|---|
| `campaign` | string | Naming convention `<segment>_<region>_<channel>_<offer>`. Also the **idempotence key**. Validated against `[A-Za-z0-9_.\-]{1,120}` in code |
| `segment` | string | Slug, matches the file name |
| `final_url` | url | The segment's landing page. Ad copy and LP must say the same thing |
| `currency` | string | Descriptive only. Amounts are in the **account's** currency; nothing converts FX |
| `geo.country_code` | string | **Required with any geo name.** Zone names resolve *within one country* (§6) |
| `geo.locale` | string | Language of the zone names, e.g. `en` |
| `geo.include` / `geo.exclude` | string[] | Plain zone **names**, resolved read-only at apply time (§6) |
| `geo.worldwide` | bool | **The only way to target everywhere.** Requires `geo.note`. Omitting `geo.include` does *not* mean worldwide — it aborts (§6) |
| `geo.note` | string | Why these zones. Reviewers read this first |
| `language` | string | Maps to a language constant id |
| `bidding` | string | `MaximizeConversions`; add `target_cpa` only once there is data |
| `monthly_budget` | number | The number the client agreed to |
| `daily_budget` | number | **= monthly / 30.4.** Not /30, not /31 |
| `budget_share` | string | Share of the media budget, with a written rationale |
| `ad_schedule.account_timezone` | string | Windows are in the **account** timezone (§6) |
| `ad_schedule.windows[]` | object[] | `days[]`, `start`, `end` as `HH:MM` |
| `ad_groups[].keywords` | object | `exact[]` / `phrase[]` / `broad[]`, syntax `[exact]`, `"phrase"`, `broad` |
| `ad_groups[].rsa` | object | `headlines[]` 3–15, `descriptions[]` 2–4, `paths[]` ≤2. **Both bounds warn in the dry-run**; over-max is truncated on create, so the plan says how many will be dropped (§6) |
| `negative_keywords` | string[] | Campaign-level negatives (§8) |
| `extensions` | object | Sitelinks, callouts, structured snippets, call. Reviewed here, created in a later pass |

Complete worked spec: **`campaign.example.json`** (`acme-widgets_us_search_demo`, 300/month → 9.87/day, 44 negatives, deliberately unrounded schedule).

Budget split across segments needs a rationale in the file, not in a chat message: *"acme-core 45% — biggest volume and strongest intent; acme-satellite 20% — narrower market, fewer zones."* A share without a reason is a number nobody can review.

## 2. Sizing

The dry-run checks syntax, not sense. These are the thresholds it cannot enforce for you.

| Question | Answer | Why |
|---|---|---|
| Keywords per ad group | 5–20, one intent | The RSA must be relevant to all of them. 50 keywords = one of them is lying |
| Ad groups per campaign | 1–5 | They share the budget. More groups = each starves |
| Floor for `MaximizeConversions` | ~15–30 conversions/month | Below that, smart bidding has no signal and burns budget learning |
| When to add `target_cpa` | After ~30 conversions, never at launch | A tCPA with no data throttles delivery to nothing |
| Broad match at launch | No | See §8 |

## 3. The dry-run is offline by construction

```bash
python3 ads-search.py --spec campaign.example.json --dry-run
# Output (real, from a shell with no credentials and no SDK; elided for length):
# [ads-search] DRY-RUN — plan only. No client built, no env read, no network.
#   account            : (GOOGLE_ADS_CUSTOMER_ID, read at apply time — not here)
#   campaign           : acme-widgets_us_search_demo
#   channel            : SEARCH (content network OFF; search partners OFF)
#   STATUS             : PAUSED  <-- forced in code; no flag can change it
#   daily budget       : 9.87 -> 9870000 micros   (monthly 300)   [USD, no FX conversion]
#   geo include (1)   : United States
#     resolved within  : country US, locale en
#       - SATURDAY  10:00 -> 24:00
#       note: 10:07-23:59 rounded -> 10:00/24:00 (quarter-hour grid)
#   … (ad groups, keywords by match type, RSA limit checks elided)
#   campaign negatives (44) : free, cheap, price, prices, pricing, cost, how much, ...
# [ads-search] END DRY-RUN — nothing written.
```

`--dry-run` returns **before** `build_client()` is ever called. It reads no environment variable at all — `load_env()` is the only place this script touches the environment, and it sits past the dry-run return. The SDK import is behind a tolerant `try/except`, so the plan renders with `google-ads` not installed.

**This is architecture, not a convenience flag.** A full campaign — every keyword, every headline, every rounded slot, every micro — is reviewable by someone with **no secret provisioned at all**, and the output holds no account id, so it is safe to paste where the review happens. Keep the offline path pure: the moment a "harmless" env read sneaks in, you have re-coupled review to secrets and started printing identifiers into chat logs.

## 4. One atomic mutate, or nothing

The whole tree is wired with **negative temporary resource names** and sent as a single mutate:

```python
budget_rn   = f"customers/{cid}/campaignBudgets/-1"   # placeholders resolved server-side
campaign_rn = f"customers/{cid}/campaigns/-2"
c.campaign_budget = budget_rn        # -2 points at -1 inside the same request
g.campaign        = campaign_rn      # ad group -3 points at -2 ...
svc.mutate(customer_id=cid, mutate_operations=ops)   # all of it, or none of it
```

Order: budget → campaign → criteria (language, geo, schedule, negatives) → ad group → keywords → responsive search ad.

**Why this is the most expensive gotcha in the skill.** Create the tree in sequential calls and any failure halfway leaves a campaign with a budget and no ad — an orphan. Orphans are not inert: a campaign object with a budget attached is a live spending surface the moment anyone enables it, and it will not show up in "campaigns I meant to create." One mutate means one rollback boundary. Never "just retry the last step."

## 5. PAUSED is a constant, not an instruction

```python
c.status = enums.CampaignStatusEnum.PAUSED   # no flag, no env var, no argument reaches this
```

There is no `--enable`. Enabling is a human action in the vendor UI, by the person whose card is charged. **A guardrail that matters is on the left side of an assignment, not in a prompt.**

Ad groups are created `ENABLED` on purpose: the paused campaign gates delivery, so the tree is ready to serve the instant a human flips exactly one switch.

## 6. API traps

| Trap | Root cause | Fix |
|---|---|---|
| Ads appear on unrelated sites | Network flags default permissive | Set all four: search ON, `target_content_network=False`, `target_partner_search_network=False`, partners off unless reviewed |
| `INVALID_ARGUMENT` on geo | Zone **names** are not resource names | Resolve names → geo target constants with a **separate READ-ONLY call before** the mutate. Never resolve inside it |
| **Campaign silently targets the whole world** | Geo resolution is **fuzzy and scoped to one country**. A name unknown in that country returns **zero** constants, no error — and a campaign with no location criterion targets everywhere. The dry-run cannot catch it: it prints the name you typed | Set `geo.country_code` per spec. The script aborts the create when any requested **name** is not covered by a suggestion, and drops suggestions whose search term you never sent or that carry no term at all. It gates on term coverage, never on how many constants came back — one name can yield several constants and another zero, so a count check passes while a zone is silently dropped |
| Budget off by 10⁶ | API takes micros | `to_micros()`: `round(amount * 1_000_000)`. 9.87 → 9870000 |
| All keywords land BROAD | Syntax is in the string, not a field | Parse `[kw]`→EXACT, `"kw"`→PHRASE, else BROAD |
| Schedule rejected | **API only accepts quarter hours** | Floor the start, ceil the end, `23:59` → `24:00`. Direction matters: ceiling a start opens the day early, flooring an end silently truncates it |
| Ad rejected after create | Limits discovered at mutate time | Count characters in the dry-run: headline ≤30, description ≤90, path ≤15, 3–15 headlines, 2–4 descriptions |
| **Approved plan ≠ shipped ad** | The API caps an RSA at 15 headlines / 4 descriptions / 2 paths, so the create **truncates** the excess | `validate_rsa` warns on the **maxima**, not just the minima: a 20-headline plan prints `20 headlines > max 15 — 5 WILL BE DROPPED on create`. A count the human approved must never be silently cut at mutate time — the truncation and the warning read the same constants |
| **Campaign targets the world because a key was missing** | An **absent** `geo` block is not "no targeting", it is *everywhere*, and it passes a gate that only fires on names-without-a-country | Worldwide must be typed, never inherited from an omission: no `geo.include` ⇒ abort, unless the spec says `"geo": {"worldwide": true, "note": "<why>"}` — and the dry-run then prints `*** WORLDWIDE (explicitly requested) ***` |
| Schedule fires at wrong local hour | Windows are interpreted in the **account** timezone | Express every window in the account timezone. Two distant zones become **two overlapping windows in account time**, not two timezones |
| Mutate fails after passing every gate | Account id copied from the UI as `123-456-7890` | `load_env()` strips dashes and rejects a non-numeric id up front |

## 7. Idempotence

```python
# name is pre-validated against [A-Za-z0-9_.\-]{1,120}, so it cannot carry a quote into the literal
query = ("SELECT campaign.resource_name FROM campaign "
         f"WHERE campaign.name = '{name}' AND campaign.status != 'REMOVED' LIMIT 1")
# found -> print, return 0, create nothing
```

The campaign name is the key. Search before create, always. Replaying a run must never produce a second campaign — duplicates compete against each other in the same auction and both spend.

**The status filter is not decoration.** The campaign report still returns REMOVED campaigns. Without `!= 'REMOVED'`, deleting a broken campaign and re-running — the documented recovery move — matches the tombstone, prints "already present", creates nothing, and exits **0**. A successful-looking run that did nothing, exactly when you were recovering.

## 8. Negative keyword taxonomy

Six families that transfer to essentially any B2B account. Ship them at campaign level from day one — they cost nothing and each blocks a class of guaranteed-waste clicks.

| Family | Negatives |
|---|---|
| Price / free | free, cheap, price, prices, pricing, cost, how much, discount, coupon, promo |
| Jobs / training | job, jobs, hiring, salary, career, internship, training, course, certification, class |
| Spare parts / DIY | parts, spare parts, battery, replacement, repair, manual, user guide, how to use, diy, refurbished |
| Marketplaces | amazon, ebay, alibaba, aliexpress, craigslist, marketplace, wholesale directory |
| Consumer / informational | for home, personal use, definition, what is, wikipedia, forum |
| Out-of-coverage zones | Every zone you cannot serve, as a keyword **and** a geo exclusion — belt and braces |

These are **campaign-level negatives**, not a shared list: the script creates one `campaign_criterion` per negative, attached to this campaign only. Re-created in full for every new campaign. To mutualise them, build a `SharedSet` once and attach it with `CampaignSharedSet` — out of scope for this script.

Launch on `exact` + `phrase` only. Broad at launch spends the budget discovering that the taxonomy above was right.

## 9. Tool split

| Job | Tool | Gate |
|---|---|---|
| Read state, spend, metrics | Read-only API queries or a reporting connector | None — reading is free |
| Create / edit campaign, ad group, RSA, targeting | SDK script + spec | Human go on the dry-run |
| Pause / enable, set budget | Vendor UI or a narrow connector | Human, always |
| Cut at the budget cap | Vendor-native script (§10) | **None — the one automatic action** |
| Offline conversions, reporting | Vendor-native (data manager UI, scheduled scripts) | Independent of the agent |

Hybrid on purpose: write through the API where structure matters, keep caps and conversion ingestion native so they survive the agent being down, refactored, or wrong.

## 10. The budget cap lives outside the agent

`budget-cap-guard.js` runs **inside the ad account** on the vendor's own script runner, hourly:

```javascript
var CONFIG = { HARD_CAP: 500, WARN_RATIO: 0.9, PAUSE_ON_CAP: true, NAME_CONTAINS: '',
               SHEET_URL: 'https://docs.google.com/spreadsheets/d/sheet-id-example/edit' };
// spend >= cap  -> pause every ENABLED campaign, append a CAP_REACHED row to the 'alerts' tab
// spend >= 90%  -> append a NEAR_CAP row; the agent relays it
```

Three properties that make it trustworthy:

1. **It is not the agent.** You do not hand the brake to the driver. If the agent is broken, looping, or hallucinating a budget increase, the cap still fires — vendor scheduler, in the account, no dependency on your runtime.
2. **It only ever subtracts.** It pauses; it cannot raise. Automate nothing whose worst case is "we spent more."
3. **It holds no secret.** It writes a row to a sheet you own; the agent reads the sheet and relays. No token, no webhook, no credential in a script pasted into a third-party UI. `RETAIN_DAYS` prunes those rows on every run — the retention is code, not a sentence.

And two costs, because a cap sold as free is a cap nobody scopes:

- **Blast radius = the whole account.** It pauses *every* ENABLED campaign, including brand, shopping, and campaigns you never created. Set `NAME_CONTAINS` to a naming prefix on a shared account.
- **No automatic recovery.** Spend resets on the 1st; nothing re-enables. Re-activation is deliberately human — so put a calendar reminder next to it, or the failure is silent: "we stopped spending for nine days and nobody noticed."

Set the media budget under the cap (e.g. 450 against a 500 cap) so the buffer absorbs normal variance and the cap only fires on something genuinely wrong. Media is billed by the ad network to the payment method on the account, separate from any agency fee.

## 11. Editorial red lines

- **No prices, no "cheap", no "free"** in copy when quoting is a conversation. Those words are negatives — do not bid on what you refuse to say.
- **Regulated verticals** (health, finance, legal, and anything with a licensing regime) carry vendor policy *and* statutory constraints. Ad copy and landing page must match; an obligation that applies to one segment must not be implied for a segment it does not cover. The accountable human decides — not the agent, not this skill.

## Output format

Post this verbatim after every dry-run. It is the artifact a human approves. It contains no account id — do not add one; the plan is reviewable without it.

```
CAMPAIGN PLAN — [campaign_name]
Spec:      [path]              Segment: [segment]
Budget:    [monthly]/mo -> [daily]/day ([micros] micros)   Share: [share]
Geo:       +[include] / -[exclude] (country [cc])   Schedule: [N] slots, acct tz [tz]
Keywords:  [N] ([N] exact / [N] phrase / [N] broad)   Negatives: [N]
RSA:       [N] headlines / [N] descriptions — limits [OK / N warnings]
Rounding:  [none | HH:MM-HH:MM -> HH:MM/HH:MM]
STATUS ON CREATE: PAUSED (forced in code)
VERDICT: [✅ READY — awaiting human go / ⚠️ N warnings / ❌ blocked: reason]
```

Filled:

```
CAMPAIGN PLAN — acme-widgets_us_search_demo
Spec:      campaign.example.json     Segment: acme-widgets
Budget:    300/mo -> 9.87/day (9870000 micros)   Share: 100%
Geo:       +United States / -Hawaii (country US)   Schedule: 6 slots, acct tz America/New_York
Keywords:  6 (3 exact / 3 phrase / 0 broad)   Negatives: 44
RSA:       7 headlines / 3 descriptions — limits OK
Rounding:  10:07-23:59 -> 10:00/24:00
STATUS ON CREATE: PAUSED (forced in code)
VERDICT: ✅ READY — awaiting human go
```

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| "already present -> no create" | Idempotence gate hit on the campaign name | Expected on replay. Rename in the spec, or edit in the UI. If you deleted the campaign and this still fires, your gate is missing the `!= 'REMOVED'` filter (§7) |
| Second identical campaign appeared | Name changed between runs, gate missed it | Name is the key — keep it stable in the spec |
| Campaign live everywhere, plan said one country | Geo names did not resolve; no location criterion was created | §6. Set `geo.country_code`; the create now aborts instead of degrading to worldwide |
| Campaign exists, no ad, spending | Tree created in several calls, one failed | One mutate (§4). Delete the orphan, re-run — the REMOVED filter makes the re-run actually create |

## Scope

**This skill ONLY:** turns a JSON spec into a reviewable plan offline, with no env read and no identifier in the output · creates one campaign tree in one atomic mutate, always PAUSED · searches by name before creating · aborts rather than shipping unresolved or unstated-worldwide geo targeting · reports rounding, micros conversion, and every count or character limit that would change the ad between the plan and the create · documents a cap that lives outside the agent, writes only spend rows to a sheet you own, and prunes them on a retention it actually executes.

**This skill NEVER:** enables a campaign · raises a budget · edits live campaigns without a human go · touches display or performance-max inventory · writes copy for a regulated claim without a named human reviewer · hardcodes, prints, or transmits a credential or an account id · bypasses any vendor control, review queue, rate limit, or policy enforcement — if the platform blocks or disapproves something, that is the answer, and a human takes it from there.

---
Alexandre Bloch (Bloch Agents) · ClawHub @AlexBloch-IA
