#!/usr/bin/env bash
# Record a Twitch stream and clip the moments chat goes wild.
#   ./watch.sh <twitch_channel> [--min 10] [--model small]
# Clips land in clips/, the full recording in rec/. Stop with Ctrl+C. See clipwatch/README.md.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m clipwatch "$@"
