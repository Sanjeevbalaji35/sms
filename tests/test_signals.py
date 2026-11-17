"""What to clip: the mood asked for reaches the AI, viewer reactions (comment timestamps, chat replay) and on-screen
action are measured and marked, and the no-AI modes pick the strongest stretch of their signal. Runs against the
code directly, with its own empty data folder. Nothing outside this machine is contacted."""
import os, subprocess, sys, tempfile
from pathlib import Path
os.environ["SMS_DATA"] = tempfile.mkdtemp(prefix="sms-sig-")
os.environ.update(DATABASE_URL="")
sys.path.insert(0, ".")
import numpy as np
from studio import auth, brain, config, creators, ctx, db, jobs, signals
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

# ---- comment timestamps
comments = [{"text": "3:42 LMAOOO", "like_count": 900}, {"text": "the part at 3:40 though"}, {"text": "1:02:03 best bit"},
            {"text": "0:00 intro 1:00 part one 2:00 part two 3:00 part three 4:00 five 5:00 six 6:00 seven"},  # chapters
            {"text": "great video, 10/10"}, {"text": "it costs $3:50? no"}]
r = signals.comment_times(comments, 0, 4000)
check("a timestamp in a comment points at that moment", int(np.argmax(r)) in range(222, 228), True)
check("a liked comment counts for more", r[222] > r[3725], True)
check("hour:minute:second works", r[3723] > 0, True)
check("a list of chapters is not a reaction", r[60] == 0 and r[120] == 0, True)
check("only the part of the video being clipped", signals.comment_times(comments, 200, 60)[22] > 0, True)
check("Twitch VOD links are recognised", [signals.twitch_vod_id(u) for u in ("https://www.twitch.tv/videos/2233445566",
      "https://twitch.tv/somebody/v/123", "https://www.twitch.tv/somebody")], ["2233445566", "123", ""])

# ---- on-screen action, measured from a real video: still for 12 s, then 6 s of motion with cuts, then still again
tmp = Path(tempfile.mkdtemp(prefix="sms-sig-vid-"))
vid = tmp / "v.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=10:d=12",
                "-f", "lavfi", "-i", "testsrc=s=320x180:r=10:d=6",
                "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=10:d=12",
                "-filter_complex", "[1]hue=H=t*3[m];[0][m][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(vid)], check=True)
a = signals.action(vid, 0, 30)
check("action is measured per second", a is not None and len(a) == 30, True)
check("the busy part stands out", a is not None and a[12:18].mean() > 5 * max(a[:10].mean(), 1e-3), True)
check("only the chosen part is measured", signals.action(vid, 12, 6) is not None and len(signals.action(vid, 12, 6)) == 6, True)

# ---- no AI: the strongest stretch of the chosen signal
words = [{"w": f"w{i}", "s": float(i), "e": i + 0.5} for i in range(0, 120, 2)]
db_ = np.full(120, -30.0); db_[30:35] = -8                         # loud around 30 s
rx = np.zeros(120); rx[80:86] = 9                                  # viewers pointed at 80 s
ax = np.zeros(120); ax[50:55] = 0.9                                # action around 50 s
asked = []
brain.ask_ai = lambda prompt, *a, **k: asked.append(prompt) or ([], "model", 0)
config.ai_ready = lambda: True
mid = lambda cs: [round((c["start"] + c["end"]) / 2) for c in cs]
for by, around in (("sound", 32), ("reactions", 83), ("action", 52)):
    got, info = brain.find_clips(words, db_, 120, max_clips=1, min_len=10, max_len=20, pick_by=by, reactions=rx, action=ax)
    check(f"pick by {by}: the clip covers that moment", mid(got), lambda m, a=around: len(m) == 1 and abs(m[0] - a) <= 10)
check("the no-AI modes never ask the AI", asked, [])
got, info = brain.find_clips(words, db_, 120, max_clips=1, min_len=10, max_len=20, pick_by="reactions", reactions=None)
check("no reactions found: falls back to loudness, and says so", (mid(got), "loudness" in info["note"]), lambda v: abs(v[0][0] - 32) <= 10 and v[1])

# ---- the AI: the mood, and the marks for reactions and action
brain.find_clips(words, db_, 120, max_clips=2, mood="funny", reactions=rx, action=ax)
p = asked[-1] if asked else ""
check("the mood asked for reaches the AI", "ONLY funny moments" in p, True)
check("lines viewers reacted to are marked (CHAT)", "[80.0] (CHAT)" in p or "(CHAT)" in p.split("[80.0]")[1][:20], True)
check("lines with on-screen action are marked (ACTION)", "(ACTION)" in p.split("[50.0]")[1][:30], True)
brain.find_clips(words, db_, 120, max_clips=2)
check("no mood: the usual mix", "funny moments, fails, clutch plays" in asked[-1] and "ONLY" not in asked[-1], True)
check("emotional moments have a category", "emotional" in brain.CATEGORIES, True)

# ---- the AI unavailable: every signal there is, mixed
brain.ask_ai = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
got, info = brain.find_clips(words, db_, 120, max_clips=3, min_len=10, max_len=20, reactions=rx, action=ax, log=lambda m: None)
check("AI down: loudness, reactions and action are all used", ("reactions" in info["note"], len(got)), (True, 3))

# ---- the settings
check("job options keep the new choices", jobs.clean_options({"mood": "hype", "pick_by": "action", "use_comments": False, "use_visual": True}),
      {"mood": "hype", "pick_by": "action", "use_comments": False, "use_visual": True})
check("nonsense falls back to the defaults", jobs.clean_options({"mood": "evil", "pick_by": "magic"}), {"mood": "any", "pick_by": "ai"})
u, _ = auth._create("owner@example.com", "Owner", password_hash="x")
with ctx.as_user(u.id):
    c = creators.create({"name": "S", "channel": "https://www.twitch.tv/s"})
    check("creators start with: anything good, by AI, comments on, picture off",
          (c["mood"], c["pick_by"], c["use_comments"], c["use_visual"]), ("any", "ai", True, False))
    c = creators.update(c["id"], {"mood": "emotional", "pick_by": "reactions", "use_visual": True})
    check("and can change them", (c["mood"], c["pick_by"], c["use_visual"]), ("emotional", "reactions", True))
    job = jobs.create({"type": "url", "url": "https://www.youtube.com/watch?v=x", "name": "x"}, {}, enqueue=False, creator=c["id"])
    check("their videos use the creator's choices", (job["options"]["mood"], job["options"]["pick_by"]), ("emotional", "reactions"))

# ---- which extra signals a video gathers
seen = []
signals.reactions = lambda url, s, n, log: seen.append(("rx", url)) or np.ones(n)
o = dict(jobs.DEFAULTS)
ex = jobs._extra_signals({}, o, {"type": "url", "url": "https://youtu.be/x"}, tmp, 0, 30, None, [])
check("a link: comments are read by default", (seen, ex["action"].result()), ([("rx", "https://youtu.be/x")], None))
ex = jobs._extra_signals({}, dict(o, use_visual=True), {"type": "file", "path": str(vid)}, tmp, 0, 30, None, [])
check("an upload with the picture on: action measured from the file itself", ex["action"].result() is not None, True)
seen.clear()
jobs._extra_signals({}, dict(o, use_comments=False), {"type": "url", "url": "https://youtu.be/x"}, tmp, 0, 30, None, [])
check("comments off: not read", seen, [])

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
