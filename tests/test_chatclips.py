"""Chat spikes mode in live recording: the setting, the spike rule on the stream clock, the 20 s + 10 s clip it asks
for, no AI checks while it's on, and what the live card is told. Runs against the code directly, with its own
empty data folder. Nothing outside this machine is contacted."""
import os, sys, tempfile
from pathlib import Path
os.environ["SMS_DATA"] = tempfile.mkdtemp(prefix="sms-chat-")
os.environ.update(DATABASE_URL="")
sys.path.insert(0, ".")
from studio import auth, creators, ctx, db, live
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

u, _ = auth._create("owner@example.com", "Owner", password_hash="x")
with ctx.as_user(u.id):
    c = creators.create({"name": "Streamer", "channel": "https://www.twitch.tv/streamer"})
    check("off by default, 10 messages, 2.5x", (c["chat_clips"], c["chat_min"], c["chat_ratio"]), (False, 10, 2.5))
    c = creators.update(c["id"], {"chat_clips": True, "chat_min": "25", "chat_ratio": "3.14"})
    check("turned on and saved", (c["chat_clips"], c["chat_min"], c["chat_ratio"]), (True, 25, 3.1))
    c2 = creators.update(c["id"], {"chat_min": 0, "chat_ratio": 99})
    check("silly values are kept in range", (c2["chat_min"], c2["chat_ratio"]), (1, 10.0))
    c = creators.update(c["id"], {"chat_min": 10, "chat_ratio": 2.5})

job_dir = Path(tempfile.mkdtemp(prefix="sms-chat-job-"))
job = {"id": "j1", "dir": str(job_dir), "user": u.id, "creator": c["id"], "title": "Live", "uploader": "streamer",
       "source": {"url": "https://www.twitch.tv/streamer", "name": "Streamer live"}, "options": {}, "clips": [], "ai": {}}
s = live.Session(job, c)
s.chat_on = True
SR = live.SR

def run_to(sec, per_sec=None):
    """Move the stream clock to `sec`, with chat messages per second from per_sec(t), checking as it goes."""
    start = int(s.samples / SR)
    for t in range(start, sec):
        s.chat[t] += per_sec(t) if per_sec else 0
        s.samples = (t + 1) * SR
        s._chat_rule()

calm = lambda t: 1 if t % 2 == 0 else 0                      # 5 messages per 10 s
run_to(90, calm)
check("calm chat makes no clips", s.manual, [])
check("the card says how busy chat usually is", s._chat_card()["usual"], 5)
run_to(101, lambda t: 3)                                        # 30 in the slice 90-100: 6x usual, over 10 (judged 1 s after it ends)
check("a burst asks for one clip", len(s.manual), 1)
req = s.manual[0] if s.manual else {}
check("20 s before and 10 s after the spike", (req.get("start"), req.get("end")), (70, 100))
check("the clip says why", req.get("why", ""), "30 messages in 10 s, usually 5")
check("and is named after the moment", req.get("title"), "Chat spike at 0:01:30")
run_to(111, lambda t: 3)                                        # still busy straight after: inside the same clip
check("a spike right after doesn't make an overlapping clip", len(s.manual), 1)
check("but it is counted on the card", s._chat_card()["spikes"], 2)

s.manual.clear()
s.settings = dict(c, chat_min=100)                              # Setup changed while recording: applies at once
run_to(200, calm); run_to(210, lambda t: 5)
check("under the minimum is not a spike", s.manual, [])

s.settings = dict(c, chat_clips=False)
run_to(220, lambda t: 9)
check("off: no chat clips", (s.manual, s._chat_mode()), ([], False))

# no chat connection: slices are skipped instead of counted as zero
s2 = live.Session(dict(job, id="j2"), dict(c, chat_clips=True))
s2.chat_on = False
for t in range(120):
    s2.samples = (t + 1) * SR
    s2._chat_rule()
check("without chat nothing is learned or clipped", (len(s2.chat_finder.history), s2.manual), (0, []))
check("a stream that isn't on Twitch is flagged on the card",
      live.Session(dict(job, id="j3", source={"url": "https://kick.com/x", "name": "x"}), dict(c, chat_clips=True))._chat_card()["twitch"], False)

# the analysis loop: in chat mode the AI is never asked, but the chat clip is made
s3 = live.Session(dict(job, id="j4", clips=[]), dict(c, chat_clips=True))
asked, made = [], []
s3._ai_check = lambda *a, **k: asked.append(a)
s3._add_clip = lambda clip: made.append(clip)
s3._scan_segments = lambda: None
s3._transcribe_step = lambda: False
s3._publish = lambda: None
described = []
live.brain.describe = lambda *a, **k: described.append(a) or {"title": "AI title", "hook": "AI hook", "score": 0}
live.creators.log = lambda *a, **k: None
live.alerts.notify = lambda *a, **k: None
live.jobs.submit_live = lambda fn: fn()
live.jobs.save = lambda j: None
s3.chat_on = True
for t in range(100):
    s3.chat[t] += 3 if t >= 90 else calm(t)
for t in range(91):                                            # the stream so far, before the burst is judged
    s3.samples = (t + 1) * SR
    s3._chat_rule()
s3.samples, s3.t_done, s3.ended = 101 * SR, 101.0, True
s3.spike = {"t": 95.0, "why": "chat +500%"}                  # what the AI mode would have sent to the AI
s3.said = {"t": 95.0, "why": 'Someone said "clip"'}
s3._analyze()
check("no AI checks in chat mode", asked, [])
check("the chat clip is made when the stream ends", [(m["start"], m["end"]) for m in made], [(70, 100)])
check("no AI call for its title either", described, [])
check("it gets a plain title, no hook, and is marked as a chat clip", [(m["title"], m["hook"], m.get("kind"), m.get("reason")) for m in made],
      [("Chat spike at 0:01:30", "", "chat", "30 messages in 10 s, usually 5")])
s3.manual.append({"start": 10, "end": 40})                    # a hand clip still gets its AI title
s3._manual_step()
check("hand clips still get an AI title", (len(described), made[-1]["title"]), (1, "AI title"))

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
