#!/bin/bash
# Double-click to start SMS on a Mac. The first start sets everything up, which takes a few minutes.
cd "$(dirname "$0")" || exit 1
for py in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$py" start.py
  fi
done
echo
echo "  SMS needs Python 3.10 or newer. Get it from https://www.python.org/downloads/"
echo "  (or run: brew install python), then double-click Start SMS again."
echo
read -r -p "  Press Enter to close this window."
