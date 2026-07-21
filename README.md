# Google Ads Campaigns

> Ship Google Ads search campaigns as reviewable JSON specs — offline dry-run, atomic create, PAUSED in code. Use when an agent builds or edits a campaign. Trigger on "create a Google Ads campaign", "search campaign spec", "ads dry-run".

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-blue.svg)](https://opensource.org/licenses/MIT-0)
[![ClawHub](https://img.shields.io/badge/ClawHub-Published-orange)](https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns)
[![Version](https://img.shields.io/badge/version-1.0.1-green)](https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns)

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

### Via ClawHub (recommended)

👉 **<https://clawhub.ai/alexbloch-ia/skills/google-ads-campaigns>**

```bash
clawhub install google-ads-campaigns
# or, from an OpenClaw agent:
openclaw skills install @alexbloch-ia/google-ads-campaigns
```

### Via this repository (manual)

```bash
git clone https://github.com/AlexBloch-IA/google-ads-campaigns.git
cd google-ads-campaigns
./install.sh
```

The script copies the full skill payload into every supported stack it finds:

- `~/.claude/skills/google-ads-campaigns/` (Claude Code)
- `~/.openclaw/skills/google-ads-campaigns/` (OpenClaw)

### Manual copy

```bash
mkdir -p ~/.claude/skills/google-ads-campaigns
cp -R SKILL.md ~/.claude/skills/google-ads-campaigns/   # plus scripts/, references/, templates/… if present
```

---

## Repository structure

```
google-ads-campaigns/
├── SKILL.md
├── ads-search.py
├── budget-cap-guard.js
├── campaign.example.json
├── README.md
├── LICENSE
└── install.sh
```

---

## License

Released under **MIT-0** (MIT No Attribution). Use, fork, adapt, redistribute — no attribution required.

---

## Author

[Alexandre Bloch](https://github.com/AlexBloch-IA) — founder of [OpenClaw](https://openclaw.ai).
Published on [ClawHub](https://clawhub.ai/alexbloch-ia).
