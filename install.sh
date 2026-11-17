#!/usr/bin/env bash
# Clipper installer (macOS, Linux).
#   curl -fsSL https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.sh | bash
# or, inside a clone:  ./install.sh
# Options: --yes (take every default, no questions).
# Env: CLIPPER_DIR (install folder), CLIPPER_REPO (git URL), CLIPPER_STT_MODEL (existing model folder),
#      CLIPPER_SKIP (comma list of: stt,renderer,command,skill,open) for unattended installs.
set -euo pipefail

REPO="${CLIPPER_REPO:-https://github.com/guleriaakshit/clipper.git}"
YES=0
for a in "$@"; do case "$a" in -y|--yes) YES=1 ;; esac; done
SKIP=",${CLIPPER_SKIP:-},"

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; green=$'\033[32m'; off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
ok()   { printf '  %s✓%s %s\n' "$green" "$off" "$*"; }
warn() { printf '  %s!%s %s\n' "$red" "$off" "$*"; }
die()  { printf '\n%sClipper install stopped:%s %s\n' "$red" "$off" "$*" >&2; exit 1; }
tty_ok() { [ "$YES" = 0 ] && { : </dev/tty; } 2>/dev/null; }
skipped() { [[ "$SKIP" == *",$1,"* ]]; }

# ask "question" y|n  -> exit status 0 for yes
ask() {
  local q="$1" def="$2" ans hint="[y/N]"
  [ "$def" = y ] && hint="[Y/n]"
  if ! tty_ok; then [ "$def" = y ]; return; fi
  read -r -p "  $q $hint " ans </dev/tty || ans=""
  ans="${ans:-$def}"
  [[ "$ans" =~ ^[Yy] ]]
}
# askval "question" default -> prints the answer
askval() {
  local q="$1" def="$2" ans
  if ! tty_ok; then printf '%s' "$def"; return; fi
  read -r -p "  $q${def:+ [$def]} " ans </dev/tty || ans=""
  printf '%s' "${ans:-$def}"
}
have() { command -v "$1" >/dev/null 2>&1; }

say "${bold}Clipper${off} finds where chat spiked in a stream (past or live) and cuts those moments into vertical Shorts."

# ---------------------------------------------------------------- system
OS="$(uname -s)"
case "$OS" in
  Darwin) PM=""; have brew && PM=brew ;;
  Linux)  PM=""; have apt-get && PM=apt; have dnf && PM=dnf; have pacman && PM=pacman ;;
  *) die "This installer is for macOS and Linux. On Windows, in PowerShell: irm https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.ps1 | iex" ;;
esac

pkg_install() {   # pkg_install <brew names> <apt names> <dnf names> <pacman names>
  case "$PM" in
    brew)   brew install $1 ;;
    apt)    sudo apt-get update -qq && sudo apt-get install -y $2 ;;
    dnf)    sudo dnf install -y $3 ;;
    pacman) sudo pacman -S --needed --noconfirm $4 ;;
    *) return 1 ;;
  esac
}

need() {   # need <command> <what it's for> <brew> <apt> <dnf> <pacman>
  local cmd="$1" why="$2"
  if have "$cmd"; then ok "$cmd found"; return 0; fi
  warn "$cmd is missing ($why)."
  if [ -n "$PM" ] && ask "Install it with $PM now?" y; then
    pkg_install "$3" "$4" "$5" "$6" || die "Couldn't install $cmd. Install it yourself and run this again."
    have "$cmd" && { ok "$cmd installed"; return 0; }
  fi
  return 1
}

step "1. Tools"
need git "to download Clipper" git git git git || die "git is needed."
need ffmpeg "to cut and read video" ffmpeg ffmpeg ffmpeg ffmpeg || die "ffmpeg is needed."

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ] && ! have uv; then
  warn "Python 3.11 or newer is missing."
  if [ -n "$PM" ] && ask "Install Python 3.12 with $PM now?" y; then
    pkg_install python@3.12 "python3 python3-venv" python3 python || true
    for c in python3.12 python3; do have "$c" && PY="$(command -v "$c")" && break; done
  fi
  [ -n "$PY" ] || die "Install Python 3.11+ (or uv: https://docs.astral.sh/uv/) and run this again."
fi
have uv && ok "uv found" || ok "Python: $PY"

# ---------------------------------------------------------------- folder
step "2. Folder"
SELF_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$SELF_DIR/clipper/cli.py" ] || SELF_DIR=""
fi
if [ -n "$SELF_DIR" ]; then
  DIR="$SELF_DIR"
  ok "Installing in this copy: $DIR"
else
  DIR="$(askval "Install folder:" "${CLIPPER_DIR:-$HOME/Clipper}")"
  DIR="${DIR/#\~/$HOME}"
  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only -q && ok "Updated $DIR"
  elif [ -f "$DIR/clipper/cli.py" ]; then
    ok "Using the Clipper already in $DIR"
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "$DIR exists and isn't Clipper. Pick an empty or new folder (CLIPPER_DIR=... or answer the question)."
  else
    git clone -q --depth 1 "$REPO" "$DIR" && ok "Downloaded Clipper to $DIR"
  fi
fi
cd "$DIR"
mkdir -p jobs tmp models
export TMPDIR="$DIR/tmp" UV_CACHE_DIR="$DIR/tmp/uv-cache" npm_config_cache="$DIR/tmp/npm-cache"

# ---------------------------------------------------------------- python env
step "3. Python packages"
if have uv; then
  [ -x .venv/bin/python ] || uv venv -q --python ">=3.11" .venv
  PIP=(uv pip install -q -p .venv/bin/python)
else
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  PIP=(.venv/bin/python -m pip install -q)
fi
"${PIP[@]}" -e . && ok "Dashboard, chat readers, yt-dlp and face finder installed"

# ---------------------------------------------------------------- choices
CONF_STT=""
step "4. Speech-to-text (word-by-word subtitles)"
say "  ${dim}Clipper writes one-word subtitles from what the streamer says, using NVIDIA Parakeet locally (offline, free).${off}"
say "  ${dim}The model is a ~490 MB download (~640 MB on disk). Without it, Shorts have no subtitles; everything else works.${off}"
if ! skipped stt && ask "Set up speech-to-text?" y; then
  "${PIP[@]}" "sherpa-onnx>=1.12" && ok "sherpa-onnx installed"
  EXISTING="${CLIPPER_STT_MODEL:-}"
  [ -z "$EXISTING" ] && EXISTING="$(askval "Already have the Parakeet TDT 0.6B v3 int8 model folder? Path, or Enter to download:" "")"
  EXISTING="${EXISTING/#\~/$HOME}"
  if [ -n "$EXISTING" ] && [ -f "$EXISTING/encoder.int8.onnx" ] && [ -f "$EXISTING/tokens.txt" ]; then
    CONF_STT="$EXISTING"; ok "Using the model in $EXISTING"
  else
    [ -n "$EXISTING" ] && warn "No model files in $EXISTING, downloading instead."
    .venv/bin/python -m clipper stt-download && ok "Model ready"
  fi
else
  say "  Skipped. Add it later with: clipper stt-download (after: .venv/bin/pip install sherpa-onnx)"
fi

step "5. Short renderer (Remotion)"
say "  ${dim}Renders the 1080x1920 Short (face cam, gameplay, subtitles). Needs Node.js 18+ and ~400 MB.${off}"
if ! skipped renderer && ask "Set up the renderer?" y; then
  if ! have node || ! node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)' 2>/dev/null; then
    need node "for the renderer" node "nodejs npm" "nodejs npm" "nodejs npm" || warn "No Node.js 18+: skipping the renderer. Install Node and run ./install.sh again."
  fi
  if have node; then
    (cd remotion && npm ci --no-audit --no-fund --loglevel=error) && ok "Remotion installed"
    (cd remotion && npx --yes remotion browser ensure >/dev/null 2>&1) && ok "Headless Chrome for rendering ready" \
      || warn "Couldn't fetch Remotion's headless Chrome now; it downloads on the first render."
  fi
else
  say "  Skipped. You can still find and preview clips; rendering needs this step (run ./install.sh again)."
fi

step "6. Terminal command"
if ! skipped command && ask "Add a 'clipper' command to your terminal?" y; then
  mkdir -p "$HOME/.local/bin"
  chmod +x "$DIR/clipper.sh"
  ln -sf "$DIR/clipper.sh" "$HOME/.local/bin/clipper" && ok "clipper -> $DIR/clipper.sh"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      RC="$HOME/.zshrc"; [ "${SHELL##*/}" = bash ] && RC="$HOME/.bashrc"
      if ask "~/.local/bin isn't on your PATH. Add it to $RC?" y; then
        printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >>"$RC" && ok "Added. Open a new terminal to use 'clipper'."
      fi ;;
  esac
fi

if [ -d "$HOME/.claude" ] && ! skipped skill; then
  step "7. Claude Code"
  say "  ${dim}/clip runs Clipper and writes what happens in each clip; /short edits a clip into a Short (the dashboard's Send to Claude button uses it).${off}"
  if ask "Install the /clip and /short skills for Claude Code?" y; then
    for name in clip short; do
      mkdir -p "$HOME/.claude/skills/$name"
      if [ -f "$HOME/.claude/skills/$name/SKILL.md" ] && ! cmp -s "$DIR/skills/$name/SKILL.md" "$HOME/.claude/skills/$name/SKILL.md"; then
        if ask "You already have a /$name skill. Replace it?" n; then cp "$DIR/skills/$name/SKILL.md" "$HOME/.claude/skills/$name/SKILL.md" && ok "Replaced /$name"; fi
      else
        cp "$DIR/skills/$name/SKILL.md" "$HOME/.claude/skills/$name/SKILL.md" && ok "Installed /$name"
      fi
    done
  fi
fi

# ---------------------------------------------------------------- config
.venv/bin/python - "$CONF_STT" <<'PY'
import json, sys, time
from pathlib import Path
p = Path("config.json")
c = json.loads(p.read_text()) if p.exists() else {}
if sys.argv[1]:
    c["stt_model"] = sys.argv[1]
c["installed"] = time.strftime("%Y-%m-%d")
p.write_text(json.dumps(c, indent=1))
PY

step "Done"
say "  Start the dashboard:  ${bold}clipper serve${off}   (or $DIR/clipper.sh serve)  then open http://localhost:8765"
say "  Past stream:          clipper analyze <Twitch VOD / YouTube / Kick VOD link>"
say "  Live stream:          clipper live <twitch.tv/channel or live YouTube link>"
say "  Get the latest:       clipper update"
if ! skipped open && ask "Open the dashboard now?" n; then exec "$DIR/clipper.sh" serve; fi
