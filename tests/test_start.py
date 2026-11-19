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
# ---- an SMS that's already running
import http.server, socket, threading
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), Health)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
os.environ["SMS_PORT"] = str(port)
check("the port is read without loading SMS", start.address(), f"http://127.0.0.1:{port}")
os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"     # a proxy that goes nowhere
check("the 'is SMS running?' check never goes through a proxy", start.up(start.address()), True)
opened, ran = [], []
start.webbrowser.open = lambda u: opened.append(u)
real_run = start.subprocess.run
start.subprocess.run = lambda *a, **k: ran.append(a) or real_run(*a, **k)
start.run = lambda cmd, **k: ran.append(cmd) or 0
check("already running: it just opens the browser", (start.main(), opened), (0, [f"http://127.0.0.1:{port}/app"]))
check("...before updating, installing or loading SMS (which would start a waiting folder move)", ran, [])
start.subprocess.run = real_run

# ---- the port taken by something that isn't answering as SMS
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(50)
os.environ["SMS_PORT"] = str(sock.getsockname()[1])
start.time.sleep = lambda s: None
check("a taken port is noticed", start.taken(int(os.environ["SMS_PORT"])), True)
try:
    start.already_running(start.address()); check("and explained, instead of a crash later", False)
except SystemExit:
    check("and explained, instead of a crash later", True)
os.environ.pop("SMS_PORT"); os.environ.pop("HTTP_PROXY"); os.environ.pop("http_proxy")
check("the Mac file checks for Python 3.10 or newer", "sys.version_info < (3, 10)" in Path("Start SMS.command").read_text(), True)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
