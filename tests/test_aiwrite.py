"""Write with AI, in the clip editor: the AI suggests the big text, title and description for one clip, from the words
spoken in that clip only, and only when asked. Nothing is saved until the person presses Save. Runs against the
code directly, with its own empty data folder. No AI service is contacted: it is stood in for."""
import json, os, sys, tempfile
from pathlib import Path
os.environ.update(SMS_DATA=tempfile.mkdtemp(prefix="sms-aiwrite-"), DATABASE_URL="", SMS_ACCOUNTS="0")
sys.path.insert(0, ".")
from fastapi import HTTPException
from studio import ai, auth, brain, config, ctx, db, jobs, server
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

def refused(call):
    try:
        call(); return None
    except HTTPException as e:
        return e.status_code, e.detail

user = auth.local_user().id
words = ([{"w": f"before{i}", "s": float(i), "e": i + 0.4} for i in range(0, 10)]
         + [{"w": w, "s": 10.0 + i, "e": 10.4 + i} for i, w in enumerate("no way he just jumped the whole canyon".split())]
         + [{"w": f"after{i}", "s": 40.0 + i, "e": 40.4 + i} for i in range(5)])
with ctx.as_user(user):
    job = jobs.create({"type": "file", "name": "Live"}, {}, enqueue=False, kind="live")
    clip = {"id": "clip001", "start": 9.5, "end": 20.0, "status": "done", "hook": "", "title": "Chat spike at 0:00:10",
            "description": "", "hashtags": [], "score": 0, "kind": "chat"}
    job.update(status="done", title="Late night stream", uploader="someone", clips=[clip])
    jobs.save(job)
    Path(job["dir"], "words.json").write_text(json.dumps(words), encoding="utf-8")

    config.ai_ready = lambda: False
    check("no AI key: says where to add one", refused(lambda: server.write_clip_text(job["id"], "clip001")),
          lambda r: r and r[0] == 400 and "Settings, AI" in r[1])

    config.ai_ready = lambda: True
    sent = []
    def fake_ai(prompt, *a, **k):
        sent.append(prompt)
        return ([{"start": 0, "end": 0, "score": 80, "category": "shock", "hook": "HE JUMPED THE CANYON",
                  "title": "He actually jumped the whole canyon", "description": "Nobody thought he'd make it.",
                  "hashtags": ["gta"], "reason": "x"}], "model", 10)
    brain.ask_ai = fake_ai
    got = server.write_clip_text(job["id"], "clip001")
    check("the AI writes the big text, title, description and hashtags",
          got, {"hook": "HE JUMPED THE CANYON", "title": "He actually jumped the whole canyon",
                "description": "Nobody thought he'd make it.", "hashtags": ["gta"]})
    check("it reads only the words spoken in this clip", ("jumped the whole canyon" in sent[0],
          "before3" in sent[0], "after2" in sent[0]), (True, False, False))
    check("nothing is saved until Save is pressed", (clip["hook"], clip["title"]), ("", "Chat spike at 0:00:10"))
    check("one AI call per press", len(sent), 1)

    from unittest.mock import patch
    with patch.object(jobs._q, "put"):
        jobs.request_render(job["id"], "clip001", {"hashtags": "#GTA6 #clips, funny  ##wow #no-way #a #b #c #d #e"})
    check("Save keeps the hashtags, as plain words, at most 8", clip["hashtags"], ["GTA6", "clips", "funny", "wow", "noway", "a", "b", "c"])
    with patch.object(jobs._q, "put"):
        jobs.request_render(job["id"], "clip001", {"hashtags": ""})
    check("and can clear them", clip["hashtags"], [])

    brain.ask_ai = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('HTTP 503 {"message": "This model is currently experiencing high demand."}'))
    ai.provider, ai.models = (lambda: "gemini"), (lambda: ["gemini-2.5-flash"])
    check("the AI busy or failing: says why, in plain words", refused(lambda: server.write_clip_text(job["id"], "clip001")),
          lambda r: r and r[0] == 502 and "overloaded" in r[1])

    clip2 = dict(clip, id="clip002", start=30.0, end=39.0)
    job["clips"].append(clip2)
    check("a clip with too little speech: says so", refused(lambda: server.write_clip_text(job["id"], "clip002")),
          lambda r: r and r[0] == 400 and "too little speech" in r[1])
    check("an unknown clip", refused(lambda: server.write_clip_text(job["id"], "nope")), lambda r: r and r[0] == 404)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
