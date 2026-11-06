#!/usr/bin/env bash
set -euo pipefail

# nightcut installer/updater for Apple Silicon macOS.
# Overrides: NIGHTCUT_DIR, NIGHTCUT_REPO, VISION_MODEL, EDIT_MODEL.

REPO="${NIGHTCUT_REPO:-https://github.com/Nilson89/nightcut.git}"
TARGET="${NIGHTCUT_DIR:-$HOME/nightcut}"
VISION_MODEL="${VISION_MODEL:-qwen2.5vl:7b}"
EDIT_MODEL="${EDIT_MODEL:-qwen3:14b}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. Use install.bat on Windows." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "This installer currently supports Apple Silicon Macs (M1 or newer)." >&2
  exit 1
fi
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if (( MACOS_MAJOR < 14 )); then
  echo "nightcut requires macOS 14 or newer (found $(sw_vers -productVersion))." >&2
  exit 1
fi

printf '\n== nightcut for Apple Silicon ==\n\n'
printf 'Install directory: %s\n' "$TARGET"
printf 'Required free space: about 25 GB\n\n'

# Refuse to update or execute an unrelated or locally modified checkout.
if [[ -d "$TARGET/.git" ]]; then
  ORIGIN="$(git -C "$TARGET" remote get-url origin 2>/dev/null || true)"
  if [[ "$ORIGIN" != "$REPO" ]]; then
    echo "Existing checkout has an unexpected origin: $ORIGIN" >&2
    echo "Expected: $REPO" >&2
    echo "Set NIGHTCUT_DIR to another directory instead of running untrusted code." >&2
    exit 1
  fi
  if [[ -n "$(git -C "$TARGET" status --porcelain)" ]]; then
    echo "The existing checkout has local changes; refusing to overwrite them: $TARGET" >&2
    exit 1
  fi
elif [[ -e "$TARGET" ]]; then
  echo "Target exists but is not a Git checkout: $TARGET" >&2
  exit 1
fi

if [[ -x /opt/homebrew/bin/brew ]]; then
  BREW=/opt/homebrew/bin/brew
elif command -v brew >/dev/null 2>&1 && [[ "$(brew --prefix)" == /opt/homebrew ]]; then
  BREW="$(command -v brew)"
else
  echo "Native Apple Silicon Homebrew is missing; installing it from brew.sh ..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  BREW=/opt/homebrew/bin/brew
fi
eval "$($BREW shellenv)"

for formula in git ffmpeg ollama uv; do
  if "$BREW" list --formula "$formula" >/dev/null 2>&1; then
    echo "- $formula is already installed"
  else
    echo "- Installing $formula ..."
    "$BREW" install "$formula"
  fi
done

if [[ -d "$TARGET/.git" ]]; then
  echo "Updating existing checkout ..."
  git -C "$TARGET" pull --ff-only
else
  echo "Cloning nightcut ..."
  git clone "$REPO" "$TARGET"
fi

echo "Creating the Python environment (native macOS PyTorch) ..."
(
  cd "$TARGET"
  uv sync
)

if ! ollama list >/dev/null 2>&1; then
  echo "Starting Ollama as a background service ..."
  "$BREW" services start ollama
  for _ in {1..60}; do
    ollama list >/dev/null 2>&1 && break
    sleep 1
  done
fi
if ! ollama list >/dev/null 2>&1; then
  echo "Ollama did not start. Run 'brew services start ollama' and retry." >&2
  exit 1
fi

echo "Downloading local AI models ..."
ollama pull "$VISION_MODEL"
ollama pull "$EDIT_MODEL"

if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q 'h264_videotoolbox'; then
  echo "Warning: FFmpeg has no h264_videotoolbox encoder; nightcut will use software x264." >&2
fi

(
  cd "$TARGET"
  uv run nightcut profiles >/dev/null
)

cat <<EOF

Installation complete: $TARGET

First run:
  cd "$TARGET"
  uv run nightcut layout "/path/to/recording.mkv" --save config/local.yaml
  uv run nightcut run "/path/to/recording.mkv" --profile bf6 --config config/local.yaml

Ollama and PyTorch use Metal automatically. The first transcription run downloads Whisper.
EOF
