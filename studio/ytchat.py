"""YouTube live chat, read the way YouTube's own chat window reads it: no account, no API key, no quota.

The watch page holds a "continuation" for the chat. Asking for it returns a batch of messages, the next continuation
and how long to wait before asking again. This is the same exchange yt-dlp uses to save live chats, and its helpers
(page parsing, request headers, message timestamps) are reused here, so when YouTube changes, updating yt-dlp
fixes this too. Requests go through the same yt-dlp downloader as everything else, so the YouTube login chosen in
Settings is used when there is one.

Only the time of each message is used, never its words: chat is untrusted text from strangers.
"""
from __future__ import annotations

import json
from collections import deque

from . import media

WATCH = "https://www.youtube.com/watch?v={}"
CHAT_PAGE = "https://www.youtube.com/live_chat?continuation={}"
CHAT_API = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={}"


class NoChat(Exception):
    """This stream has no chat that can be read (turned off, members only, or not a live stream)."""


def message_times(chat: dict) -> list:
    """The times (milliseconds since 1970) of the chat messages in one batch. Super Chats and memberships count:
    they are people reacting too. Tickers and banners repeat earlier messages, so they don't."""
    from yt_dlp.downloader.youtube_live_chat import YoutubeLiveChatFD
    out = []
    for action in chat.get("actions") or []:
        if isinstance(action, dict) and "addChatItemAction" in action:
            ts = YoutubeLiveChatFD.parse_live_timestamp(action)
            if ts is not None:
                out.append(ts)
    return out


def next_step(chat: dict):
    """(next continuation, seconds to wait), or (None, 0) when the chat has ended."""
    for c in chat.get("continuations") or []:
        data = c.get("invalidationContinuationData") or c.get("timedContinuationData") or {}
        if data.get("continuation"):
            wait = (data.get("timeoutMs") or 5000) / 1000
            return data["continuation"], min(10.0, max(1.0, float(wait)))
    return None, 0.0


def all_messages(chat: dict):
    """The continuation for "Live chat" (every message) instead of the default "Top chat", which YouTube filters
    and smooths so heavily on busy streams that chat never seems to spike. Same place yt-dlp looks."""
    try:
        items = chat["header"]["liveChatHeaderRenderer"]["viewSelector"]["sortFilterSubMenuRenderer"]["subMenuItems"]
        return items[1]["continuation"]["reloadContinuationData"]["continuation"]
    except (KeyError, IndexError, TypeError):
        return None


class Placer:
    """Turns each message's time into how many seconds ago it was written, without trusting this computer's clock
    to match YouTube's (a clock a minute off would put every spike a minute off). The newest message in a batch
    is taken as just written, give or take the smallest delay seen lately; the others keep their spacing."""

    def __init__(self):
        self.lags = deque(maxlen=30)

    def ages(self, times_ms, now: float) -> list:
        if not times_ms:
            return []
        self.lags.append(now - max(times_ms) / 1000)
        floor = min(self.lags)
        return [max(0.0, now - ms / 1000 - floor) for ms in times_ms]


def read(video_id: str, on_messages, stop, on_connected=None) -> None:
    """Follow a live stream's chat until it ends or `stop` (a threading.Event) is set. Calls on_messages(times) with
    each batch. The first batch is the backlog already on screen when reading starts: it's passed with backlog=True."""
    from yt_dlp.extractor.youtube import YoutubeBaseInfoExtractor
    from yt_dlp.networking import Request
    from yt_dlp.utils import try_get

    with media.new_ydl({"quiet": True, "no_warnings": True, "logger": media._Quiet(), "socket_timeout": 20}) as ydl:
        ie = YoutubeBaseInfoExtractor(ydl)
        page = ydl.urlopen(WATCH.format(video_id)).read().decode("utf-8", "replace")
        data = ie.extract_yt_initial_data(video_id, page, fatal=False) or {}
        cont = try_get(data, lambda x: x["contents"]["twoColumnWatchNextResults"]["conversationBar"]["liveChatRenderer"]
                       ["continuations"][0]["reloadContinuationData"]["continuation"], str)
        if not cont:
            raise NoChat("This stream's chat is turned off or not public.")
        ytcfg = ie.extract_ytcfg(video_id, page) or {}
        key, context = ytcfg.get("INNERTUBE_API_KEY"), ytcfg.get("INNERTUBE_CONTEXT")
        if not key or not context:
            raise NoChat("YouTube didn't give what's needed to read this chat.")
        visitor = try_get(context, lambda x: x["client"]["visitorData"], str)

        def chat_page(c):
            html = ydl.urlopen(CHAT_PAGE.format(c)).read().decode("utf-8", "replace")
            return try_get(ie.extract_yt_initial_data(video_id, html, fatal=False) or {},
                           lambda x: x["continuationContents"]["liveChatContinuation"], dict) or {}

        chat = chat_page(cont)
        unfiltered = all_messages(chat)
        if unfiltered and unfiltered != cont:       # switch from "Top chat" to "Live chat": every message
            chat = chat_page(unfiltered) or chat
        if on_connected:
            on_connected()
        on_messages(message_times(chat), backlog=True)
        cont, wait = next_step(chat)

        headers = ie.generate_api_headers(ytcfg=ytcfg, visitor_data=visitor)
        headers["content-type"] = "application/json"
        while cont and not stop.wait(wait):
            body = json.dumps({"context": context, "continuation": cont}).encode()
            got = json.loads(ydl.urlopen(Request(CHAT_API.format(key), data=body, headers=headers)).read())
            chat = try_get(got, lambda x: x["continuationContents"]["liveChatContinuation"], dict) or {}
            on_messages(message_times(chat), backlog=False)
            cont, wait = next_step(chat)
