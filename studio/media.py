"""Getting video in: link download, probing, audio extraction, loudness."""
import contextlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------- when YouTube refuses this server
# YouTube sometimes refuses a server's downloads altogether ("sign in to confirm you're not a bot"). It is a
# refusal of the server, not of one video, so once it happens YouTube links are turned away at once for a few
# minutes, with a clear message, instead of each one waiting to fail. Then the next link simply tries again.
YT_PAUSE = 600
_yt_refused_until = 0.0
YT_BLOCKED = ("YouTube isn't letting SMS download videos right now. Upload the video file instead, or use a "
              "Twitch or Kick link. YouTube links will be tried again in a few minutes."
              + ("" if config.ACCOUNTS else " To fix it for good: Settings, YouTube login."))
_BOT_CHECK = re.compile(r"not a bot|confirm you.?re not|sign in to confirm(?! your age)", re.I)


class YouTubeBlocked(RuntimeError):
    pass


# ---------------------------------------------------------------- the guard against YouTube's bot check
# YouTube sends each player a small puzzle written in JavaScript. yt-dlp solves it with Deno (a JavaScript program)
# and its solver scripts (yt-dlp-ejs); both come with `pip install -r requirements.txt`. Without them YouTube soon
# treats SMS as a bot. Deno installed by pip is often not on the PATH, so SMS tells yt-dlp exactly where it is.
# On top of that, on your own computer SMS can use the YouTube login from your browser (Settings, YouTube login).
BROWSERS = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox", "brave": "Brave", "safari": "Safari",
            "opera": "Opera", "vivaldi": "Vivaldi", "chromium": "Chromium"}


def deno_path() -> str:
    """Where Deno is, or "" if it is missing."""
    import shutil
    try:
        import deno
        found = deno.find_deno_bin()
        if found and Path(found).is_file():
            return str(found)
    except Exception:  # noqa: BLE001 - not installed through pip: look on the PATH
        pass
    return shutil.which("deno") or ""


def guard_problems() -> list:
    """What is missing from the guard, in plain words. Empty when all is well."""
    out = []
    if not deno_path():
        out.append("Deno is missing")
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        out.append("yt-dlp's YouTube solver (yt-dlp-ejs) is missing")
    return out


def _login_file() -> Path:
    return config.DATA_DIR / "youtube.json"


def login_browser() -> str:
    """The browser whose YouTube login SMS uses, or "". SMS_YT_BROWSER in .env wins over the dashboard."""
    b = (config.env("SMS_YT_BROWSER") or "").strip().lower()
    if not b and not config.ACCOUNTS:
        try:
            b = str(json.loads(_login_file().read_text(encoding="utf-8")).get("browser") or "")
        except (OSError, ValueError):
            b = ""
    return b if b in BROWSERS else ""


def guard_opts() -> dict:
    """Added to every yt-dlp call: Deno's exact location."""
    d = deno_path()
    return {"js_runtimes": {"deno": {"path": d}}} if d else {}


# The browser login is read once and kept in memory while SMS runs, not read again for every download: on a Mac each
# read of Chrome's login asks for the keychain password. Only the YouTube and Google parts are kept, so nothing
# else from the browser is ever sent anywhere. It is read again when the browser is changed in Settings, or when
# YouTube refuses SMS anyway (the login may have run out).
_login = {"browser": "", "jar": None}
_login_lock = threading.Lock()


def _read_login(browser: str):
    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
    jar = YoutubeDLCookieJar()
    for c in extract_cookies_from_browser(browser):
        d = (c.domain or "").lstrip(".").lower()
        if d in ("youtube.com", "google.com") or d.endswith((".youtube.com", ".google.com")):
            jar.set_cookie(c)
    return jar


def login_jar():
    """The YouTube login from the chosen browser, or None (none chosen, or it couldn't be read: that is
    remembered too, so a refused keychain isn't asked again on every download, only when the browser is chosen again)."""
    b = login_browser()
    if not b:
        return None
    with _login_lock:
        if _login["browser"] != b or _login["jar"] is None:
            try:
                _login.update(browser=b, jar=_read_login(b))
            except Exception:  # noqa: BLE001
                _login.update(browser=b, jar=False)
        return _login["jar"] or None


def forget_login():
    with _login_lock:
        _login.update(browser="", jar=None)


def new_ydl(opts: dict):
    """A yt-dlp downloader with the guard and, if one is chosen, the browser login (read once, see above)."""
    import yt_dlp
    y = yt_dlp.YoutubeDL({**guard_opts(), **opts})
    jar = login_jar()                        # None: no login, or the browser can't be read (Settings says why)
    if jar is not None:
        y.__dict__["cookiejar"] = jar        # used instead of yt-dlp reading the browser itself
    return y


def login_status(check=False) -> dict:
    b = login_browser()
    out = {"browser": b, "browsers": BROWSERS, "from_env": bool((config.env("SMS_YT_BROWSER") or "").strip()),
           "guard": guard_problems()}
    if b and check:
        name = BROWSERS[b]
        try:
            jar = login_jar()
            if jar is None:
                raise RuntimeError("could not read the browser")
            signed_in = any(c.domain.endswith("youtube.com") and c.name in ("LOGIN_INFO", "SID", "__Secure-3PSID", "SAPISID")
                            for c in jar)
            out["ok"] = signed_in
            out["note"] = (f"Found your YouTube login in {name}. YouTube links will use it." if signed_in else
                           f"{name} isn't signed in to YouTube. Open youtube.com in {name}, sign in, then choose it here again.")
        except Exception:  # noqa: BLE001
            out["ok"] = False
            out["note"] = (f"SMS couldn't read {name}'s login. Close {name} completely and choose it again"
                           + (", and click Allow if your Mac asks for the keychain password." if b in ("chrome", "brave", "edge", "opera", "vivaldi", "chromium") else ".")
                           + (" On a Mac, Safari also needs Full Disk Access for Terminal." if b == "safari" else "")
                           + " If it still fails, try Firefox.")
    return out


def set_login_browser(b: str) -> dict:
    global _yt_refused_until
    b = (b or "").strip().lower()
    if b and b not in BROWSERS:
        raise ValueError("Choose one of the browsers in the list.")
    _login_file().write_text(json.dumps({"browser": b}), encoding="utf-8")
    forget_login()
    _yt_refused_until = 0.0          # try YouTube again straight away
    return login_status(check=True)


def is_youtube(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse((url or "").strip()).hostname or "").lower()
    return host in ("youtube.com", "youtu.be") or host.endswith((".youtube.com", ".youtu.be"))


def youtube_paused() -> bool:
    return time.time() < _yt_refused_until


def check_youtube(url: str):
    """Raise YouTubeBlocked straight away if YouTube is refusing this server right now."""
    if is_youtube(url) and youtube_paused():
        raise YouTubeBlocked(YT_BLOCKED)


def note_refusal(url: str, err) -> bool:
    """If this error is YouTube refusing the server, pause YouTube links for a while. True if it was."""
    global _yt_refused_until
    if is_youtube(url) and _BOT_CHECK.search(str(err)):
        _yt_refused_until = time.time() + YT_PAUSE
        forget_login()                       # read the browser login again next time, in case it ran out
        return True
    return False


@contextlib.contextmanager
def _youtube_guard(url):
    check_youtube(url)
    try:
        yield
    except YouTubeBlocked:
        raise
    except Exception as e:  # noqa: BLE001
        if note_refusal(url, e):
            raise YouTubeBlocked(YT_BLOCKED) from None
        raise


def site_allowed(url: str) -> bool:
    """Links are fetched by the server, so only known video sites are accepted (never arbitrary addresses)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == s or host.endswith("." + s) for s in config.ALLOWED_SITES)


def run(cmd, cwd=None, timeout=3600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(str(cmd[0])).name} took too long and was stopped.")
    if p.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {p.stderr[-1500:]}")
    return p.stdout


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _ydl(opts):
    # a channel or playlist link is answered as "a list" straight away, never walked video by video
    return new_ydl({"noplaylist": True, "quiet": True, "no_warnings": True, "logger": _Quiet(),
                             "extract_flat": "in_playlist", "playlistend": 1, "socket_timeout": 20,
                             "concurrent_fragment_downloads": 4, **opts})


def _video_format(height):
    return f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b"


def _biggest(job_dir: Path, stem: str) -> Path:
    files = sorted((p for p in job_dir.glob(stem + ".*") if p.suffix not in (".part", ".ytdl")),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("Download finished but no file was produced.")
    return files[0]


def info(url: str) -> dict:
    """Title, channel and length, without downloading anything."""
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
    return {"title": i.get("title") or "", "uploader": i.get("uploader") or i.get("channel") or "",
            "duration": float(i.get("duration") or 0), "is_live": bool(i.get("is_live")),
            "thumbnail": i.get("thumbnail") or "", "kind": i.get("_type") or "video"}


def youtube_words(url: str):
    """YouTube's own transcript of a video, word by word: ([{'w', 's', 'e'}, ...], language), or None if the video
    has none. Only the transcript in the language that was spoken is used, never a translation."""
    if not is_youtube(url):
        return None
    import json
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
        auto = i.get("automatic_captions") or {}
        key = next((k for k in auto if k.endswith("-orig")), None)
        if not key and i.get("language") in auto:
            key = i["language"]
        track = next((f for f in auto.get(key) or [] if f.get("ext") == "json3" and f.get("url")), None) if key else None
        if not track:
            return None
        raw = ydl.urlopen(track["url"]).read(40_000_000)
    return _caption_words(json.loads(raw)), key.replace("-orig", "")


def youtube_english_words(url: str):
    """YouTube's English captions of a video spoken in another language: ones a person wrote if there are any,
    otherwise YouTube's own translation of its transcript. Word list like youtube_words, or None."""
    if not is_youtube(url):
        return None
    import json
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
        tracks = []
        for group in (i.get("subtitles") or {}, i.get("automatic_captions") or {}):
            for k in ("en", "en-US", "en-GB"):
                tracks.append(next((f for f in group.get(k) or [] if f.get("ext") == "json3" and f.get("url")), None))
        track = next((t for t in tracks if t), None)
        if not track:
            return None
        raw = ydl.urlopen(track["url"]).read(40_000_000)
    words = _caption_words(json.loads(raw))
    return words or None


def _caption_words(data: dict) -> list:
    out = []
    for ev in data.get("events") or []:
        segs = [x for x in ev.get("segs") or [] if (x.get("utf8") or "").strip()]
        if not segs:
            continue
        t0, dur = float(ev.get("tStartMs") or 0), float(ev.get("dDurationMs") or 0)
        timed = len(segs) > 1 or "tOffsetMs" in segs[0]
        for x in segs:
            parts = x["utf8"].split()
            for n, w in enumerate(parts):
                if re.fullmatch(r"[\[(].*[\])]", w) or w in ("[", "]", "__"):
                    continue                      # [Music], [Applause], and words YouTube blanked out
                # a line with no word timings: its words are spread evenly over the time the line was on screen
                at = t0 + float(x.get("tOffsetMs") or 0) if timed else t0 + dur * n / max(1, len(parts))
                out.append({"w": w, "s": round(at / 1000, 2)})
    out.sort(key=lambda w: w["s"])
    for n, w in enumerate(out):
        nxt = out[n + 1]["s"] if n + 1 < len(out) else w["s"] + 0.4
        w["e"] = round(max(w["s"] + 0.08, min(nxt, w["s"] + 0.6)), 2)
    return out


_PREVIEWS: dict = {}


def preview(url: str) -> dict:
    """What the user will get from a link, checked before any work starts. The thumbnail comes back inline
    (the page may only load images from this site). Cached, so retyping a link costs nothing."""
    url = url.strip()
    if url in _PREVIEWS:
        return _PREVIEWS[url]
    i = info(url)
    if i["kind"] != "video" or not i["title"]:
        raise ValueError("That link is a channel or playlist, not a single video.")
    thumb = ""
    t = i.get("thumbnail") or ""
    if t.startswith("https://"):
        import base64
        import urllib.request
        try:
            req = urllib.request.Request(t, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                data = r.read(600_000 + 1)
            if ctype in ("image/jpeg", "image/png", "image/webp") and len(data) <= 600_000:
                thumb = f"data:{ctype};base64," + base64.b64encode(data).decode()
        except Exception:
            thumb = ""
    out = {"title": i["title"][:200], "uploader": i["uploader"][:120], "duration": i["duration"],
           "is_live": i["is_live"], "thumb": thumb}
    if len(_PREVIEWS) > 300:
        _PREVIEWS.pop(next(iter(_PREVIEWS)))
    _PREVIEWS[url] = out
    return out


def save_thumb(job_dir: Path, url: str = "", video: Path = None) -> bool:
    """A small picture for the video list: the link's own thumbnail, or a frame from an uploaded file."""
    import base64
    out = Path(job_dir) / "clips" / "thumb.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if url:
            p = _PREVIEWS.get(url.strip()) or preview(url)
            if not p.get("thumb"):
                return False
            out.write_bytes(base64.b64decode(p["thumb"].split(",", 1)[1]))
            return True
        if video:
            d = probe(video).get("duration") or 0
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{min(3.0, d / 3):.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)])
            return out.is_file()
    except Exception:
        return False
    return False


def download_audio(url: str, job_dir: Path, on_progress=None) -> Path:
    """Audio only: a few seconds even for long videos, and all the AI needs to pick clips."""
    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if on_progress and d.get("status") == "downloading" and total:
            on_progress(d.get("downloaded_bytes", 0) / total)

    with _youtube_guard(url), _ydl({"format": "ba[ext=m4a]/ba/worst", "outtmpl": str(job_dir / "audio_src.%(ext)s"),
               "progress_hooks": [hook]}) as ydl:
        ydl.download([url])
    return _biggest(job_dir, "audio_src")


def download(url: str, job_dir: Path, height=720) -> Path:
    """The whole video. Used for shorter videos, where this is quicker than fetching pieces."""
    with _youtube_guard(url), _ydl({"format": _video_format(height), "merge_output_format": "mp4",
                                    "outtmpl": str(job_dir / "source.%(ext)s")}) as ydl:
        ydl.download([url])
    return _biggest(job_dir, "source")


def download_section(url: str, out_dir: Path, stem: str, start: float, end: float, height=720, exact=False) -> Path:
    """Just one stretch of the video. The cut lands on a keyframe, so find_offset() pins the true start; with
    exact=True the cut is made at exactly `start` instead (slower), for when there is nothing to match against."""
    from yt_dlp.utils import download_range_func
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {"format": _video_format(height), "merge_output_format": "mp4", "outtmpl": str(out_dir / (stem + ".%(ext)s")),
            "download_ranges": download_range_func(None, [(max(0.0, start), end)])}
    if exact:
        opts["force_keyframes_at_cuts"] = True
    with _youtube_guard(url), _ydl(opts) as ydl:
        ydl.download([url])
    return _biggest(out_dir, stem)


def find_offset(full: np.ndarray, part: np.ndarray, expected: float, search=20.0) -> float:
    """Where in the full audio (seconds) a downloaded piece really begins, by matching the sound itself."""
    n = min(len(part), 30 * 16000)
    a0 = max(0, int((expected - search) * 16000))
    hay = full[a0: int((expected + search) * 16000) + n].astype(np.float32)
    needle = part[:n].astype(np.float32)
    if n < 16000 or len(hay) < n or float(np.abs(needle).mean()) < 20:
        return expected  # too short or silent to match: trust the requested time
    size = 1 << (len(hay) + n).bit_length()
    corr = np.fft.irfft(np.fft.rfft(hay, size) * np.conj(np.fft.rfft(needle, size)), size)[: len(hay) - n + 1]
    return (a0 + int(np.argmax(corr))) / 16000


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RuntimeError("This file has no video track.")
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise RuntimeError("This file has no audio track, so there is nothing to transcribe.")
    w, h = int(v["width"]), int(v["height"])
    if w * h > 3840 * 2160 or w < 16 or h < 16:
        raise RuntimeError("This video's resolution isn't supported (4K is the largest).")
    rot = str((v.get("side_data_list") or [{}])[0].get("rotation", "0"))
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    return {"width": w, "height": h, "duration": float(data["format"]["duration"])}


def extract_audio(src: Path, wav: Path):
    # first_pts=0 pads any gap before the audio starts, so second 0 of the wav is second 0 of the file
    # -t: never more audio than the longest video allowed, whatever the file claims about itself
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-t", str(int(config.MAX_VIDEO_HOURS * 3600) + 60),
         "-af", "aresample=async=1:first_pts=0", "-c:a", "pcm_s16le", str(wav)])


def load_audio(wav: Path) -> np.ndarray:
    data, sr = sf.read(str(wav), dtype="int16")
    assert sr == 16000
    return data


def loudness_per_second(audio: np.ndarray) -> np.ndarray:
    """RMS level in dB for each second of audio."""
    n = len(audio) // 16000
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    x = audio[: n * 16000].reshape(n, 16000).astype(np.float32) / 32768.0
    rms = np.sqrt((x ** 2).mean(axis=1) + 1e-10)
    return (20 * np.log10(rms)).astype(np.float32)
