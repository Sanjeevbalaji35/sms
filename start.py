"""Start SMS with one double-click: "Start SMS" on Windows, "Start SMS.command" on a Mac (or: python3 start.py).

Every time, it:
  1. gets the latest version, if this folder came from git (skipped when there are no changes or no internet),
  2. makes SMS its own Python folder (.venv) the first time, so nothing else on the computer is touched,
  3. installs what SMS needs, only when that list has changed,
  4. installs ffmpeg if it's missing (Windows with winget, Mac with Homebrew),
  5. starts SMS and opens it in your browser.

Close the window (or press Ctrl+C) to stop SMS. Uses only what comes with Python, so it runs before anything is installed.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
STAMP = VENV / "sms-installed.txt"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def stop(msg: str) -> None:
    say("\n  " + msg.replace("\n", "\n  "))
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\n  Press Enter to close this window.")
        except EOFError:
            pass
    sys.exit(1)


def run(cmd, **kw) -> int:
    try:
        return subprocess.call(cmd, cwd=ROOT, **kw)
    except OSError:
        return 1


# ---------------------------------------------------------------- 1. the latest version
def update() -> None:
    if os.environ.get("SMS_LAUNCH_UPDATE") == "0" or not (ROOT / ".git").exists() or not shutil.which("git"):
        return
    say("  Checking for a newer version...")
    try:
        r = subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            say("  Couldn't update right now (offline, or this folder has its own changes). Starting this version.")
    except (OSError, subprocess.TimeoutExpired):
        say("  Couldn't update right now. Starting this version.")


# ---------------------------------------------------------------- 2 and 3. SMS's own Python folder
def need_list() -> str:
    return hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()


def install() -> None:
    if not PY.exists():
        say("  First start: setting up SMS's own Python folder. This takes a few minutes, once.")
        if run([sys.executable, "-m", "venv", str(VENV)]) != 0 or not PY.exists():
            stop("Couldn't set up Python for SMS." + ("\nOn Linux, install it with:  sudo apt install python3-venv" if not (WIN or MAC) else ""))
        run([str(PY), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    want = need_list()
    if STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == want:
        return
    say("  Installing what SMS needs...")
    if run([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"]) != 0:
        stop("Installing failed. Check the internet connection and double-click Start SMS again.")
    STAMP.write_text(want, encoding="utf-8")


# ---------------------------------------------------------------- 4. ffmpeg
def ffmpeg() -> None:
    missing = subprocess.run([str(PY), "-c", "from studio import tools; print(int(bool(tools.missing())))"],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if missing != "1":
        return
    if WIN and shutil.which("winget"):
        say("  Installing ffmpeg, which SMS uses to cut video...")
        run(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements", "--accept-package-agreements"])
    elif MAC and shutil.which("brew"):
        say("  Installing ffmpeg, which SMS uses to cut video...")
        run(["brew", "install", "ffmpeg"])
    else:
        say("  ffmpeg isn't installed yet. SMS will open, and its dashboard says how to install it.")


# ---------------------------------------------------------------- 5. start and open
def address() -> str:
    out = subprocess.run([str(PY), "-c", "from studio import config; print(f'http://127.0.0.1:{config.PORT}')"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return out or "http://127.0.0.1:8765"


def up(url: str) -> bool:
    try:
        urllib.request.urlopen(url + "/healthz", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def start() -> int:
    url = address()
    if up(url):
        say(f"\n  SMS is already running. Opening {url}/app")
        webbrowser.open(url + "/app")
        return 0
    say("  Starting SMS...")
    quiet = dict(os.environ, PYTHONWARNINGS="ignore::DeprecationWarning")   # notes meant for programmers, not for you
    server = subprocess.Popen([str(PY), "-m", "studio.server"], cwd=ROOT, env=quiet)
    for _ in range(180):
        if up(url):
            say(f"\n  SMS is open in your browser: {url}/app\n  Keep this window open while you use SMS. Close it to stop SMS.\n")
            webbrowser.open(url + "/app")
            break
        if server.poll() is not None:
            stop("SMS stopped while starting. The message above says why.")
        time.sleep(1)
    try:
        return server.wait()
    except KeyboardInterrupt:
        say("\n  Stopping SMS...")
        server.terminate()
        try:
            server.wait(15)
        except subprocess.TimeoutExpired:
            server.kill()
        return 0


def main() -> int:
    if sys.version_info < (3, 10):
        stop(f"SMS needs Python 3.10 or newer, and this is {sys.version.split()[0]}.\n"
             "Get the newest from https://www.python.org/downloads/ and double-click Start SMS again.")
    os.chdir(ROOT)
    say("\n  SMS\n")
    update()
    install()
    ffmpeg()
    return start()


if __name__ == "__main__":
    sys.exit(main())
