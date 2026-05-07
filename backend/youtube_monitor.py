"""Low-cost YouTube market commentary monitor.

This module runs a deterministic filter first. Optional LLM summarization only
executes for videos that passed the cheap filter.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from xml.etree import ElementTree

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

YOUTUBE_RSS_URL = "https://www.youtube.com/feeds/videos.xml"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
DEFAULT_CONFIG_PATH = Path("config/youtube_sources.json")
YOUTUBE_ORIGIN = "https://www.youtube.com"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

DEFAULT_KEYWORDS = [
    "stock", "stocks", "market", "nasdaq", "s&p", "qqq", "fed", "powell",
    "inflation", "rates", "tariff", "earnings", "guidance", "recession",
    "bullish", "bearish", "software", "hardware", "ai",
]
THEME_KEYWORDS = {
    "macro": ["fed", "powell", "inflation", "rates", "gdp", "labor", "jobs", "recession"],
    "indices": ["nasdaq", "s&p", "qqq", "spy", "index", "indices"],
    "earnings": ["earnings", "eps", "revenue", "guidance", "cash flow", "buyback"],
    "ai": ["ai", "software", "hardware", "compute", "cpu", "gpu", "inference"],
    "risk": ["tariff", "war", "iran", "oil", "commodity", "bearish"],
}
COMMON_WORDS = {
    "AI", "API", "CEO", "CFO", "CPU", "EPS", "ETF", "EV", "Fed", "GDP",
    "GPU", "IPO", "PMI", "SEC", "USA", "USD", "AND", "DID", "GO", "HERE",
    "HOLY", "IT", "JUST", "THE", "THIS",
}
SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stance": {
            "type": "string",
            "enum": ["bullish", "bearish", "mixed", "neutral", "unclear"],
        },
        "headline": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "tickers": {
            "type": "array",
            "items": {"type": "string"},
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "watch_items": {
            "type": "array",
            "items": {"type": "string"},
        },
        "source_timestamps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "timestamp": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["timestamp", "reason"],
            },
        },
    },
    "required": [
        "stance",
        "headline",
        "key_points",
        "tickers",
        "themes",
        "risks",
        "watch_items",
        "source_timestamps",
    ],
}


@dataclass(frozen=True)
class Video:
    video_id: str
    channel_id: str
    channel_name: str
    title: str
    url: str
    published_at: datetime


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    text: str

    @property
    def timestamp(self) -> str:
        total = int(self.start_seconds)
        minutes, seconds = divmod(total, 60)
        return f"{minutes}:{seconds:02d}"


@dataclass(frozen=True)
class MarketMention:
    video: Video
    score: int
    tickers: list[str]
    themes: list[str]
    snippets: list[dict[str, str]]
    transcript_status: str

    @property
    def url(self) -> str:
        return self.video.url


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_youtube_monitor_tables() -> None:
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS youtube_market_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            video_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            score INTEGER NOT NULL,
            tickers_json TEXT NOT NULL DEFAULT '[]',
            themes_json TEXT NOT NULL DEFAULT '[]',
            snippets_json TEXT NOT NULL DEFAULT '[]',
            transcript_status TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            summary_status TEXT NOT NULL DEFAULT 'not_requested',
            summary_model TEXT DEFAULT '',
            summarized_at TEXT DEFAULT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, video_id)
        );
        CREATE INDEX IF NOT EXISTS idx_youtube_mentions_user_created
            ON youtube_market_mentions(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_youtube_mentions_user_published
            ON youtube_market_mentions(user_id, published_at);
    """)
    _ensure_column(conn, "youtube_market_mentions", "summary_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "youtube_market_mentions", "summary_status", "TEXT NOT NULL DEFAULT 'not_requested'")
    _ensure_column(conn, "youtube_market_mentions", "summary_model", "TEXT DEFAULT ''")
    _ensure_column(conn, "youtube_market_mentions", "summarized_at", "TEXT DEFAULT NULL")
    conn.commit()
    conn.close()


def save_market_mentions(mentions: list[MarketMention], user_id: str | None = None) -> None:
    owner = user_id or settings.DEFAULT_USER_ID
    init_youtube_monitor_tables()
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    for mention in mentions:
        conn.execute(
            """INSERT INTO youtube_market_mentions
               (user_id, video_id, channel_id, channel_name, title, url, published_at,
                score, tickers_json, themes_json, snippets_json, transcript_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, video_id) DO UPDATE SET
                 channel_name = excluded.channel_name,
                 title = excluded.title,
                 url = excluded.url,
                 published_at = excluded.published_at,
                 score = excluded.score,
                 tickers_json = excluded.tickers_json,
                 themes_json = excluded.themes_json,
                 snippets_json = excluded.snippets_json,
                 transcript_status = excluded.transcript_status,
                 created_at = excluded.created_at""",
            (
                owner,
                mention.video.video_id,
                mention.video.channel_id,
                mention.video.channel_name,
                mention.video.title,
                mention.video.url,
                mention.video.published_at.isoformat(),
                mention.score,
                json.dumps(mention.tickers),
                json.dumps(mention.themes),
                json.dumps(mention.snippets),
                mention.transcript_status,
                now,
            ),
        )
    conn.commit()
    conn.close()


def load_market_mentions(limit: int = 25, user_id: str | None = None) -> list[dict[str, Any]]:
    owner = user_id or settings.DEFAULT_USER_ID
    init_youtube_monitor_tables()
    conn = _conn()
    rows = conn.execute(
        """SELECT * FROM youtube_market_mentions
           WHERE user_id = ?
           ORDER BY published_at DESC
           LIMIT ?""",
        (owner, limit),
    ).fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        item["tickers"] = json.loads(item.pop("tickers_json") or "[]")
        item["themes"] = json.loads(item.pop("themes_json") or "[]")
        item["snippets"] = json.loads(item.pop("snippets_json") or "[]")
        item["summary"] = json.loads(item.pop("summary_json") or "{}")
        results.append(item)
    return results


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or settings.YOUTUBE_MONITOR_CONFIG_PATH or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return {
            "lookback_days": 3,
            "max_videos_per_channel": 5,
            "sources": [],
            "keywords": DEFAULT_KEYWORDS,
        }
    data = json.loads(config_path.read_text())
    data.setdefault("lookback_days", 3)
    data.setdefault("max_videos_per_channel", 5)
    data.setdefault("sources", [])
    data.setdefault("keywords", DEFAULT_KEYWORDS)
    return data


def fetch_recent_videos(
    config: dict[str, Any],
    client: httpx.Client | None = None,
) -> list[Video]:
    close_client = client is None
    client = client or _client()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(config["lookback_days"]))
        max_per_channel = int(config["max_videos_per_channel"])
        videos: list[Video] = []
        for source in config["sources"]:
            if not source.get("enabled", True):
                continue
            channel_id = source["channel_id"]
            channel_videos = _fetch_rss_videos(client, source, cutoff, max_per_channel)
            if not channel_videos:
                channel_videos = _fetch_channel_page_videos(client, source, cutoff, max_per_channel)
            videos.extend(channel_videos)
        return videos
    finally:
        if close_client:
            client.close()


def _fetch_rss_videos(
    client: httpx.Client,
    source: dict[str, Any],
    cutoff: datetime,
    max_per_channel: int,
) -> list[Video]:
    try:
        channel_id = source["channel_id"]
        response = client.get(YOUTUBE_RSS_URL, params={"channel_id": channel_id})
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
    except Exception as exc:
        logger.info("YouTube RSS unavailable for %s: %s", source.get("name", "channel"), exc)
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    channel_name = source.get("name") or _text(root.find("atom:title", ns))
    videos = []
    for entry in root.findall("atom:entry", ns)[:max_per_channel]:
        video_id = _text(entry.find("yt:videoId", ns))
        title = _text(entry.find("atom:title", ns))
        published_at = _parse_datetime(_text(entry.find("atom:published", ns)))
        if not video_id or published_at < cutoff:
            continue
        videos.append(
            Video(
                video_id=video_id,
                channel_id=source["channel_id"],
                channel_name=channel_name,
                title=title,
                url=WATCH_URL.format(video_id=video_id),
                published_at=published_at,
            )
        )
    return videos


def _fetch_channel_page_videos(
    client: httpx.Client,
    source: dict[str, Any],
    cutoff: datetime,
    max_per_channel: int,
) -> list[Video]:
    channel_url = source.get("channel_url") or source.get("url")
    if not channel_url and source.get("handle"):
        channel_url = f"{YOUTUBE_ORIGIN}/@{source['handle'].lstrip('@')}/videos"
    if not channel_url:
        return []
    try:
        response = client.get(channel_url)
        response.raise_for_status()
        data = _extract_json_object(response.text, "ytInitialData")
    except Exception as exc:
        logger.info("YouTube channel page unavailable for %s: %s", source.get("name", "channel"), exc)
        return []
    if not data:
        return []

    videos = []
    seen = set()
    for renderer in _walk_key(data, "videoRenderer"):
        video_id = renderer.get("videoId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = _runs_text(renderer.get("title", {}))
        published_at = _relative_to_datetime(
            renderer.get("publishedTimeText", {}).get("simpleText", "")
        )
        if published_at < cutoff:
            continue
        videos.append(
            Video(
                video_id=video_id,
                channel_id=source["channel_id"],
                channel_name=source.get("name", ""),
                title=title,
                url=WATCH_URL.format(video_id=video_id),
                published_at=published_at,
            )
        )
        if len(videos) >= max_per_channel:
            break
    return videos


def analyze_video(
    video: Video,
    keywords: list[str] | None = None,
    client: httpx.Client | None = None,
) -> MarketMention | None:
    keywords = keywords or DEFAULT_KEYWORDS
    close_client = client is None
    client = client or _client()
    try:
        segments, transcript_status = fetch_transcript(video.video_id, client)
        if not segments:
            title_score, title_themes = _score_text(video.title, keywords)
            if title_score == 0:
                return None
            return MarketMention(
                video=video,
                score=title_score,
                tickers=_extract_tickers(video.title),
                themes=title_themes,
                snippets=[{"timestamp": "title", "text": video.title, "url": video.url}],
                transcript_status=transcript_status,
            )

        snippets = _matching_snippets(video, segments, keywords)
        if not snippets:
            return None
        text = " ".join(s["text"] for s in snippets)
        title_score, title_themes = _score_text(video.title, keywords)
        body_score, body_themes = _score_text(text, keywords)
        return MarketMention(
            video=video,
            score=title_score + body_score + len(snippets),
            tickers=sorted(set(_extract_tickers(video.title) + _extract_tickers(text))),
            themes=sorted(set(title_themes + body_themes)),
            snippets=snippets,
            transcript_status=transcript_status,
        )
    finally:
        if close_client:
            client.close()


def run_monitor(
    config_path: str | Path | None = None,
    user_id: str | None = None,
    persist: bool = True,
    summarize: bool | None = None,
) -> list[MarketMention]:
    config = load_config(config_path)
    client = _client()
    try:
        mentions: list[MarketMention] = []
        for video in fetch_recent_videos(config, client):
            mention = analyze_video(video, config["keywords"], client)
            if mention:
                mentions.append(mention)
        mentions.sort(key=lambda item: (item.score, item.video.published_at), reverse=True)
        if persist:
            save_market_mentions(mentions, user_id=user_id)
            should_summarize = settings.YOUTUBE_MONITOR_LLM_ENABLED if summarize is None else summarize
            if should_summarize:
                summarize_market_mentions(
                    mentions,
                    user_id=user_id,
                    limit=settings.YOUTUBE_MONITOR_SUMMARIZE_LIMIT,
                )
        return mentions
    finally:
        client.close()


def summarize_market_mentions(
    mentions: list[MarketMention],
    user_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Summarize already-filtered mentions and persist summary JSON."""
    if not mentions:
        return []
    if not settings.OPENAI_API_KEY:
        logger.info("Skipping YouTube summaries: OPENAI_API_KEY is not set")
        return []

    init_youtube_monitor_tables()
    owner = user_id or settings.DEFAULT_USER_ID
    max_items = limit or settings.YOUTUBE_MONITOR_SUMMARIZE_LIMIT
    summaries = []
    for mention in mentions[:max_items]:
        try:
            summary = summarize_market_mention(mention)
            _save_summary(
                mention.video.video_id,
                summary,
                status="summarized",
                user_id=owner,
            )
            summaries.append(summary)
        except Exception as exc:
            logger.info("Summary failed for %s: %s", mention.video.video_id, exc)
            _save_summary(
                mention.video.video_id,
                {"error": str(exc)[:500]},
                status="summary_error",
                user_id=owner,
            )
    return summaries


def summarize_market_mention(mention: MarketMention) -> dict[str, Any]:
    prompt = _summary_prompt(mention)
    with _openai_client() as client:
        response = client.post(
            OPENAI_RESPONSES_URL,
            json={
                "model": settings.OPENAI_MODEL,
                "instructions": (
                    "You summarize YouTube finance commentary for a portfolio tracker. "
                    "Extract what the creator said, not your own market view. "
                    "Do not give personalized financial advice. Be concise."
                ),
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "youtube_market_summary",
                        "schema": SUMMARY_SCHEMA,
                        "strict": True,
                    }
                },
                "max_output_tokens": 900,
            },
        )
        response.raise_for_status()
    data = response.json()
    output_text = data.get("output_text") or _extract_response_text(data)
    summary = json.loads(output_text)
    summary["model"] = settings.OPENAI_MODEL
    summary["video_id"] = mention.video.video_id
    summary["url"] = mention.video.url
    return summary


def _save_summary(
    video_id: str,
    summary: dict[str, Any],
    status: str,
    user_id: str,
) -> None:
    conn = _conn()
    conn.execute(
        """UPDATE youtube_market_mentions
           SET summary_json = ?, summary_status = ?, summary_model = ?, summarized_at = ?
           WHERE user_id = ? AND video_id = ?""",
        (
            json.dumps(summary),
            status,
            settings.OPENAI_MODEL,
            datetime.now(timezone.utc).isoformat(),
            user_id,
            video_id,
        ),
    )
    conn.commit()
    conn.close()


def _summary_prompt(mention: MarketMention) -> str:
    snippets = "\n".join(
        f"- {snippet['timestamp']}: {snippet['text']} ({snippet['url']})"
        for snippet in mention.snippets
    )
    return (
        f"Video title: {mention.video.title}\n"
        f"Channel: {mention.video.channel_name}\n"
        f"URL: {mention.video.url}\n"
        f"Detected tickers: {', '.join(mention.tickers) or 'none'}\n"
        f"Detected themes: {', '.join(mention.themes) or 'none'}\n\n"
        "Relevant transcript/title snippets:\n"
        f"{snippets}\n\n"
        "Return JSON with: stance, headline, key_points, tickers, themes, risks, "
        "watch_items, and source_timestamps. Keep key_points factual and grounded "
        "in the snippets."
    )


def _openai_client() -> httpx.Client:
    return httpx.Client(
        timeout=45,
        follow_redirects=True,
        headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )


def _extract_response_text(response: dict[str, Any]) -> str:
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    if not parts:
        raise ValueError("OpenAI response did not include output text")
    return "".join(parts)


def fetch_transcript(
    video_id: str,
    client: httpx.Client | None = None,
) -> tuple[list[TranscriptSegment], str]:
    close_client = client is None
    client = client or _client()
    try:
        page = client.get(WATCH_URL.format(video_id=video_id))
        page.raise_for_status()
        player = _extract_json_object(page.text, "ytInitialPlayerResponse")
        if not player:
            return [], "watch_page_unparsed"
        tracks = (
            player.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if not tracks:
            return [], "no_caption_tracks"
        for track in tracks:
            base_url = track.get("baseUrl")
            if not base_url:
                continue
            for fmt in ("json3", "srv3", "vtt"):
                transcript = _fetch_caption_url(client, base_url, fmt)
                if transcript:
                    return transcript, f"captions_{fmt}"
        return [], "caption_fetch_empty"
    except Exception as exc:
        logger.info("Transcript fetch failed for %s: %s", video_id, exc)
        return [], "transcript_error"
    finally:
        if close_client:
            client.close()


def _fetch_caption_url(
    client: httpx.Client,
    base_url: str,
    fmt: str,
) -> list[TranscriptSegment]:
    url = _with_query(base_url, {"fmt": fmt})
    response = client.get(url)
    if response.status_code >= 400 or not response.text.strip():
        return []
    if fmt == "json3":
        return _parse_json3(response.text)
    if fmt == "srv3":
        return _parse_srv3(response.text)
    return _parse_vtt(response.text)


def _matching_snippets(
    video: Video,
    segments: list[TranscriptSegment],
    keywords: list[str],
    max_snippets: int = 8,
) -> list[dict[str, str]]:
    pattern = _keyword_pattern(keywords)
    snippets: list[dict[str, str]] = []
    seen = set()
    for idx, segment in enumerate(segments):
        if not pattern.search(segment.text):
            continue
        window = segments[max(0, idx - 1): min(len(segments), idx + 2)]
        text = " ".join(part.text for part in window)
        key = text.lower()[:160]
        if key in seen:
            continue
        seen.add(key)
        timestamp = segment.timestamp
        snippets.append(
            {
                "timestamp": timestamp,
                "text": _compact(text),
                "url": f"{video.url}&t={int(segment.start_seconds)}s",
            }
        )
        if len(snippets) >= max_snippets:
            break
    return snippets


def _score_text(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    lowered = text.lower()
    score = len(_keyword_pattern(keywords).findall(text))
    themes = [
        theme
        for theme, theme_keywords in THEME_KEYWORDS.items()
        if _keyword_pattern(theme_keywords).search(lowered)
    ]
    return score, themes


def _extract_tickers(text: str) -> list[str]:
    cashtags = re.findall(r"\$([A-Z]{1,5})\b", text)
    candidates = cashtags + re.findall(r"\b[A-Z]{2,5}\b", text)
    return sorted({item for item in candidates if item not in COMMON_WORDS})


def _keyword_pattern(keywords: list[str]) -> re.Pattern:
    parts = []
    for keyword in sorted(set(keywords), key=len, reverse=True):
        escaped = re.escape(keyword)
        if re.match(r"^[A-Za-z0-9]+$", keyword):
            parts.append(rf"\b{escaped}\b")
        else:
            parts.append(escaped)
    return re.compile("|".join(parts), re.IGNORECASE)


def _parse_json3(text: str) -> list[TranscriptSegment]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    segments = []
    for event in data.get("events", []):
        parts = event.get("segs") or []
        caption_text = "".join(part.get("utf8", "") for part in parts)
        caption_text = _compact(caption_text)
        if caption_text:
            segments.append(
                TranscriptSegment(
                    start_seconds=float(event.get("tStartMs", 0)) / 1000,
                    text=caption_text,
                )
            )
    return segments


def _parse_srv3(text: str) -> list[TranscriptSegment]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []
    segments = []
    for elem in root.iter():
        if elem.tag.endswith("text") or elem.tag.endswith("p"):
            start = elem.attrib.get("start") or elem.attrib.get("t") or "0"
            raw_text = "".join(elem.itertext())
            caption_text = _compact(html.unescape(raw_text))
            if caption_text:
                start_seconds = float(start) / (1000 if elem.attrib.get("t") else 1)
                segments.append(TranscriptSegment(start_seconds=start_seconds, text=caption_text))
    return segments


def _parse_vtt(text: str) -> list[TranscriptSegment]:
    segments = []
    current_start: float | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if "-->" in line:
            if current_start is not None and current_lines:
                segments.append(TranscriptSegment(current_start, _compact(" ".join(current_lines))))
            current_lines = []
            current_start = _vtt_time_to_seconds(line.split("-->", 1)[0].strip())
        elif line and not line.startswith("WEBVTT") and current_start is not None:
            current_lines.append(re.sub(r"<[^>]+>", "", line))
    if current_start is not None and current_lines:
        segments.append(TranscriptSegment(current_start, _compact(" ".join(current_lines))))
    return segments


def _extract_json_object(text: str, marker: str) -> dict[str, Any] | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    start = text.find("{", idx)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start: pos + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _walk_key(value: Any, key: str):
    if isinstance(value, dict):
        if key in value and isinstance(value[key], dict):
            yield value[key]
        for child in value.values():
            yield from _walk_key(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_key(child, key)


def _runs_text(value: dict[str, Any]) -> str:
    if "simpleText" in value:
        return value["simpleText"]
    return "".join(run.get("text", "") for run in value.get("runs", []))


def _with_query(url: str, updates: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in updates.items():
        query[key] = [value]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _relative_to_datetime(value: str) -> datetime:
    now = datetime.now(timezone.utc)
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", value.lower())
    if not match:
        return now
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "minute":
        return now - timedelta(minutes=amount)
    if unit == "hour":
        return now - timedelta(hours=amount)
    if unit == "day":
        return now - timedelta(days=amount)
    if unit == "week":
        return now - timedelta(weeks=amount)
    if unit == "month":
        return now - timedelta(days=amount * 30)
    if unit == "year":
        return now - timedelta(days=amount * 365)
    return now


def _vtt_time_to_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return hours * 3600 + minutes * 60 + seconds


def _text(element: ElementTree.Element | None) -> str:
    return element.text.strip() if element is not None and element.text else ""


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _mention_to_dict(mention: MarketMention) -> dict[str, Any]:
    return {
        "video_id": mention.video.video_id,
        "channel_name": mention.video.channel_name,
        "title": mention.video.title,
        "url": mention.video.url,
        "published_at": mention.video.published_at.isoformat(),
        "score": mention.score,
        "tickers": mention.tickers,
        "themes": mention.themes,
        "snippets": mention.snippets,
        "transcript_status": mention.transcript_status,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor YouTube videos for market commentary.")
    parser.add_argument("--config", default=None, help="Path to YouTube monitor JSON config.")
    parser.add_argument("--no-save", action="store_true", help="Print results without writing SQLite.")
    parser.add_argument("--summarize", action="store_true", help="Use OpenAI to summarize matched videos.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum results to print.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    mentions = run_monitor(
        config_path=args.config,
        persist=not args.no_save,
        summarize=args.summarize,
    )
    payload = [_mention_to_dict(mention) for mention in mentions[: args.limit]]
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
