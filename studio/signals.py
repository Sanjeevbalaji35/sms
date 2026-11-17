"""Extra signals for finding moments in a video, one number per second:

- reactions: what viewers reacted to. Timestamps people write in YouTube comments ("3:42 lmao"), and Twitch VOD chat
  replay (how busy chat was each second). Both are untrusted text from strangers: only the times are used, never the words.
- action: how much happens on screen. Scene changes and fast motion, measured with ffmpeg on a tiny, low-frame-rate
  copy of the picture. No AI looks at the video.

All of these are best effort: when a site won't give them, the video is clipped without them.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import urllib.request

import numpy as np

from . import media

STAMP = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?![\d:])")
MAX_COMMENTS = 1500


def _seconds(m) -> int:
    h, mi, s = m.group(1), m.group(2), m.group(3)
    return int(h or 0) * 3600 + int(mi) * 60 + int(s)


def comment_times(comments, start: float, length: int) -> np.ndarray:
    """Per second of [start, start+length): how strongly viewers pointed at it. A comment naming 3:42 points at the
    few seconds after it; a liked comment counts for more (a like is someone agreeing)."""
    out = np.zeros(max(0, int(length)), dtype=np.float32)
    for c in comments:
        text = str(c.get("text") or "")[:2000]
        stamps = {_seconds(m) for m in STAMP.finditer(text)}
        if not stamps or len(stamps) > 6:           # a long list of chapters is a table of contents, not a reaction
            continue
        weight = 1.0 + math.log1p(max(0, int(c.get("like_count") or 0)))
        for t in stamps:
            a = int(t - start)
            for k in range(a, a + 10):               # the moment named, and what follows it
                if 0 <= k < len(out):
                    out[k] += weight * (1.0 if k - a < 6 else 0.5)
    return out


def youtube_comments(url: str) -> list:
    opts = {"skip_download": True, "getcomments": True, "quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 20, "logger": media._Quiet(),
            "extractor_args": {"youtube": {"max_comments": [str(MAX_COMMENTS), "all", "0"], "comment_sort": ["top"]}}}
    with media._youtube_guard(url), media.new_ydl(opts) as y:
        info = y.extract_info(url, download=False) or {}
    return info.get("comments") or []


# Twitch's own web player asks this for chat replay. yt-dlp no longer reads it, so SMS does, carefully: a refusal
# just means no chat for this video.
TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"          # the public id of Twitch's web player, not a secret
TWITCH_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def twitch_vod_id(url: str) -> str:
    m = re.match(r"^https?://(?:www\.|m\.)?twitch\.tv/(?:videos/|[^/]+/v(?:ideo)?/)(\d+)", url or "")
    return m.group(1) if m else ""


def twitch_chat(vod: str, start: float, length: int, deadline_s: float = 120) -> np.ndarray:
    import time
    out = np.zeros(max(0, int(length)), dtype=np.float32)
    end_at = time.time() + deadline_s
    variables = {"videoID": vod, "contentOffsetSeconds": int(start)}
    while time.time() < end_at:
        body = json.dumps([{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
                            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_HASH}}}]).encode()
        req = urllib.request.Request(TWITCH_GQL, data=body, headers={"Client-Id": TWITCH_CLIENT, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        comments = (((data[0].get("data") or {}).get("video") or {}).get("comments") or {})
        edges = comments.get("edges") or []
        if not edges:
            break
        last = 0.0
        for e in edges:
            t = float((e.get("node") or {}).get("contentOffsetSeconds") or 0)
            last = t
            k = int(t - start)
            if 0 <= k < len(out):
                out[k] += 1
        if last - start >= length or not (comments.get("pageInfo") or {}).get("hasNextPage"):
            break
        variables = {"videoID": vod, "cursor": edges[-1].get("cursor")}
    return out


def reactions(url: str, start: float, length: int, log=print) -> np.ndarray | None:
    """Viewer reactions for a link, or None if this site has none or won't give them."""
    try:
        if media.is_youtube(url):
            got = comment_times(youtube_comments(url), start, length)
        elif twitch_vod_id(url):
            got = twitch_chat(twitch_vod_id(url), start, length)
        else:
            return None
    except Exception as e:  # noqa: BLE001 - no reactions: clip without them
        log(f"Viewer reactions unavailable ({str(e)[-80:]})")
        return None
    return got if got.any() else None


# ---------------------------------------------------------------- what happens on screen
def action(video, start: float, length: int) -> np.ndarray | None:
    """Per second: how much the picture changed (cuts, flashes, fast motion), from 0 to about 1."""
    vf = "fps=2,scale=96:-2,select='gte(scene\\,0)',metadata=print:file=-"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{max(0.0, start):.2f}", "-i", str(video), "-t", f"{length:.2f}",
           "-an", "-vf", vf, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120, length / 4),
                           creationflags=media.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = np.zeros(max(0, int(length)), dtype=np.float32)
    t = None
    for line in r.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and t is not None and 0 <= int(t) < len(out):
            out[int(t)] = max(out[int(t)], float(m.group(1)))
    if not out.any():
        return None
    return np.convolve(out, np.ones(3) / 3, mode="same").astype(np.float32)     # a moment, not a single frame


def lowres_video(url: str, folder, start=None, end=None):
    """A tiny copy of the picture only (no sound), just for measuring action. Deleted when done."""
    from yt_dlp.utils import download_range_func
    opts = {"format": "wv*[height>=144]/wv*/worstvideo/worst", "outtmpl": str(folder / "lowres.%(ext)s"), "quiet": True,
            "no_warnings": True, "noplaylist": True, "logger": media._Quiet(), "socket_timeout": 20}
    if start is not None or end is not None:
        opts["download_ranges"] = download_range_func(None, [(start or 0, end or float("inf"))])
    with media._youtube_guard(url), media.new_ydl(opts) as y:
        y.download([url])
    return media._biggest(folder, "lowres")


# ---------------------------------------------------------------- no AI: the strongest stretches of a signal
def zscore(x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if len(x) < 5 or not x.any():
        return np.zeros(len(x), dtype=np.float32)
    return (x - np.median(x)) / (x.std() + 1e-6)


def fit(x, n: int) -> np.ndarray | None:
    """Make a signal exactly n seconds long."""
    if x is None:
        return None
    x = np.asarray(x, dtype=np.float32)[:n]
    return np.pad(x, (0, n - len(x))) if len(x) < n else x


LABELS = {"sound": ("Loud moment", "Picked by loudness."),
          "reactions": ("Viewers reacted", "Picked where viewers reacted most (comments and chat)."),
          "action": ("Big moment on screen", "Picked where the most happens on screen."),
          "mixed": ("Big moment", "Picked by loudness, viewer reactions and on-screen action, because the AI was unavailable.")}


def peaks(signal, words, n: int, max_len: float, duration: float, kind: str, need_speech=True) -> list:
    """The n strongest, non-overlapping stretches of a signal, as clips."""
    z = zscore(signal)
    title, reason = LABELS[kind]
    picks = []
    for sec in np.argsort(-z):
        if z[sec] <= 0.5 and picks:
            break                                     # the rest is ordinary: fewer clips beat weak ones
        s = max(0.0, float(sec) - max_len * 0.6)
        e = min(duration, s + max_len)
        if any(not (e <= p["start"] or s >= p["end"]) for p in picks):
            continue
        if need_speech and words and not any(s <= w["s"] <= e for w in words):
            continue
        picks.append({"start": s, "end": e, "score": 50, "category": "other", "hook": "WAIT FOR IT", "title": title,
                      "description": "", "hashtags": [], "highlight_words": [], "reason": reason})
        if len(picks) >= n:
            break
    return picks
