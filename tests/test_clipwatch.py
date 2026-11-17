"""The clip watcher (./watch.sh): spike rule, chat reading, cutting a 9:16 clip from a live recording, subtitles.
Nothing outside this machine is contacted: a fake streamlink makes a test stream with ffmpeg, and a fake chat
server stands in for Twitch. Time is sped up: 1-second chat slices instead of 10."""
import json, os, socket, subprocess, sys, tempfile, threading, time, types
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="clipwatch-test-"))
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
os.environ["CLIPWATCH_IRC"] = f"127.0.0.1:{srv.getsockname()[1]}"
sys.path.insert(0, ".")
import clipwatch.__main__ as cw
from clipwatch.spikes import SpikeFinder

fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

# ---- the spike rule
f = SpikeFinder(ratio=2.5, minimum=10, window=18, warmup=6)
check("nothing counts while warming up", [f.add(50) for _ in range(6)], [False] * 6)
f = SpikeFinder(ratio=2.5, minimum=10, window=18, warmup=6)
for _ in range(18): f.add(4)
check("2.5x the usual and over the minimum is a spike", f.add(10), True)
f = SpikeFinder(ratio=2.5, minimum=10, window=18, warmup=6)
for _ in range(18): f.add(4)
check("under 2.5x is not", f.add(9), False)
f = SpikeFinder(ratio=2.5, minimum=10, window=18, warmup=6)
for _ in range(18): f.add(1)
check("busy enough relative to usual but under --min is not", f.add(8), False)
f = SpikeFinder(ratio=2.5, minimum=10, window=18, warmup=6)
for _ in range(17): f.add(4)
f.add(200)
check("one huge slice doesn't move 'usual' (median, not average)", f.usual(), 4)
f = SpikeFinder(window=18)
for i in range(30): f.add(i)
check("only the last 3 minutes are remembered", len(f.history), 18)

check("channel names and links are accepted", [cw.channel_name(x) for x in ("Foo_Bar", "#foo", "https://www.twitch.tv/foo/")],
      ["foo_bar", "foo", "foo"])
try:
    cw.channel_name("foo bar; rm -rf /"); check("odd names are refused", False)
except SystemExit:
    check("odd names are refused", True)
check("subtitle times", cw._stamp(3725.5), "01:02:05,500")

# ---- a fake chat server: 2 messages a second, then a burst of 40, and a PING that must be answered
pong = threading.Event()
def chat_server():
    c, _ = srv.accept()
    c.settimeout(0.5)
    hello = b""
    while b"JOIN" not in hello: hello += c.recv(1024)
    check("logs in anonymously as justinfan12345", b"NICK justinfan12345" in hello, True)
    c.sendall(b":justinfan12345!justinfan12345@justinfan12345.tmi.twitch.tv JOIN #demo\r\nPING :tmi.twitch.tv\r\n")
    msg = lambda i: f":v{i}!v{i}@v{i}.tmi.twitch.tv PRIVMSG #demo :hello {i}\r\n".encode()
    for sec in range(40):
        try:
            if b"PONG :tmi.twitch.tv" in c.recv(1024): pong.set()
        except socket.timeout:
            pass
        c.sendall(b"".join(msg(i) for i in range(40 if sec == 14 else 2)))
        time.sleep(0.5)
    c.close()
threading.Thread(target=chat_server, daemon=True).start()

# ---- a fake streamlink: writes a 1280x720 test stream into --output in real time for 32 seconds
bin_ = tmp / "bin"; bin_.mkdir()
(bin_ / "streamlink").write_text(f"""#!{sys.executable}
import subprocess, sys
out = sys.argv[sys.argv.index("--output") + 1]
subprocess.run(["ffmpeg", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "32", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-f", "mpegts", out])
""")
(bin_ / "streamlink").chmod(0o755)
os.environ["PATH"] = f"{bin_}{os.pathsep}{os.environ['PATH']}"

cw.BUCKET, cw.BEFORE, cw.AFTER, cw.SETTLE = 1, 4, 2, 1
code = cw.main(["demo", "--no-subs", "--out", str(tmp)])
check("finishes cleanly when the stream ends", code, 0)
check("answers the chat server's PING", pong.is_set(), True)
recs = list((tmp / "rec").glob("demo-*.ts"))
check("the stream is recorded into rec/", len(recs), 1)
clips = sorted((tmp / "clips").glob("demo-*.mp4"))
check("exactly one clip for the one burst", [c.name.split("-")[-1] for c in clips], ["40msgs.mp4"])
if clips:
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                                   "-of", "json", str(clips[0])], capture_output=True, text=True).stdout)
    v = [s for s in p["streams"] if s["codec_type"] == "video"][0]
    check("the clip is vertical 1080x1920", (v["width"], v["height"]), (1080, 1920))
    check("the clip has sound", any(s["codec_type"] == "audio" for s in p["streams"]), True)
    check("the clip is before + after long", float(p["format"]["duration"]), lambda d: abs(d - 6) < 0.6)

# ---- a channel that isn't live: streamlink gives up, and so do we, with its message
(bin_ / "streamlink").write_text(f"#!{sys.executable}\nprint('error: No playable streams found on this URL')\nraise SystemExit(1)\n")
check("an offline channel stops with an error", cw.main(["offline", "--no-subs", "--out", str(tmp / "off")]), 1)

# ---- subtitles, with a stand-in for Whisper (the real one downloads its model from the internet)
class Seg:
    def __init__(s, a, b, t): s.start, s.end, s.text = a, b, t
class FakeModel:
    def __init__(s, *a, **k): pass
    def transcribe(s, path, **k): return iter([Seg(0, 1.25, " Hello chat"), Seg(1.25, 3, "that was crazy ")]), None
sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeModel)
if clips:
    subs = cw.Subtitles("tiny", None)
    subs.add(clips[0]); subs.finish()
    srt = clips[0].with_suffix(".srt")
    check("an .srt is written next to the clip", srt.read_text(encoding="utf-8") if srt.exists() else "",
          "1\n00:00:00,000 --> 00:00:01,250\nHello chat\n\n2\n00:00:01,250 --> 00:00:03,000\nthat was crazy\n\n")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
