# Clip watcher

One command that records a Twitch stream, watches its chat, and clips the moments where chat suddenly gets busy.
It runs on its own and doesn't need the SMS server.

The same rule is built into the dashboard: a creator's **Setup → Clip on chat spikes** makes their live recordings
clip this way, with captions and posting.

```bash
./watch.sh <twitch_channel>
```

## What it does

1. **Records** the live stream into `rec/` with streamlink.
2. **Reads chat** anonymously over IRC (`irc.chat.twitch.tv:6667`, as `justinfan12345`). No Twitch account or
   token needed.
3. **Finds spikes.** Counts chat messages in 10-second slices. A slice is a spike when it has at least **2.5 times**
   the usual number of messages (the middle value of the last 3 minutes) **and** at least `--min` messages.
   Nothing counts during the first minute, while it learns what "usual" is.
4. **Cuts a clip** for each spike: 20 seconds before and 10 seconds after, center-cropped to a 9:16 vertical
   video at 1080x1920, saved in `clips/`. A spike whose clip would overlap the last one doesn't make a second clip.
5. **Writes subtitles**: Whisper makes an `.srt` next to each clip, in the background.

Stop it with Ctrl+C. Clips already started are finished first. It also stops by itself when the stream ends.

## Setup (once)

You need Python 3.10+ and ffmpeg (see the main README), then:

```bash
pip install -r clipwatch/requirements.txt
```

The first clip downloads the Whisper model, once: `small` is about 500 MB.

## Options

| Option | What it does |
|---|---|
| `--min 10` | fewest messages in 10 s that can count as a spike. Raise it for big channels. |
| `--ratio 2.5` | how many times busier than usual a spike must be |
| `--model small` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3`. Bigger is better and slower. |
| `--language en` | the spoken language, if Whisper guesses wrong |
| `--no-subs` | skip subtitles |
| `--quality best` | stream quality, like `720p60` or `480p` |
| `--out <folder>` | where `rec/` and `clips/` go (default: this folder) |

Example for a big channel, English subtitles:

```bash
./watch.sh somestreamer --min 40 --language en
```

## Good to know

- Clips are placed by the clock: second 0 of the recording is when the first video arrived. Twitch ad breaks are
  left out of the recording, so after an ad the clips can start a few seconds early or late.
- `rec/` keeps the whole stream and grows by roughly 3 GB an hour at 1080p60. Delete old recordings when you're
  done with them.
- `rec/` and `clips/` are not saved to git.
- Tests: `python tests/test_clipwatch.py` (uses a fake stream and fake chat, nothing online).
