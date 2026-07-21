#!/usr/bin/env python3
"""
ads-search.py — build one paid search campaign from a versioned JSON spec.

Reference implementation for the `google-ads-campaigns` skill. The vendor here is
the main search ad network (google-ads-python SDK); the shape is vendor-agnostic:
spec in -> plan -> (dry-run report | one atomic create).

TWO MODES, and only one of them touches the network:

  --dry-run   Renders the full plan and exits. Does NOT build the API client, does NOT
              read a single environment variable, makes zero network calls. This is the
              mode a human reviews. It runs on a laptop with no secret provisioned at all,
              and its output is safe to paste: no account id, no credential, no identifier.

  (no flag)   Mutating path. Requires env vars below. Never run this without a human
              "go" on the dry-run output first.

GUARANTEES ENFORCED IN CODE (not in a prompt):
  * Campaign status is PAUSED, always, on create. There is no flag to make it ENABLED.
    Going live is a separate human action in the ad network UI.
  * Search only: content network and partner search network are hard-off.
  * The whole tree is created in ONE atomic mutate. A partial create would leave an
    orphan campaign with no ad — and campaigns spend.
  * Idempotent: a campaign with the same name is never created twice.

PREREQUISITES (mutating path only — the dry-run needs none of this):
  * pip install google-ads
  * A Google Ads manager (MCC) account, and a developer token with BASIC or STANDARD
    API access approved by the vendor. The default TEST access level cannot write to a
    production account, and approval takes days. Budget for that before you plan a launch.
  * An OAuth desktop client and a refresh token, both generated outside this skill.

ENV (mutating path only; read only after --dry-run has been ruled out; never hardcode,
never print, never log):
  GOOGLE_ADS_DEVELOPER_TOKEN
  GOOGLE_ADS_CLIENT_ID
  GOOGLE_ADS_CLIENT_SECRET
  GOOGLE_ADS_REFRESH_TOKEN
  GOOGLE_ADS_LOGIN_CUSTOMER_ID   manager account id (dashes are stripped)
  GOOGLE_ADS_CUSTOMER_ID         target account id (dashes are stripped)

The conversion action this campaign optimises toward is an ACCOUNT-level setting, not a
campaign field this script writes. Configure conversion goals in the vendor UI before you
enable. This script deliberately declares no env var it does not consume.

Usage:
  ads-search.py --spec campaign.example.json --dry-run     # offline, no auth
  ads-search.py --spec campaign.example.json               # atomic create, PAUSED
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
except ImportError:  # dry-run, --help and lint must work without the SDK installed
    GoogleAdsClient = None
    GoogleAdsException = Exception  # type: ignore[assignment,misc]

REQUIRED_ENV = [
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "GOOGLE_ADS_CUSTOMER_ID",
]

LANGUAGE_CONSTANTS = {"english": "1000", "french": "1002", "spanish": "1003", "german": "1001"}
DEFAULT_LANGUAGE_CONSTANT = "1000"

# Search partners are off by default: you did not review those placements.
TARGET_SEARCH_PARTNERS = False

RSA_HEADLINE_MAX, RSA_DESCRIPTION_MAX, RSA_PATH_MAX = 30, 90, 15
RSA_MIN_HEADLINES, RSA_MIN_DESCRIPTIONS = 3, 2
# Maxima are enforced in validate_rsa (warning in the plan) AND applied in build_operations
# (truncation at mutate). Both read these constants: a plan that says 15 ships 15.
RSA_MAX_HEADLINES, RSA_MAX_DESCRIPTIONS, RSA_MAX_PATHS = 15, 4, 2

# The campaign name is interpolated into a GAQL literal (find_existing_campaign). Validating
# it against the documented naming convention is cheaper and safer than escaping it.
CAMPAIGN_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,120}")

DAYS = {"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"}
MATCH_MARKER = {"EXACT": "[ ]", "PHRASE": '" "', "BROAD": "   "}
_MIN_ENUM = {0: "ZERO", 15: "FIFTEEN", 30: "THIRTY", 45: "FORTY_FIVE"}
_MIN_INT = {v: k for k, v in _MIN_ENUM.items()}


# --- pure helpers: no network, no secret, shared by both modes ------------------------

def to_micros(amount: float) -> int:
    """Account-currency unit -> micros (1 unit = 1_000_000 micros). No FX conversion happens
    anywhere in this script: amounts are whatever currency the account is billed in."""
    return int(round(float(amount) * 1_000_000))


def parse_keyword(raw: str) -> tuple[str, str]:
    """'[kw]' -> (kw, EXACT) ; '"kw"' -> (kw, PHRASE) ; 'kw' -> (kw, BROAD)."""
    s = raw.strip()
    if len(s) >= 2 and s.startswith("[") and s.endswith("]"):
        return s[1:-1].strip(), "EXACT"
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1].strip(), "PHRASE"
    return s, "BROAD"


def hhmm_to_slot(value: str, *, is_end: bool) -> tuple[int, str, bool]:
    """
    'HH:MM' -> (hour 0-24, minute enum name, was_rounded).
    The API only accepts quarter hours. Start floors, end ceils, 23:59 -> 24:00.
    Rounding an end DOWN would silently shorten your day; rounding a start UP would
    silently open it early. Direction is not cosmetic.
    """
    hh, mm = value.split(":")
    hour, minute, rounded = int(hh), int(mm), False
    if is_end:
        q = math.ceil(minute / 15) * 15
        if q == 60:
            hour, q = hour + 1, 0
        if q != minute:
            rounded = True
        minute = q
        if hour >= 24:
            if hour != 24 or minute != 0:
                rounded = True
            hour, minute = 24, 0
    else:
        q = (minute // 15) * 15
        if q != minute:
            rounded = True
        minute = q
    return hour, _MIN_ENUM[minute], rounded


def validate_rsa(headlines: list[str], descriptions: list[str], paths: list[str]) -> list[str]:
    """Character limits are counted here, not discovered at mutate time. Warns, never raises.

    Counts BOTH bounds. The maxima are not cosmetic: build_operations truncates to
    RSA_MAX_HEADLINES / RSA_MAX_DESCRIPTIONS / 2 paths. Without a warning here, a human
    approves a plan showing 20 headlines and the mutate ships 15 — the plan-vs-artifact
    divergence this whole skill exists to prevent. Truncating what a human approved must be
    visible in the plan, never a silent act at mutate time.
    """
    w: list[str] = []
    if len(headlines) < RSA_MIN_HEADLINES:
        w.append(f"{len(headlines)} headline(s) < min {RSA_MIN_HEADLINES}")
    if len(headlines) > RSA_MAX_HEADLINES:
        w.append(f"{len(headlines)} headlines > max {RSA_MAX_HEADLINES} — "
                 f"{len(headlines) - RSA_MAX_HEADLINES} WILL BE DROPPED on create")
    if len(descriptions) < RSA_MIN_DESCRIPTIONS:
        w.append(f"{len(descriptions)} description(s) < min {RSA_MIN_DESCRIPTIONS}")
    if len(descriptions) > RSA_MAX_DESCRIPTIONS:
        w.append(f"{len(descriptions)} descriptions > max {RSA_MAX_DESCRIPTIONS} — "
                 f"{len(descriptions) - RSA_MAX_DESCRIPTIONS} WILL BE DROPPED on create")
    if len(paths) > RSA_MAX_PATHS:
        w.append(f"{len(paths)} paths > max {RSA_MAX_PATHS} — "
                 f"{len(paths) - RSA_MAX_PATHS} WILL BE DROPPED on create")
    for h in headlines:
        if len(h) > RSA_HEADLINE_MAX:
            w.append(f"headline > {RSA_HEADLINE_MAX} ({len(h)}): {h!r}")
    for d in descriptions:
        if len(d) > RSA_DESCRIPTION_MAX:
            w.append(f"description > {RSA_DESCRIPTION_MAX} ({len(d)}): {d!r}")
    for p in paths:
        if len(p) > RSA_PATH_MAX:
            w.append(f"path > {RSA_PATH_MAX} ({len(p)}): {p!r}")
    return w


def normalize_schedule(sched: dict | None) -> tuple[list[dict], list[str]]:
    """Flatten windows into day x slot criteria. Returns (criteria, rounding notes)."""
    crits: list[dict] = []
    notes: list[str] = []
    if not sched:
        return crits, notes
    for win in sched.get("windows", []):
        sh, sm, r1 = hhmm_to_slot(win["start"], is_end=False)
        eh, em, r2 = hhmm_to_slot(win["end"], is_end=True)
        if r1 or r2:
            notes.append(f"{win['start']}-{win['end']} rounded -> {sh:02d}:{_MIN_INT[sm]:02d}"
                         f"/{eh:02d}:{_MIN_INT[em]:02d} (quarter-hour grid)")
        for day in win.get("days", []):
            d = day.strip().upper()
            if d not in DAYS:
                notes.append(f"unknown day skipped: {day!r}")
                continue
            crits.append({"day": d, "sh": sh, "sm": sm, "eh": eh, "em": em})
    return crits, notes


def build_plan(spec: dict) -> dict:
    """Spec -> normalized plan. Pure. This is what the human actually reviews."""
    for field in ("campaign", "final_url", "daily_budget"):
        if not spec.get(field):
            sys.exit(f"[ads-search] spec missing required field: {field}")
    if not CAMPAIGN_NAME_RE.fullmatch(spec["campaign"]):
        sys.exit(f"[ads-search] campaign name must match {CAMPAIGN_NAME_RE.pattern} "
                 f"(naming convention <segment>_<region>_<channel>_<offer>): {spec['campaign']!r}")
    groups = []
    for ag in spec.get("ad_groups", []):
        kw = ag.get("keywords", {})
        raw = list(kw.get("exact", [])) + list(kw.get("phrase", [])) + list(kw.get("broad", []))
        rsa = ag.get("rsa", {})
        groups.append({
            "name": ag.get("name") or spec["campaign"],
            "keywords": [parse_keyword(k) for k in raw],
            "headlines": list(rsa.get("headlines", [])),
            "descriptions": list(rsa.get("descriptions", [])),
            "paths": list(rsa.get("paths", [])),
        })
    if not groups:
        sys.exit("[ads-search] spec has no ad_groups")
    geo = spec.get("geo", {}) or {}
    if (geo.get("include") or geo.get("exclude")) and not geo.get("country_code"):
        # Zone names are resolved by the vendor *within one country*. Guessing the country
        # is how "Texas" silently resolves to nothing and the campaign targets the world.
        sys.exit("[ads-search] spec has geo names but no geo.country_code (e.g. \"US\", \"GB\")")
    if not geo.get("include") and not geo.get("worldwide"):
        # Worldwide must be TYPED, never inherited from an omission. A campaign with no
        # location criterion targets everywhere — the single most expensive default in the
        # vendor's API. The abort above only fires when names exist without a country; an
        # absent geo block would sail through every gate and spend globally on a plan whose
        # reviewer simply never saw the word. Same principle as RETAIN_DAYS=0: an expensive
        # choice is legitimate, but it is a sentence someone wrote, not a key they forgot.
        sys.exit("[ads-search] spec has no geo.include: a campaign with no location criterion "
                 "targets THE WHOLE WORLD. Add geo.include + geo.country_code, or state it "
                 "explicitly: \"geo\": {\"worldwide\": true, \"note\": \"<why>\"}")
    if geo.get("worldwide") and not (geo.get("note") or "").strip():
        sys.exit("[ads-search] geo.worldwide requires geo.note explaining why worldwide is intended")
    lang = (spec.get("language") or "english").strip().lower()
    return {
        "campaign_name": spec["campaign"],
        "segment": spec.get("segment"),
        "final_url": spec["final_url"],
        "daily_budget": float(spec["daily_budget"]),
        "budget_micros": to_micros(spec["daily_budget"]),
        "monthly_budget": spec.get("monthly_budget"),
        "target_cpa": spec.get("target_cpa"),
        "currency": spec.get("currency", "(account currency)"),
        "language": lang,
        "language_constant": LANGUAGE_CONSTANTS.get(lang, DEFAULT_LANGUAGE_CONSTANT),
        "geo_include": list(geo.get("include", [])),
        "geo_exclude": list(geo.get("exclude", [])),
        "geo_worldwide": bool(geo.get("worldwide")),
        "geo_country": (geo.get("country_code") or "").strip().upper(),
        "geo_locale": (geo.get("locale") or "en").strip().lower(),
        "geo_note": geo.get("note"),
        "ad_schedule": spec.get("ad_schedule"),
        "ad_groups": groups,
        "negatives": list(spec.get("negative_keywords", [])),
        "extensions": spec.get("extensions", {}) or {},
    }


# --- dry-run: offline by construction -------------------------------------------------

def render_dry_run(plan: dict) -> int:
    """Pure. Takes the plan and nothing else: no env, no client, no network, no identifier.
    That is what makes the output safe to paste into a ticket or a chat for review."""
    print("=" * 74)
    print("[ads-search] DRY-RUN — plan only. No client built, no env read, no network.")
    print("=" * 74)
    print("  account            : (GOOGLE_ADS_CUSTOMER_ID, read at apply time — not here)")
    print(f"  campaign           : {plan['campaign_name']}")
    print(f"  channel            : SEARCH (content network OFF; "
          f"search partners {'ON' if TARGET_SEARCH_PARTNERS else 'OFF'})")
    print("  STATUS             : PAUSED  <-- forced in code; no flag can change it")
    bid = "MaximizeConversions"
    if plan["target_cpa"] is not None:
        bid += f" + tCPA {plan['target_cpa']} ({to_micros(plan['target_cpa'])} micros)"
    print(f"  bidding            : {bid}")
    monthly = f"   (monthly {plan['monthly_budget']})" if plan.get("monthly_budget") else ""
    print(f"  daily budget       : {plan['daily_budget']:.2f} -> {plan['budget_micros']} micros{monthly}"
          f"   [{plan['currency']}, no FX conversion]")
    print(f"  final_url          : {plan['final_url']}")
    print("  conversion action  : (account-level setting — this script sets none; configure goals in the UI)")
    print(f"  language           : {plan['language']} -> languageConstants/{plan['language_constant']}")
    if plan.get("geo_worldwide"):
        print("  geo include        : *** WORLDWIDE (explicitly requested in the spec) ***")
        print("                       no location criterion — this campaign can serve in every country")
    else:
        print(f"  geo include ({len(plan['geo_include'])})   : {', '.join(plan['geo_include']) or '(none)'}")
    print(f"  geo exclude ({len(plan['geo_exclude'])})   : {', '.join(plan['geo_exclude']) or '(none)'}")
    if plan["geo_include"] or plan["geo_exclude"]:
        print(f"    resolved within  : country {plan['geo_country']}, locale {plan['geo_locale']}")
    if plan.get("geo_note"):
        print(f"    note             : {plan['geo_note']}")
    print("    (names resolve to geo target constants via a READ-ONLY call at apply time;")
    print("     a name that does not resolve in that country ABORTS the create — see §6)")

    crits, notes = normalize_schedule(plan["ad_schedule"])
    if plan["ad_schedule"]:
        tz = plan["ad_schedule"].get("account_timezone", "?")
        print(f"  ad schedule        : {len(crits)} criterion(s) — ACCOUNT timezone {tz}")
        for c in crits:
            print(f"      - {c['day']:<9} {c['sh']:02d}:{_MIN_INT[c['sm']]:02d}"
                  f" -> {c['eh']:02d}:{_MIN_INT[c['em']]:02d}")
        for n in notes:
            print(f"      note: {n}")
    else:
        print("  ad schedule        : (none — serving 24/7)")

    total = 0
    for ag in plan["ad_groups"]:
        print("-" * 74)
        print(f"  ad group           : {ag['name']}")
        by_type: dict[str, int] = {}
        for _t, mt in ag["keywords"]:
            by_type[mt] = by_type.get(mt, 0) + 1
        total += len(ag["keywords"])
        print(f"    keywords ({len(ag['keywords'])})     : "
              + ", ".join(f"{n} {t}" for t, n in sorted(by_type.items())))
        for text, mt in ag["keywords"]:
            print(f"        {MATCH_MARKER[mt]} {text}")
        print(f"    RSA              : {len(ag['headlines'])} headlines / "
              f"{len(ag['descriptions'])} descriptions"
              + (f" / paths {ag['paths']}" if ag["paths"] else ""))
        warns = validate_rsa(ag["headlines"], ag["descriptions"], ag["paths"])
        for w in warns:
            print(f"        WARNING {w}")
        if not warns:
            print(f"        (limits OK: {RSA_MIN_HEADLINES}-{RSA_MAX_HEADLINES} headlines, "
                  f"{RSA_MIN_DESCRIPTIONS}-{RSA_MAX_DESCRIPTIONS} descriptions, "
                  f"<={RSA_MAX_PATHS} paths; chars <={RSA_HEADLINE_MAX}/{RSA_DESCRIPTION_MAX}/{RSA_PATH_MAX}"
                  f" — nothing dropped on create)")

    print("-" * 74)
    print(f"  campaign negatives ({len(plan['negatives'])}) : {', '.join(plan['negatives']) or '(none)'}")
    print(f"  TOTAL positive keywords : {total}")
    ext = plan["extensions"]
    if ext:
        print("-" * 74)
        print("  extensions (assets — shown for review, NOT mutated by this script):")
        for key in ("sitelinks", "callouts"):
            if ext.get(key):
                print(f"      {key:<20}: {len(ext[key])}")
        if ext.get("structured_snippets"):
            ss = ext["structured_snippets"]
            print(f"      structured snippets : {ss.get('header')} = {', '.join(ss.get('values', []))}")
        if ext.get("call"):
            print(f"      call extension      : {ext['call'].get('phone')} ({ext['call'].get('country')})")
    print("=" * 74)
    print("[ads-search] END DRY-RUN — nothing written. Real create = same command without --dry-run,")
    print("             only after a human has read this plan and said go.")
    return 0


# --- mutating path --------------------------------------------------------------------

def load_env() -> dict:
    """The ONLY place this script reads the environment, and it is never reached on --dry-run."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        sys.exit(f"[ads-search] missing env vars: {', '.join(missing)}")
    env = {k: os.environ[k].strip() for k in REQUIRED_ENV}
    # The UI shows account ids as 123-456-7890; the API wants digits. Copy-pasting the UI
    # form otherwise fails deep in the mutate, after the idempotence and geo calls.
    for key in ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_ADS_CUSTOMER_ID"):
        env[key] = env[key].replace("-", "").replace(" ", "")
        if not env[key].isdigit():
            sys.exit(f"[ads-search] {key} must be an account id in digits (dashes are fine)")
    return env


def build_client(env: dict):
    if GoogleAdsClient is None:
        sys.exit("[ads-search] SDK not installed: pip install google-ads")
    return GoogleAdsClient.load_from_dict({
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": env["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
        "use_proto_plus": True,
    })


def find_existing_campaign(client, customer_id: str, name: str) -> str | None:
    """Idempotence gate. The name is already validated against CAMPAIGN_NAME_RE in build_plan,
    so it cannot carry a quote or a backslash into this literal.

    REMOVED campaigns are excluded on purpose: the campaign report still returns them, and
    without this filter a re-run after deleting a bad campaign matches the tombstone, prints
    'already present', creates nothing, and exits 0 — a silent no-op exactly when you are
    trying to recover.
    """
    svc = client.get_service("GoogleAdsService")
    query = ("SELECT campaign.resource_name, campaign.name FROM campaign "
             f"WHERE campaign.name = '{name}' AND campaign.status != 'REMOVED' LIMIT 1")
    for row in svc.search(customer_id=customer_id, query=query):
        return row.campaign.resource_name
    return None


def resolve_geo(client, names: list[str], *, locale: str,
                country_code: str) -> tuple[list[str], set[str]]:
    """READ-ONLY name -> geo target constants. Runs BEFORE the mutate, never inside it.

    Returns (resource_names, matched_search_terms). The second element is what the caller
    gates on — NOT len(resource_names). Suggestion matching is fuzzy and NOT 1:1: one search
    term can yield several constants and another can yield zero, so counting constants
    against counting requested names is a gate that fails OPEN (2 constants for one term
    + 0 for another == 2 == len(names) -> no abort, one zone silently dropped, one extra
    zone silently targeted). Coverage of the requested terms is the only sound check.

    A suggestion with an empty search term is dropped, not kept: it cannot be attributed to
    anything the human approved. A geo criterion that quietly fails to be added does not
    mean 'no targeting', it means 'the whole world'.
    """
    if not names:
        return [], set()
    svc = client.get_service("GeoTargetConstantService")
    req = client.get_type("SuggestGeoTargetConstantsRequest")
    req.locale = locale
    req.country_code = country_code
    req.location_names.names.extend(names)
    wanted = {n.strip().casefold() for n in names}
    out: list[str] = []
    matched: set[str] = set()
    for s in svc.suggest_geo_target_constants(request=req).geo_target_constant_suggestions:
        term = (getattr(s, "suggestion_search_term", "") or "").strip().casefold()
        if not term or term not in wanted:
            continue  # unattributable, or a zone nobody asked for
        if s.geo_target_constant and s.geo_target_constant.resource_name:
            out.append(s.geo_target_constant.resource_name)
            matched.add(term)
    return out, matched


def build_operations(client, customer_id: str, plan: dict,
                     geo_in: list[str], geo_out: list[str]) -> list:
    """Whole tree as operations, wired by negative temp resource names, for ONE mutate."""
    enums = client.enums

    def rn(coll: str, tmp: int) -> str:
        return f"customers/{customer_id}/{coll}/{tmp}"

    budget_rn, campaign_rn = rn("campaignBudgets", -1), rn("campaigns", -2)
    ops: list = []

    op = client.get_type("MutateOperation")
    b = op.campaign_budget_operation.create
    b.resource_name = budget_rn
    b.name = f"{plan['campaign_name']} - budget"
    b.amount_micros = plan["budget_micros"]
    b.delivery_method = enums.BudgetDeliveryMethodEnum.STANDARD
    b.explicitly_shared = False
    ops.append(op)

    op = client.get_type("MutateOperation")
    c = op.campaign_operation.create
    c.resource_name = campaign_rn
    c.name = plan["campaign_name"]
    c.advertising_channel_type = enums.AdvertisingChannelTypeEnum.SEARCH
    c.status = enums.CampaignStatusEnum.PAUSED  # absolute guard: never ENABLED from code
    c.campaign_budget = budget_rn
    c.maximize_conversions = client.get_type("MaximizeConversions")
    if plan.get("target_cpa") is not None:
        c.maximize_conversions.target_cpa_micros = to_micros(plan["target_cpa"])
    c.network_settings.target_google_search = True
    c.network_settings.target_search_network = TARGET_SEARCH_PARTNERS
    c.network_settings.target_content_network = False
    c.network_settings.target_partner_search_network = False
    ops.append(op)

    op = client.get_type("MutateOperation")
    crit = op.campaign_criterion_operation.create
    crit.campaign = campaign_rn
    crit.language.language_constant = f"languageConstants/{plan['language_constant']}"
    ops.append(op)

    for gtc in geo_in:
        op = client.get_type("MutateOperation")
        k = op.campaign_criterion_operation.create
        k.campaign = campaign_rn
        k.location.geo_target_constant = gtc
        ops.append(op)

    for gtc in geo_out:
        op = client.get_type("MutateOperation")
        k = op.campaign_criterion_operation.create
        k.campaign = campaign_rn
        k.negative = True
        k.location.geo_target_constant = gtc
        ops.append(op)

    for s in normalize_schedule(plan["ad_schedule"])[0]:
        op = client.get_type("MutateOperation")
        k = op.campaign_criterion_operation.create
        k.campaign = campaign_rn
        k.ad_schedule.day_of_week = getattr(enums.DayOfWeekEnum, s["day"])
        k.ad_schedule.start_hour = s["sh"]
        k.ad_schedule.start_minute = getattr(enums.MinuteOfHourEnum, s["sm"])
        k.ad_schedule.end_hour = s["eh"]
        k.ad_schedule.end_minute = getattr(enums.MinuteOfHourEnum, s["em"])
        ops.append(op)

    for neg in plan["negatives"]:
        op = client.get_type("MutateOperation")
        k = op.campaign_criterion_operation.create
        k.campaign = campaign_rn
        k.negative = True
        k.keyword.text = neg
        k.keyword.match_type = enums.KeywordMatchTypeEnum.BROAD
        ops.append(op)

    tmp = -3
    for ag in plan["ad_groups"]:
        ag_rn = rn("adGroups", tmp)
        tmp -= 1
        op = client.get_type("MutateOperation")
        g = op.ad_group_operation.create
        g.resource_name = ag_rn
        g.name = ag["name"]
        g.campaign = campaign_rn
        g.type_ = enums.AdGroupTypeEnum.SEARCH_STANDARD
        g.status = enums.AdGroupStatusEnum.ENABLED  # harmless: the PAUSED campaign gates delivery
        ops.append(op)

        for text, mt in ag["keywords"]:
            op = client.get_type("MutateOperation")
            k = op.ad_group_criterion_operation.create
            k.ad_group = ag_rn
            k.status = enums.AdGroupCriterionStatusEnum.ENABLED
            k.keyword.text = text
            k.keyword.match_type = getattr(enums.KeywordMatchTypeEnum, mt)
            ops.append(op)

        op = client.get_type("MutateOperation")
        a = op.ad_group_ad_operation.create
        a.ad_group = ag_rn
        a.status = enums.AdGroupAdStatusEnum.ENABLED
        a.ad.final_urls.append(plan["final_url"])
        rsa = a.ad.responsive_search_ad
        for h in ag["headlines"][:RSA_MAX_HEADLINES]:
            asset = client.get_type("AdTextAsset")
            asset.text = h
            rsa.headlines.append(asset)
        for d in ag["descriptions"][:RSA_MAX_DESCRIPTIONS]:
            asset = client.get_type("AdTextAsset")
            asset.text = d
            rsa.descriptions.append(asset)
        paths = ag.get("paths", [])[:RSA_MAX_PATHS]  # excess already warned in validate_rsa
        if len(paths) >= 1 and paths[0]:
            rsa.path1 = paths[0]
        if len(paths) >= 2 and paths[1]:
            rsa.path2 = paths[1]
        ops.append(op)

    return ops


def apply_campaign(client, customer_id: str, plan: dict) -> int:
    existing = find_existing_campaign(client, customer_id, plan["campaign_name"])
    if existing:
        print(f"[ads-search] already present ({existing}) -> no create (idempotent).")
        return 0
    geo_args = {"locale": plan["geo_locale"], "country_code": plan["geo_country"]}
    geo_in, in_matched = resolve_geo(client, plan["geo_include"], **geo_args)
    geo_out, out_matched = resolve_geo(client, plan["geo_exclude"], **geo_args)
    want_in = {n.strip().casefold() for n in plan["geo_include"]}
    want_out = {n.strip().casefold() for n in plan["geo_exclude"]}
    print(f"[ads-search] geo resolved in {plan['geo_country'] or '(n/a)'}: "
          f"include={len(in_matched)}/{len(want_in)} name(s) -> {len(geo_in)} constant(s), "
          f"exclude={len(out_matched)}/{len(want_out)} name(s) -> {len(geo_out)} constant(s)")
    # A zone name that did not resolve must stop the create. Falling through would ship a
    # campaign with no location criterion at all — i.e. targeting the whole world on a plan a
    # human approved because it said "United States". Gate on TERM COVERAGE, never on list
    # length: see resolve_geo — counting constants is a gate that fails open.
    unresolved = sorted((want_in - in_matched) | (want_out - out_matched))
    if unresolved:
        sys.exit(f"[ads-search] geo names did not resolve in country {plan['geo_country']!r}: "
                 f"{', '.join(repr(n) for n in unresolved)}. "
                 "Refusing to create: an unresolved include would mean worldwide targeting. "
                 "Fix geo.country_code or the zone names in the spec.")
    ops = build_operations(client, customer_id, plan, geo_in, geo_out)
    print(f"[ads-search] atomic create of '{plan['campaign_name']}' — "
          f"{len(ops)} operation(s), status=PAUSED.")
    svc = client.get_service("GoogleAdsService")
    resp = svc.mutate(customer_id=customer_id, mutate_operations=ops)
    for r in resp.mutate_operation_responses:
        for field in ("campaign_budget_result", "campaign_result", "ad_group_result",
                      "campaign_criterion_result", "ad_group_criterion_result",
                      "ad_group_ad_result"):
            res = getattr(r, field)
            if res and getattr(res, "resource_name", ""):
                print(f"    created: {res.resource_name}")
                break
    print("[ads-search] Campaign created PAUSED. Going live is a human action in the UI.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Create one paid search campaign (SEARCH, PAUSED) from a JSON spec.")
    p.add_argument("--spec", required=True, help="path to the campaign spec JSON")
    p.add_argument("--dry-run", action="store_true",
                   help="render the plan and exit: no client, no auth, no network")
    args = p.parse_args()

    path = Path(args.spec)
    if not path.is_file():
        sys.exit(f"[ads-search] spec not found: {args.spec}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[ads-search] invalid JSON ({args.spec}): {exc}")

    plan = build_plan(spec)

    if args.dry_run:
        # Offline on purpose, and pure on purpose: nothing below this line has run, no
        # environment variable has been touched. A plan is reviewable on any machine, with
        # zero secret provisioned — and the output carries no account identifier to leak.
        return render_dry_run(plan)

    # --- everything past this point is the mutating path ---
    env = load_env()
    client = build_client(env)
    try:
        return apply_campaign(client, env["GOOGLE_ADS_CUSTOMER_ID"], plan)
    except GoogleAdsException as ex:  # type: ignore[misc]
        for err in ex.failure.errors:
            print(f"[ads-search] API error: {err.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
