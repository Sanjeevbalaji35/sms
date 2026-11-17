"""The guard against YouTube's bot check: Deno and yt-dlp's solver are installed and handed to every yt-dlp call,
and the browser login chosen in Settings is used. Runs against the code directly, with its own empty data folder."""
import json, os, subprocess, sys, tempfile
os.environ["SMS_DATA"] = tempfile.mkdtemp(prefix="sms-yt-")
os.environ.update(DATABASE_URL="", SMS_ACCOUNTS="0")
os.environ.pop("SMS_YT_BROWSER", None)
sys.path.insert(0, ".")
from studio import config, media
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

check("requirements install yt-dlp with its YouTube solver and Deno",
      "yt-dlp[default,deno]==" in open("requirements.txt").read(), True)
check("nothing is missing from the guard", media.guard_problems(), [])
d = media.deno_path()
check("Deno is found and runs", subprocess.run([d, "--version"], capture_output=True, text=True).stdout if d else "",
      lambda out: out.startswith("deno "))
with media._ydl({}) as y:
    check("every yt-dlp call is told where Deno is", y.params.get("js_runtimes"), {"deno": {"path": d}})
    check("no browser login until one is chosen", "cookiesfrombrowser" in y.params, False)

media._yt_refused_until = 9e18
st = media.set_login_browser("Firefox")
check("choosing a browser saves it", json.loads(media._login_file().read_text())["browser"], "firefox")
check("and tries YouTube again straight away", media.youtube_paused(), False)
check("a browser that can't be read gives a plain answer, not an error", (st["ok"], st["note"]), lambda v: v[0] is False and "Firefox" in v[1])
with media._ydl({"format": "x"}) as y:
    check("downloads then use that browser's login", y.params.get("cookiesfrombrowser"), ("firefox",))
    check("alongside their own options", y.params.get("format"), "x")
try:
    media.set_login_browser("netscape"); check("unknown browsers are refused", False)
except ValueError:
    check("unknown browsers are refused", True)
os.environ["SMS_YT_BROWSER"] = "edge"
check("SMS_YT_BROWSER in .env wins", (media.login_browser(), media.login_status()["from_env"]), ("edge", True))
os.environ.pop("SMS_YT_BROWSER")
media.set_login_browser("")
check("turning it off", media.login_browser(), "")
check("the blocked message points to the fix", media.YT_BLOCKED, lambda m: m.endswith("Settings, YouTube login."))

config.ACCOUNTS = True                  # a shared server: a file saved earlier is not used, only .env
media._login_file().write_text(json.dumps({"browser": "chrome"}))
check("a shared server never uses a saved browser login", media.login_browser(), "")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
