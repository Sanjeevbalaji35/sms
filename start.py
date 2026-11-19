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
import re
import shutil
import socket
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
def port() -> int:
    """SMS's port, read without loading SMS (loading it would start moving its folder, if a move is waiting)."""
    v = os.environ.get("SMS_PORT", "")
    env_file = ROOT / ".env"
    if not v and env_file.is_file():
        m = re.search(r"^\s*SMS_PORT\s*=\s*(\d+)", env_file.read_text(encoding="utf-8", errors="replace"), re.M)
        v = m.group(1) if m else ""
    return int(v) if v.isdigit() else 8765


def address() -> str:
    return f"http://127.0.0.1:{port()}"


_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy or VPN setting


def up(url: str) -> bool:
    try:
        _direct.open(url + "/healthz", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def taken(p: int) -> bool:
    """Something on this computer already answers on this port."""
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", p)) == 0


def holder(p: int):
    """(process id, program name) of whatever listens on the port, if it can be told."""
    try:
        if WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
            pid = next((l.split()[-1] for l in out.splitlines() if f":{p} " in l and "LISTENING" in l), "")
            name = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                  text=True).stdout.split(",")[0].strip('"') if pid else ""
        else:
            pid = subprocess.run(["lsof", "-nP", f"-iTCP:{p}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout.split()[0]
            name = subprocess.run(["ps", "-p", pid, "-o", "comm="], capture_output=True, text=True).stdout.strip()
        return (int(pid), name) if pid else None
    except Exception:  # noqa: BLE001
        return None


def already_running(url: str) -> bool:
    """True if SMS is already running (then the browser is opened). If the port is taken by something that isn't
    answering as SMS, explain, and offer to stop it when it's an older SMS (a Python program)."""
    if up(url):
        say(f"\n  SMS is already running. Opening {url}/app")
        webbrowser.open(url + "/app")
        return True
    p = port()
    if not taken(p):
        return False
    say("  Something is already using SMS's address. Waiting a moment in case it's SMS starting up...")
    for _ in range(20):
        time.sleep(1.5)
        if up(url):
            say(f"\n  SMS is already running. Opening {url}/app")
            webbrowser.open(url + "/app")
            return True
    who = holder(p)
    if who and who[0] == os.getpid():
        who = None                               # never this window itself
    if who and "python" in who[1].lower():
        say(f"  An older SMS is still running in the background (program {who[1]}, number {who[0]}), and it isn't answering.")
        if not (sys.stdin and sys.stdin.isatty()):
            stop("Close the other SMS window (or end it in Task Manager), then start SMS again.")
        try:
            input("  Press Enter to stop it and start SMS fresh, or close this window to leave it.")
        except EOFError:
            stop("Close the other SMS window, then double-click Start SMS again.")
        if WIN:
            subprocess.run(["taskkill", "/PID", str(who[0]), "/T", "/F"], capture_output=True)
        else:
            os.kill(who[0], 15)
        for _ in range(20):
            if not taken(p):
                return False
            time.sleep(0.5)
        stop("The older SMS didn't stop. Restart the computer, then double-click Start SMS again.")
    stop(f"Another program ({who[1] if who else 'unknown'}) is using port {p}, which SMS needs.\n"
         f"Close it, or give SMS another port: add the line  SMS_PORT=8766  to the .env file in the SMS folder.")
    return True


def start() -> int:
    url = address()
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
    if already_running(address()):      # first: nothing is moved or reinstalled under a running SMS
        return 0
    update()
    install()
    ffmpeg()
    return start()


if __name__ == "__main__":
    sys.exit(main())
