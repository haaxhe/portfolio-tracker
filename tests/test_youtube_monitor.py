import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from backend import youtube_monitor
from backend.youtube_monitor import MarketMention, TranscriptSegment, Video


class YouTubeMonitorTests(unittest.TestCase):
    def test_extracts_market_snippets_without_llm(self):
        video = Video(
            video_id="abc123",
            channel_id="channel",
            channel_name="Creator",
            title="Daily update",
            url="https://www.youtube.com/watch?v=abc123",
            published_at=datetime.now(timezone.utc),
        )
        segments = [
            TranscriptSegment(0, "This part is personal housekeeping."),
            TranscriptSegment(12, "The Nasdaq and software stocks are breaking out after earnings."),
            TranscriptSegment(25, "This closing segment is unrelated."),
        ]

        snippets = youtube_monitor._matching_snippets(video, segments, ["nasdaq", "earnings"])

        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0]["timestamp"], "0:12")
        self.assertIn("software stocks", snippets[0]["text"])
        self.assertTrue(snippets[0]["url"].endswith("&t=12s"))

    def test_persists_mentions_as_json_fields(self):
        old_db_path = youtube_monitor.settings.DB_PATH
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            db_path = tmp.name
        try:
            youtube_monitor.settings.DB_PATH = db_path
            video = Video(
                video_id="abc123",
                channel_id="channel",
                channel_name="Creator",
                title="Market update",
                url="https://www.youtube.com/watch?v=abc123",
                published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            mention = MarketMention(
                video=video,
                score=4,
                tickers=["QQQ"],
                themes=["indices"],
                snippets=[{"timestamp": "0:12", "text": "QQQ target hit.", "url": video.url}],
                transcript_status="captions_json3",
            )

            youtube_monitor.save_market_mentions([mention], user_id="test-user")
            rows = youtube_monitor.load_market_mentions(user_id="test-user")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["video_id"], "abc123")
            self.assertEqual(rows[0]["tickers"], ["QQQ"])
            self.assertEqual(rows[0]["themes"], ["indices"])
            self.assertEqual(rows[0]["snippets"][0]["text"], "QQQ target hit.")
            self.assertEqual(rows[0]["summary"], {})
            self.assertEqual(rows[0]["summary_status"], "not_requested")
        finally:
            youtube_monitor.settings.DB_PATH = old_db_path
            os.unlink(db_path)

    def test_config_defaults(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            json.dump({"sources": []}, tmp)
            path = tmp.name
        try:
            config = youtube_monitor.load_config(path)
            self.assertEqual(config["lookback_days"], 3)
            self.assertEqual(config["max_videos_per_channel"], 5)
            self.assertIn("market", config["keywords"])
        finally:
            os.unlink(path)

    def test_keyword_matching_uses_whole_terms(self):
        score, themes = youtube_monitor._score_text("Here we go again.", ["ai"])

        self.assertEqual(score, 0)
        self.assertEqual(themes, [])

    def test_persists_llm_summary(self):
        old_db_path = youtube_monitor.settings.DB_PATH
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            db_path = tmp.name
        try:
            youtube_monitor.settings.DB_PATH = db_path
            video = Video(
                video_id="abc123",
                channel_id="channel",
                channel_name="Creator",
                title="QQQ target hit",
                url="https://www.youtube.com/watch?v=abc123",
                published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            mention = MarketMention(
                video=video,
                score=5,
                tickers=["QQQ"],
                themes=["indices"],
                snippets=[{"timestamp": "0:12", "text": "QQQ target hit.", "url": video.url}],
                transcript_status="captions_json3",
            )
            youtube_monitor.save_market_mentions([mention], user_id="test-user")

            youtube_monitor._save_summary(
                "abc123",
                {"stance": "bullish", "headline": "QQQ hit target"},
                "summarized",
                "test-user",
            )
            rows = youtube_monitor.load_market_mentions(user_id="test-user")

            self.assertEqual(rows[0]["summary_status"], "summarized")
            self.assertEqual(rows[0]["summary"]["headline"], "QQQ hit target")
        finally:
            youtube_monitor.settings.DB_PATH = old_db_path
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
