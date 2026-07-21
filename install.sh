#!/usr/bin/env bash
# install.sh — copy this skill into your local agent's skills directory.
#
# Detects supported agent stacks and installs into each one that's present:
#   • Claude Code  → ~/.claude/skills/google-ads-campaigns/
#   • OpenClaw     → ~/.openclaw/skills/google-ads-campaigns/
#
# Copies the full skill payload (SKILL.md + any scripts/, references/,
# templates/, context/ and example files). Idempotent: re-running overwrites.
#
# Usage:
#   ./install.sh            # auto-detect and install everywhere supported
#   ./install.sh claude     # force install into ~/.claude only
#   ./install.sh openclaw   # force install into ~/.openclaw only

set -euo pipefail

SKILL_NAME="google-ads-campaigns"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/SKILL.md" ]]; then
  echo "❌ SKILL.md not found next to install.sh (${SCRIPT_DIR})" >&2
  exit 1
fi

TARGET="${1:-auto}"
installed_count=0

copy_payload() {
  local dest_dir="$1"
  mkdir -p "${dest_dir}"
  # copy everything except repo-only meta files
  ( cd "${SCRIPT_DIR}" && \
    find . -mindepth 1 -maxdepth 1 \
      ! -name '.git' ! -name '.gitignore' ! -name 'README.md' \
      ! -name 'LICENSE' ! -name 'install.sh' \
      -exec cp -R {} "${dest_dir}/" \; )
}

install_into() {
  local label="$1"; local base_dir="$2"
  local dest_dir="${base_dir}/${SKILL_NAME}"
  if [[ ! -d "${base_dir}" ]]; then
    if [[ "${TARGET}" == "auto" ]]; then return 0; fi
    echo "⚠  ${label} skills dir (${base_dir}) missing — creating it."
    mkdir -p "${base_dir}"
  fi
  copy_payload "${dest_dir}"
  echo "✅ Installed into ${label} → ${dest_dir}"
  installed_count=$((installed_count + 1))
}

case "${TARGET}" in
  auto)
    install_into "Claude Code" "${HOME}/.claude/skills"
    install_into "OpenClaw"    "${HOME}/.openclaw/skills"
    ;;
  claude)   install_into "Claude Code" "${HOME}/.claude/skills" ;;
  openclaw) install_into "OpenClaw"    "${HOME}/.openclaw/skills" ;;
  *) echo "Usage: $0 [auto|claude|openclaw]" >&2; exit 2 ;;
esac

if [[ ${installed_count} -eq 0 ]]; then
  echo "❌ No supported skills directory found (~/.claude/skills, ~/.openclaw/skills)." >&2
  echo "   Run: $0 claude   (or)   $0 openclaw" >&2
  exit 1
fi
