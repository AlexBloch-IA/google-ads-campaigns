# Google Ads Campaigns

> Google Ads search campaigns as reviewable JSON specs — offline plan by default, `--apply` creates PAUSED; opt-in budget-cap script pauses a mandatory named scope and fails closed.

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-blue.svg)](https://opensource.org/licenses/MIT-0)
[![ClawHub](https://img.shields.io/badge/ClawHub-Published-orange)](https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns)
[![Version](https://img.shields.io/badge/version-1.1.0-green)](https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns)

A Claude Code / [OpenClaw](https://openclaw.ai) skill, published on [ClawHub](https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns). Portable operating doctrine — drop it into an agent's skills directory and follow it.

---

## What the doctrine covers

- The spec is the artifact
- Sizing
- The dry-run is offline by construction
- One atomic mutate, or nothing
- PAUSED is a constant, not an instruction
- API traps
- Idempotence
- Negative keyword taxonomy
- Tool split
- The budget cap lives outside the agent
- Editorial red lines

The full, load-bearing detail lives in [`SKILL.md`](./SKILL.md).

---

## Install

Install through ClawHub only — the registry serves the reviewed, scanned artifact. The slug is shared with another publisher, so name the owner:

```bash
clawhub install @alexbloch-ia/google-ads-campaigns
```

---

## Repository structure

```
google-ads-campaigns/
├── SKILL.md
├── ads-search.py
├── budget-cap-guard.js
├── campaign.example.json
├── tests/                  (hostile-fixture tests, not published)
├── README.md
└── LICENSE
```

---

## License

Released under **MIT-0** (MIT No Attribution). Use, fork, adapt, redistribute — no attribution required.

---

## Author

[Alexandre Bloch](https://github.com/AlexBloch-IA) — founder of [OpenClaw](https://openclaw.ai).
Published on [ClawHub](https://clawhub.ai/alexbloch-ia).
