#!/usr/bin/env bash
# Run Clipper from anywhere: clipper serve | analyze <url> | live <url> | stop <job> | render <job> <n> | list | update | stt-download
# Works through a symlink (install.sh links it as ~/.local/bin/clipper).
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" != /* ]] && src="$dir/$src"
done
CLIPPER_HOME="$(cd -P "$(dirname "$src")" && pwd)"
export CLIPPER_HOME
cd "$CLIPPER_HOME" || { echo "Clipper folder not found."; exit 1; }
[ -x .venv/bin/python ] || { echo "Clipper isn't set up yet. Run ./install.sh in $CLIPPER_HOME."; exit 1; }
export PATH="$CLIPPER_HOME/.venv/bin:$PATH"     # yt-dlp lives in the venv
export NO_PROXY="localhost,127.0.0.1${NO_PROXY:+,$NO_PROXY}" no_proxy="localhost,127.0.0.1${no_proxy:+,$no_proxy}"   # Gradio checks localhost; never via a proxy
exec .venv/bin/python -m clipper "$@"
