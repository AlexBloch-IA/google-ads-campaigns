#!/usr/bin/env python3
"""Hostile-fixture tests for ads-search.py gates. Run: python3 tests/ads_search_test.py
Offline: no SDK, no credential, no network. Not part of the published artifact."""
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = json.loads((ROOT / "campaign.example.json").read_text())
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GOOGLE_ADS_")}
fails = 0


def run(spec_text, *flags, env=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(spec_text)
    try:
        return subprocess.run([sys.executable, str(ROOT / "ads-search.py"), "--spec", f.name, *flags],
                              capture_output=True, text=True, env=env or CLEAN_ENV, timeout=30)
    finally:
        os.unlink(f.name)


def mutate(fn):
    s = copy.deepcopy(BASE)
    fn(s)
    return json.dumps(s)


def expect(label, res, ok, must=None):
    global fails
    out = res.stdout + res.stderr
    good = (res.returncode == 0) == ok and "Traceback" not in out and (must is None or must in out)
    fails += not good
    last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
    print(f"{'PASS' if good else 'FAIL'}  {label}  (exit {res.returncode}) {last[0][:100]}")


def setk(path, value):
    def fn(s):
        cur = s
        for k in path[:-1]:
            cur = cur[k]
        if value is KeyError:
            cur.pop(path[-1], None)
        else:
            cur[path[-1]] = value
    return fn


print("--- spec file: absent / empty / null / wrong type ---")
expect("empty file", run(""), False)
expect("JSON null", run("null"), False, "spec must be a JSON object")
expect("JSON list", run("[]"), False, "spec must be a JSON object")
expect("JSON string", run('"campaign"'), False, "spec must be a JSON object")
expect("JSON {}", run("{}"), False, "invalid spec")
r = subprocess.run([sys.executable, str(ROOT / "ads-search.py"), "--spec", "/nonexistent.json"],
                   capture_output=True, text=True, env=CLEAN_ENV)
expect("spec path missing", r, False, "spec not found")

print("--- field types: refuse, never coerce ---")
cases = [
    (["daily_budget"], KeyError), (["daily_budget"], None), (["daily_budget"], "9.87"),
    (["daily_budget"], True), (["daily_budget"], 0), (["daily_budget"], -5), (["daily_budget"], float("nan")),
    (["campaign"], None), (["campaign"], 123), (["campaign"], "bad'name"),
    (["final_url"], None), (["final_url"], "http://example.com"),
    (["language"], KeyError), (["language"], None), (["language"], "klingon"),
    (["geo"], KeyError), (["geo"], None), (["geo"], "US"),
    (["geo", "include"], "United States"), (["geo", "include"], None), (["geo", "include"], [None]),
    (["geo", "country_code"], KeyError), (["geo", "locale"], KeyError), (["geo", "worldwide"], "yes"),
    (["ad_groups"], None), (["ad_groups"], []), (["ad_groups"], ["x"]), (["ad_groups"], {}),
    (["negative_keywords"], "free"), (["negative_keywords"], None),
    (["ad_schedule"], "always"), (["ad_schedule", "windows"], None),
]
for path, val in cases:
    label = f"{'.'.join(path)}={'<absent>' if val is KeyError else json.dumps(val) if val == val else 'NaN'}"
    txt = mutate(setk(path, val)) if val == val else mutate(setk(path, 0)).replace('"daily_budget": 0', '"daily_budget": NaN')
    expect(label, run(txt), False)
expect("ad_groups[0].keywords.exact='[kw]' (string)", run(mutate(lambda s: s["ad_groups"][0]["keywords"].__setitem__("exact", "[kw]"))), False)
expect("ad_groups[0].rsa=null", run(mutate(lambda s: s["ad_groups"][0].__setitem__("rsa", None))), False)
for bad in ("25:00", "ab", "", None, "24:30", "9:5"):
    expect(f"schedule start={bad!r}", run(mutate(lambda s, b=bad: s["ad_schedule"]["windows"][0].__setitem__("start", b))), False)
expect("schedule end before start", run(mutate(lambda s: s["ad_schedule"]["windows"][0].update(start="19:00", end="08:00"))), False)
expect("no geo.include, no worldwide", run(mutate(lambda s: s.__setitem__("geo", {}))), False, "WHOLE WORLD")
expect("worldwide without note", run(mutate(lambda s: s.__setitem__("geo", {"worldwide": True}))), False)

print("--- mode boundary ---")
expect("no flag = plan only, exit 0, no env read", run(json.dumps(BASE)), True, "END DRY-RUN")
expect("--dry-run = plan only", run(json.dumps(BASE), "--dry-run"), True, "END DRY-RUN")
expect("--dry-run --apply together refused", run(json.dumps(BASE), "--dry-run", "--apply"), False)
expect("--apply without env -> refuses before any client", run(json.dumps(BASE), "--apply"), False, "missing env vars")
fake = dict(CLEAN_ENV, **{k: "1" for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")},
        GOOGLE_ADS_LOGIN_CUSTOMER_ID="12x", GOOGLE_ADS_CUSTOMER_ID="1")
expect("--apply with non-numeric account id", run(json.dumps(BASE), "--apply", env=fake), False, "must be an account id")
fake["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = "1"
expect("--apply with SDK absent/unpinned", run(json.dumps(BASE), "--apply", env=fake), False, "google-ads")
expect("valid worldwide with note", run(mutate(lambda s: s.__setitem__("geo", {"worldwide": True, "note": "global SaaS"}))), True, "WORLDWIDE")
expect("language_constant override", run(mutate(lambda s: (s.pop("language"), s.__setitem__("language_constant", "1005")))), True, "languageConstants/1005")

print(f"\n{fails} FAILURE(S)" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
