#!/usr/bin/env bash
# nightcut updater for macOS and Linux: ./update.sh inside the nightcut folder.
# Pulls the latest version from GitHub, updates the Python environment and
# downloads missing Ollama models. config/local.yaml, your own profiles and
# the output folder are kept.
set -euo pipefail

# Git replaces this file during the update; run from a copy so bash does not
# read the new file halfway through.
if [[ "${1:-}" != "__run" ]]; then
  DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  COPY="$(mktemp -t nightcut-update.XXXXXX)"
  cp "${BASH_SOURCE[0]}" "$COPY"
  exec bash "$COPY" __run "$DIR"
fi
DIR="$2"
trap 'rm -f "$0"' EXIT
cd "$DIR"

[[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
for tool in git uv ollama; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "$tool not found. Run the installer (install-macos.sh) once." >&2
    exit 1
  }
done
[[ -d .git ]] || { echo "Not a nightcut checkout: $DIR" >&2; exit 1; }

version() { sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1; }
OLD="$(version)"

printf '\n== nightcut update ==\nFolder: %s\n\n' "$DIR"
echo "[1/4] Fetching the latest version ..."
git fetch --tags --force origin
git checkout -- uv.lock 2>/dev/null || true   # uv may rewrite the lockfile; not your change

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo
  echo "You changed files that belong to nightcut:"
  git status --short --untracked-files=no
  echo
  echo "Tip: your own settings belong in config/local.yaml, which is never overwritten."
  read -r -p "Put the changes aside (git stash) and update anyway? [y/N] " answer
  [[ "$answer" =~ ^[YyJj] ]] || { echo "Update cancelled, nothing changed."; exit 1; }
  git stash push -m "nightcut update.sh $(date '+%Y-%m-%d %H:%M')"
  echo "Your changes are in the stash. Get them back with: git stash pop"
fi

if [[ "$(git rev-list --count HEAD..@{u})" == "0" ]]; then
  echo "nightcut is already up to date."
else
  echo
  echo "New since your version:"
  git log --oneline --no-decorate HEAD..@{u}
  echo
  # Copied by hand into an old checkout? Then the untracked file blocks the merge.
  git ls-files --error-unmatch update.sh >/dev/null 2>&1 || rm -f update.sh
  git merge --ff-only @{u} || {
    echo "Your checkout differs from GitHub (own commits?). Merge by hand, e.g. git pull --rebase" >&2
    exit 1
  }
fi

echo
echo "[2/4] Updating the Python environment ..."
uv sync
git checkout -- uv.lock 2>/dev/null || true

echo
echo "[3/4] Checking AI models ..."
if ! ollama list >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then brew services start ollama; else (ollama serve >/dev/null 2>&1 &); fi
  for _ in {1..60}; do ollama list >/dev/null 2>&1 && break; sleep 1; done
fi
ollama list >/dev/null 2>&1 || { echo "Ollama does not start. Start it by hand and run update.sh again." >&2; exit 1; }
CFG=()
[[ -f config/local.yaml ]] && CFG=(--config config/local.yaml)
uv run --no-sync nightcut models ${CFG[@]+"${CFG[@]}"} | while read -r model; do
  if ollama show "$model" </dev/null >/dev/null 2>&1; then
    echo "- $model is present"
  else
    echo "- Downloading $model ..."
    ollama pull "$model" </dev/null
  fi
done

echo
echo "[4/4] Quick test ..."
uv run --no-sync nightcut profiles >/dev/null
NEW="$(version)"

echo
if [[ "$OLD" == "$NEW" ]]; then
  echo "Done. nightcut $NEW is up to date."
else
  echo "Done. nightcut $OLD -> $NEW. See CHANGELOG.md for what is new."
fi
