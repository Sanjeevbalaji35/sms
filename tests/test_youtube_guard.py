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
    check("no browser login until one is chosen", len(list(y.cookiejar)), 0)

# the browser login: read once (on a Mac each read asks for the keychain password), only YouTube and Google kept
import http.cookiejar, yt_dlp.cookies as ydc
def cookie(domain, name):
    return http.cookiejar.Cookie(0, name, "v", None, False, domain, True, domain.startswith("."), "/", False,
                                 True, None, False, None, None, {})
reads = []
def fake_browser(name, *a, **k):
    reads.append(name)
    return [cookie(".youtube.com", "LOGIN_INFO"), cookie(".google.com", "SID"), cookie(".twitch.tv", "auth-token"),
            cookie("mail.example.com", "session")]
ydc.extract_cookies_from_browser = fake_browser

media._yt_refused_until = 9e18
st = media.set_login_browser("Chrome")
check("choosing a browser saves it", json.loads(media._login_file().read_text())["browser"], "chrome")
check("and tries YouTube again straight away", media.youtube_paused(), False)
check("and checks the login once", (st["ok"], len(reads)), (True, 1))
for _ in range(5):
    with media._ydl({"format": "x"}) as y:
        jar = y.cookiejar
check("five downloads later the browser was still read only once", len(reads), 1)
check("downloads use the login, alongside their own options", (y.params.get("format"), "cookiesfrombrowser" in y.params), ("x", False))
check("only the YouTube and Google cookies are kept", sorted(c.domain for c in jar), [".google.com", ".youtube.com"])
media.note_refusal("https://www.youtube.com/watch?v=x", "Sign in to confirm you're not a bot")
with media._ydl({}) as y:
    y.cookiejar
check("when YouTube refuses anyway, the login is read again (it may have run out)", len(reads), 2)
media.set_login_browser("firefox")
check("changing the browser reads the new one", reads[-1], "firefox")
def broken(name, *a, **k):
    raise RuntimeError("keychain said no")
ydc.extract_cookies_from_browser = broken
media.forget_login()
st = media.login_status(check=True)
check("a browser that can't be read gives a plain answer, not an error", (st["ok"], "Firefox" in st["note"]), (False, True))
tried = []
ydc.extract_cookies_from_browser = lambda *a, **k: tried.append(1) or broken(*a)
for _ in range(3):
    with media._ydl({}) as y:
        check("and downloads still go ahead without the login", len(list(y.cookiejar)), 0)
check("a refused keychain isn't asked again on every download", len(tried), 0)
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
