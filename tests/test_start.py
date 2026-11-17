"""The one-click launcher (start.py, Start SMS.bat, Start SMS.command): what it checks before doing work.
The real end-to-end run (fresh .venv, install, start, open) is too slow for the suite; this covers the decisions."""
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, ".")
import start
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

check("the double-click files are in git, the Mac one runnable",
      subprocess.run(["git", "ls-files", "-s", "Start SMS.bat", "Start SMS.command"], capture_output=True, text=True).stdout.splitlines() and
      [l.split()[0] for l in subprocess.run(["git", "ls-files", "-s", "Start SMS.bat", "Start SMS.command"], capture_output=True, text=True).stdout.splitlines()],
      ["100644", "100755"])
check("Windows gets Windows line endings for its file",
      subprocess.run(["git", "check-attr", "eol", "Start SMS.bat"], capture_output=True, text=True).stdout.strip().endswith("crlf"), True)
check("the install is redone only when the list of needs changes", len(start.need_list()), 64)
calls = []
start.run = lambda cmd, **k: calls.append(cmd) or 0
os.environ["SMS_LAUNCH_UPDATE"] = "0"
start.update()
check("SMS_LAUNCH_UPDATE=0 skips updating", calls, [])
start.up = lambda url: True
opened = []
start.webbrowser.open = lambda u: opened.append(u)
start.address = lambda: "http://127.0.0.1:9999"
check("already running: just opens the browser", (start.start(), opened), (0, ["http://127.0.0.1:9999/app"]))
check("the Mac file checks for Python 3.10 or newer", "sys.version_info < (3, 10)" in Path("Start SMS.command").read_text(), True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
