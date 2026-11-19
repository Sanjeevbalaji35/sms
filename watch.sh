#!/usr/bin/env bash
# Record a Twitch or YouTube live stream and clip the moments chat goes wild.
#   ./watch.sh <twitch_channel> [--min 10] [--model small]
#   ./watch.sh @youtube_handle
# Clips land in clips/, the full recording in rec/. Stop with Ctrl+C. See clipwatch/README.md.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/bin/python3" ]; then
  PYTHON=".venv/bin/python3"
else
  PYTHON="python3"
fi

exec "$PYTHON" -m clipwatch "$@"
