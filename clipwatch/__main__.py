"""Clip watcher: records a Twitch stream, watches its chat, and clips the moments where chat suddenly gets busy.

    ./watch.sh <twitch_channel> [--min 10] [--model small]

What it does, step by step:
  1. streamlink records the live stream into rec/.
  2. It reads the channel's chat anonymously (no account or token needed).
  3. It counts chat messages in 10-second slices. A slice is a "spike" when it has at least 2.5 times the usual
     number of messages (the middle value of the last 3 minutes) and at least --min messages.
  4. For each spike, ffmpeg cuts 20 seconds before and 10 seconds after into clips/, cropped to a 9:16 vertical
     video from the middle of the picture.
  5. Whisper writes subtitles (.srt) next to each clip.

Stop it with Ctrl+C. Clips already started are finished first. It also stops on its own when the stream ends.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .spikes import SpikeFinder

BUCKET = 10          # seconds per chat slice
BEFORE = 20          # seconds kept before the spike
AFTER = 10           # seconds kept after the spike
SETTLE = 5           # extra seconds to wait so the recording surely holds the end of the clip

IRC = os.environ.get("CLIPWATCH_IRC", "irc.chat.twitch.tv:6667")   # changed only by the tests
NICK = "justinfan12345"                                              # Twitch's built-in anonymous, read-only login

_print_lock = threading.Lock()


def say(msg: str) -> None:
    with _print_lock:
        print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def channel_name(raw: str) -> str:
    """Accepts "name", "#name" or a twitch.tv link, and returns the plain lowercase name."""
    name = re.sub(r"^(https?://)?(www\.)?twitch\.tv/", "", raw.strip(), flags=re.I).strip("#/ ").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,25}", name):
        raise SystemExit(f"'{raw}' doesn't look like a Twitch channel name.")
    return name


# ---------------------------------------------------------------- 1. recording

class Recorder:
    """Runs streamlink, which writes the stream into one growing .ts file."""

    def __init__(self, channel: str, folder: Path, quality: str):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{channel}-{datetime.now():%Y%m%d-%H%M%S}.ts"
        self.started_at: float | None = None      # the moment the first video arrived: second 0 of the file
        cmd = ["streamlink", "--force", "--output", str(self.path), f"https://twitch.tv/{channel}", quality]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.log: list[str] = []
        threading.Thread(target=self._read_log, daemon=True).start()

    def _read_log(self) -> None:
        for line in self.proc.stdout:
            self.log.append(line.rstrip())

    def wait_for_video(self, timeout: float = 60) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.path.exists() and self.path.stat().st_size > 0:
                self.started_at = time.time()
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.2)
        return False

    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------- 2. chat

class Chat:
    """Reads the channel's chat over IRC and counts the messages. Reconnects by itself if the connection drops."""

    def __init__(self, channel: str):
        self.channel = channel
        self.count = 0
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.connected = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def take(self) -> int:
        """How many messages arrived since the last call."""
        with self.lock:
            n, self.count = self.count, 0
        return n

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        host, port = IRC.rsplit(":", 1)
        wait = 2
        while not self.stopping.is_set():
            try:
                self._session(host, int(port))
                wait = 2
            except OSError as e:
                if self.stopping.is_set():
                    break
                self.connected.clear()
                say(f"Chat connection lost ({e}). Trying again in {wait} s.")
                self.stopping.wait(wait)
                wait = min(wait * 2, 60)

    def _session(self, host: str, port: int) -> None:
        with socket.create_connection((host, port), timeout=10) as s:
            s.settimeout(1)
            s.sendall(f"NICK {NICK}\r\nJOIN #{self.channel}\r\n".encode())
            buf, quiet_since = b"", time.time()
            while not self.stopping.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    if time.time() - quiet_since > 360:     # Twitch pings every 5 minutes; silence longer than that means a dead line
                        raise OSError("no reply from the chat server")
                    continue
                if not data:
                    raise OSError("the chat server closed the connection")
                quiet_since = time.time()
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        s.sendall(("PONG" + line[4:] + "\r\n").encode())
                    elif f" JOIN #{self.channel}" in line and not self.connected.is_set():
                        self.connected.set()
                        say(f"Reading chat for #{self.channel}.")
                    elif f" PRIVMSG #{self.channel} " in line:
                        with self.lock:
                            self.count += 1


# ---------------------------------------------------------------- 4. cutting

def cut(recording: Path, start: float, length: float, out: Path) -> bool:
    """Cuts [start, start+length] seconds out of the recording, center-cropped to 9:16 at 1080x1920."""
    vf = "crop='min(iw,trunc(ih*9/32)*2)':ih,scale=1080:1920,setsar=1"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, start):.2f}", "-i", str(recording),
           "-t", f"{length:.2f}", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        say(f"Couldn't cut {out.name}: {r.stderr.strip()[-300:] or 'ffmpeg failed'}")
        out.unlink(missing_ok=True)
        return False
    return True


# ---------------------------------------------------------------- 5. subtitles

class Subtitles:
    """Writes an .srt next to each clip, one clip at a time, so it never slows down the watching."""

    def __init__(self, model: str, language: str | None):
        self.model_name, self.language = model, language
        self.jobs: queue.Queue[Path | None] = queue.Queue()
        self.engine = self._pick()
        self.model = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _pick(self) -> str | None:
        try:
            import faster_whisper  # noqa: F401
            return "faster-whisper"
        except ImportError:
            pass
        if shutil.which("whisper"):
            return "whisper"
        say("Whisper isn't installed, so clips won't get subtitles. Install it with: pip install faster-whisper")
        return None

    def add(self, clip: Path) -> None:
        if self.engine:
            self.jobs.put(clip)

    def finish(self) -> None:
        self.jobs.put(None)
        self.thread.join()

    def _run(self) -> None:
        while (clip := self.jobs.get()) is not None:
            try:
                self._one(clip)
                say(f"Subtitles ready: {clip.with_suffix('.srt').name}")
            except Exception as e:  # noqa: BLE001 - one bad clip should not stop the others
                say(f"Couldn't write subtitles for {clip.name}: {e}")

    def _one(self, clip: Path) -> None:
        srt = clip.with_suffix(".srt")
        if self.engine == "whisper":
            cmd = ["whisper", str(clip), "--model", self.model_name, "--output_format", "srt",
                   "--output_dir", str(clip.parent), "--verbose", "False"]
            if self.language:
                cmd += ["--language", self.language]
            subprocess.run(cmd, check=True, capture_output=True)
            return
        if self.model is None:
            from faster_whisper import WhisperModel
            say(f"Loading the Whisper '{self.model_name}' model (downloads once, the first time)...")
            self.model = WhisperModel(self.model_name, device="auto", compute_type="default")
        segments, _ = self.model.transcribe(str(clip), language=self.language, vad_filter=True)
        with srt.open("w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                f.write(f"{i}\n{_stamp(seg.start)} --> {_stamp(seg.end)}\n{seg.text.strip()}\n\n")


def _stamp(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ---------------------------------------------------------------- putting it together

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="watch.sh", description="Record a Twitch stream and clip the moments chat goes wild.")
    ap.add_argument("channel", help="Twitch channel name or link")
    ap.add_argument("--min", type=int, default=10, help="fewest messages in 10 s that can count as a spike (default 10)")
    ap.add_argument("--ratio", type=float, default=2.5, help="how many times busier than usual a spike must be (default 2.5)")
    ap.add_argument("--quality", default="best", help="stream quality for streamlink (default best)")
    ap.add_argument("--model", default="small", help="Whisper model: tiny, base, small, medium, large-v3 (default small)")
    ap.add_argument("--language", default=None, help="spoken language for subtitles, like en or hi (default: detect)")
    ap.add_argument("--no-subs", action="store_true", help="don't write subtitles")
    ap.add_argument("--out", default=".", help="folder that gets rec/ and clips/ (default: here)")
    a = ap.parse_args(argv)

    channel = channel_name(a.channel)
    for tool in ("streamlink", "ffmpeg"):
        if not shutil.which(tool):
            print(f"{tool} isn't installed. See clipwatch/README.md.", file=sys.stderr)
            return 1
    out = Path(a.out)
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)

    say(f"Starting the recording of twitch.tv/{channel}...")
    rec = Recorder(channel, out / "rec", a.quality)
    chat = Chat(channel)
    if not rec.wait_for_video():
        rec.stop()
        chat.stop()
        detail = "\n".join(rec.log[-5:]) or "no video arrived"
        print(f"Couldn't record twitch.tv/{channel}. Is the channel live?\n{detail}", file=sys.stderr)
        return 1
    say(f"Recording to {rec.path}")

    subs = None if a.no_subs else Subtitles(a.model, a.language)
    finder = SpikeFinder(ratio=a.ratio, minimum=a.min)
    pending: list[tuple[float, float, Path]] = []   # (ready at, wall time of the spike slice start, clip path)
    last_end = 0.0
    made = 0

    def cut_ready(force: bool = False) -> None:
        nonlocal made
        for job in [j for j in pending if force or time.time() >= j[0]]:
            pending.remove(job)
            _, moment, path = job
            start = moment - BEFORE - rec.started_at
            if cut(rec.path, start, BEFORE + AFTER + min(0.0, start), path):
                made += 1
                say(f"Clip saved: {path}")
                if subs:
                    subs.add(path)

    slice_start = time.time()
    chat.take()                                  # start counting from now
    try:
        while rec.running():
            time.sleep(0.2)
            cut_ready()
            if time.time() - slice_start < BUCKET:
                continue
            n = chat.take()
            usual = finder.usual()
            if finder.add(n):
                if slice_start < last_end:
                    say(f"Spike: {n} messages (usual {usual:g}) - already inside the last clip.")
                else:
                    name = f"{channel}-{datetime.fromtimestamp(slice_start):%Y%m%d-%H%M%S}-{n}msgs.mp4"
                    pending.append((slice_start + AFTER + SETTLE, slice_start, clips / name))
                    last_end = slice_start + AFTER
                    say(f"Spike: {n} messages in {BUCKET} s (usual {usual:g}). Clipping...")
            slice_start += BUCKET
        say("The stream ended.")
    except KeyboardInterrupt:
        say("Stopping...")
    finally:
        rec.stop()
        chat.stop()
        cut_ready(force=True)
        if subs:
            subs.finish()
    say(f"Done. {made} clip(s) in {clips}/, recording in {rec.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
