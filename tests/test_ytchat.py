"""YouTube live chat for live recordings: reading it the way YouTube's chat window does (with yt-dlp's own helpers),
placing each message at the second it was written, and chat spikes working for YouTube like they do for Twitch.
YouTube is simulated with pages and answers shaped like the real ones; nothing outside this machine is contacted."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
os.environ["SMS_DATA"] = tempfile.mkdtemp(prefix="sms-ytchat-")
os.environ.update(DATABASE_URL="")
sys.path.insert(0, ".")
import yt_dlp
from studio import creators, live, media, ytchat
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

def text_msg(ts_ms, msg="pog"):
    return {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "message": {"runs": [{"text": msg}]}, "timestampUsec": str(ts_ms * 1000)}}}}
def paid_msg(ts_ms):
    return {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {"timestampUsec": str(ts_ms * 1000)}}}}
def ticker(ts_ms):
    return {"addLiveChatTickerItemAction": {"item": {"liveChatTickerPaidMessageItemRenderer": {
        "showItemEndpoint": {"showLiveChatItemEndpoint": {"renderer": {"liveChatPaidMessageRenderer": {"timestampUsec": str(ts_ms * 1000)}}}}}}}}
def batch(actions, cont=None, timeout=1000, kind="invalidationContinuationData"):
    c = {"actions": actions}
    if cont:
        c["continuations"] = [{kind: {"continuation": cont, "timeoutMs": timeout}}]
    return {"continuationContents": {"liveChatContinuation": c}}

# ---- one batch
b = batch([text_msg(1000), paid_msg(2000), ticker(1500), {"removeChatItemAction": {}}, "junk"], "next")["continuationContents"]["liveChatContinuation"]
check("messages and Super Chats count, tickers (repeats) and removals don't", ytchat.message_times(b), [1000, 2000])
check("the next step and its wait", ytchat.next_step(b), ("next", 1.0))
check("timed continuations work too", ytchat.next_step(batch([], "t2", 5000, "timedContinuationData")["continuationContents"]["liveChatContinuation"]), ("t2", 5.0))
check("no continuation: the chat has ended", ytchat.next_step({"actions": []}), (None, 0.0))

# ---- following a chat, against a simulated YouTube
CFG = {"INNERTUBE_API_KEY": "KEY123", "INNERTUBE_CLIENT_NAME": "WEB", "INNERTUBE_CLIENT_VERSION": "2.20261101.00.00",
       "INNERTUBE_CONTEXT_CLIENT_NAME": 1, "INNERTUBE_CONTEXT": {"client": {"clientName": "WEB", "clientVersion": "2.20261101.00.00", "visitorData": "VIS"}}}
def html(initial):
    return (f'<html><script>ytcfg.set({json.dumps(CFG)});</script><script>var ytInitialData = {json.dumps(initial)};</script></html>').encode()
WATCH = html({"contents": {"twoColumnWatchNextResults": {"conversationBar": {"liveChatRenderer": {
    "continuations": [{"reloadContinuationData": {"continuation": "C0"}}]}}}}})
now_ms = int(time.time() * 1000)
top = batch([text_msg(now_ms - 60000)], "CT")                  # "Top chat": what YouTube shows first, filtered
top["continuationContents"]["liveChatContinuation"]["header"] = {"liveChatHeaderRenderer": {"viewSelector": {
    "sortFilterSubMenuRenderer": {"subMenuItems": [
        {"title": "Top chat", "continuation": {"reloadContinuationData": {"continuation": "C0"}}},
        {"title": "Live chat", "continuation": {"reloadContinuationData": {"continuation": "CL"}}}]}}}}
FIRST = html(top)
LIVE_FIRST = html(batch([text_msg(now_ms - 60000)], "C1"))      # "Live chat": every message; the backlog on screen
API = [batch([text_msg(now_ms + 1), text_msg(now_ms + 2)], "C2"), batch([text_msg(now_ms + 3)])]
asked = []

class Resp:
    def __init__(self, body): self.body = body
    def read(self): return self.body

def fake_urlopen(self, req):
    url = req if isinstance(req, str) else req.url
    asked.append((url, getattr(req, "data", None)))
    if "/watch?v=" in url:
        return Resp(WATCH)
    if "/live_chat?continuation=C0" in url:
        return Resp(FIRST)
    if "/live_chat?continuation=CL" in url:
        return Resp(LIVE_FIRST)
    if "get_live_chat?key=KEY123" in url:
        return Resp(json.dumps(API.pop(0)).encode())
    raise AssertionError("unexpected " + url)
yt_dlp.YoutubeDL.urlopen = fake_urlopen

got, connected = [], []
ytchat.read("vid1", lambda t, backlog=False: got.append((backlog, t)), threading.Event(), on_connected=lambda: connected.append(1))
check("connects through the watch page and the chat page", [u for u, _ in asked[:2]],
      ["https://www.youtube.com/watch?v=vid1", "https://www.youtube.com/live_chat?continuation=C0"])
check("then switches from the filtered Top chat to Live chat (every message), as yt-dlp does", asked[2][0],
      "https://www.youtube.com/live_chat?continuation=CL")
posted = [json.loads(d) for u, d in asked[3:]]
check("then follows the chat with each next continuation", [p["continuation"] for p in posted], ["C1", "C2"])
check("asking as YouTube's own web client", posted[0]["context"]["client"]["clientName"], "WEB")
check("every batch is handed over, the first marked as backlog", [(bl, len(t)) for bl, t in got], [(True, 1), (False, 2), (False, 1)])
check("and the chat window is reported as connected", connected, [1])
check("it stops when the chat ends", API, [])

WATCH = html({"contents": {"twoColumnWatchNextResults": {}}})
try:
    ytchat.read("vid2", lambda *a, **k: None, threading.Event()); check("chat turned off is said plainly", False)
except ytchat.NoChat as e:
    check("chat turned off is said plainly", "turned off" in str(e), True)

stop = threading.Event(); stop.set()
WATCH = html({"contents": {"twoColumnWatchNextResults": {"conversationBar": {"liveChatRenderer": {
    "continuations": [{"reloadContinuationData": {"continuation": "C0"}}]}}}}})
API[:] = [batch([], "C9")]
asked.clear()
ytchat.read("vid3", lambda *a, **k: None, stop)
check("Stop ends the reading straight away", any("get_live_chat" in u for u, _ in asked), False)

# ---- in a live recording
c = dict(creators.DEFAULTS, id="cr", chat_clips=True)
job = {"id": "j1", "dir": tempfile.mkdtemp(), "user": "u", "creator": "cr", "title": "Live", "uploader": "x",
       "source": {"url": "https://www.youtube.com/@someone/live", "name": "x"}, "options": {}, "clips": [], "ai": {}}
s = live.Session(job, c)
check("a YouTube stream knows its chat is on YouTube", (s.chat_site, s._chat_card()["chat_site"]), ("youtube", "youtube"))
s.samples = 100 * live.SR
t = time.time()
s._yt_messages([int((t - 300) * 1000)] * 5, backlog=True)
check("the backlog on screen when reading began is left out", s.chat_total, 0)
s._yt_messages([int((t - 2.4) * 1000), int((t - 2.4) * 1000), int((t - 0.4) * 1000)])
check("each message keeps its place: written 2 s before the newest one", dict(s.chat), {98: 2, 100: 1})
for skew in (-60, 75):                                          # this computer's clock a minute or more off
    sk = live.Session(dict(job, id=f"skew{skew}"), c)
    sk.samples = 100 * live.SR
    sk._yt_messages([int((t + skew - 0.4) * 1000)])             # teach it the usual delay
    sk.chat.clear()
    sk._yt_messages([int((t + skew - 2.4) * 1000), int((t + skew - 2.4) * 1000), int((t + skew - 0.4) * 1000)])
    got = dict(sk.chat)
    newest = max(got) if got else -1
    check(f"a clock {abs(skew)} s {'behind' if skew < 0 else 'ahead'} doesn't move the moments", got,
          lambda g, n=newest: n in (99, 100) and g == {n - 2: 2, n: 1})

# chat spikes on YouTube: same rule as Twitch, judged a little later because messages come in batches
s2 = live.Session(dict(job, id="j2"), c)
s2.chat_on = True
for sec in range(130):
    s2.chat[sec] += 3 if 90 <= sec < 100 else (1 if sec % 2 == 0 else 0)
for sec in range(107):                                         # up to 107 s into the stream
    s2.samples = (sec + 1) * live.SR
    s2._chat_rule()
check("a burst isn't judged until late messages have had time to arrive", s2.manual, [])
for sec in range(107, 112):
    s2.samples = (sec + 1) * live.SR
    s2._chat_rule()
check("then the YouTube chat spike makes a clip, like on Twitch", [(m["start"], m["end"]) for m in s2.manual], [(70, 100)])

# chat that can't be read is shown on the card instead of "connecting" for ever
s3 = live.Session(dict(job, id="j3"), c)
live_read = ytchat.read
ytchat.read = lambda *a, **k: (_ for _ in ()).throw(ytchat.NoChat("This stream's chat is turned off or not public."))
s3.yt_chat_for = "vidX"
s3._yt_chat("vidX")
check("chat turned off: the card says why", s3._chat_card()["problem"], "This stream's chat is turned off or not public.")
ytchat.read = live_read

# the stream's video id is found when the recording starts, so its chat can be read
class FakeYDL:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=False):
        return {"is_live": True, "id": "LIVEID", "title": "t", "uploader": "u", "url": "https://x/stream.m3u8"}
media.new_ydl = lambda opts: FakeYDL()
check("the live YouTube video is found for its chat", live.resolve("https://www.youtube.com/@someone/live")["youtube_id"], "LIVEID")
check("Twitch streams don't get one", live.resolve("https://www.twitch.tv/someone")["youtube_id"], "")

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
