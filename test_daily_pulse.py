import os
import time
import unittest
import urllib.error
from unittest.mock import patch

from daily_pulse_app import is_newer_version, parse_version
from daily_pulse import Item, Source, build_digest, collect_items, parse_rss, substitute_body


class DailyPulseTests(unittest.TestCase):
    def test_parse_rss_items(self):
        raw = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Hello</title>
              <link>https://example.com/hello</link>
              <pubDate>Wed, 29 Apr 2026 08:00:00 GMT</pubDate>
              <description><![CDATA[<p>World</p>]]></description>
            </item>
          </channel>
        </rss>
        """

        items = parse_rss(Source(name="Example", url="https://example.com/feed"), raw)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Hello")
        self.assertEqual(items[0].url, "https://example.com/hello")
        self.assertEqual(items[0].source, "Example")
        self.assertEqual(items[0].excerpt, "World")

    def test_substitute_body_preserves_json_safety(self):
        template = {"text": {"content": "${body}"}, "list": ["prefix ${body}"]}
        body = 'line one\n"quoted"'

        payload = substitute_body(template, body)

        self.assertEqual(payload["text"]["content"], body)
        self.assertEqual(payload["list"][0], f"prefix {body}")

    def test_collect_items_fetches_concurrently_but_preserves_source_order(self):
        config = {
            "max_total_items": 10,
            "sources": [
                {"name": "Slow", "url": "https://example.com/slow", "type": "rss"},
                {"name": "Fast", "url": "https://example.com/fast", "type": "rss"},
            ],
        }

        def fake_fetch(source):
            if source.name == "Slow":
                time.sleep(0.02)
            return [Item(title=source.name, url=source.url, source=source.name)], None

        with patch("daily_pulse.fetch_source", side_effect=fake_fetch):
            items, errors = collect_items(config)

        self.assertEqual(errors, [])
        self.assertEqual([item.source for item in items], ["Slow", "Fast"])

    def test_build_digest_explains_ai_request_failures(self):
        config = {
            "title": "Test Pulse",
            "summary_words": 120,
            "ai": {
                "endpoint": "https://api.example.com/chat/completions",
                "model": "example-model",
                "api_key_env": "TEST_AI_KEY",
            },
        }
        items = [Item(title="Hello", url="https://example.com/hello", source="Example", excerpt="World")]

        with patch.dict(os.environ, {"TEST_AI_KEY": "secret"}):
            with patch("daily_pulse.urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
                digest = build_digest(config, items, [])

        self.assertIn("AI 摘要未使用", digest)
        self.assertIn("AI 请求失败", digest)
        self.assertIn("Hello", digest)

    def test_update_version_comparison(self):
        self.assertEqual(parse_version("v0.1.10"), (0, 1, 10))
        self.assertTrue(is_newer_version("v0.1.10", "0.1.3"))
        self.assertTrue(is_newer_version("v0.2.0", "0.1.99"))
        self.assertFalse(is_newer_version("v0.1.3", "0.1.3"))
        self.assertFalse(is_newer_version("v0.1.2", "0.1.3"))


if __name__ == "__main__":
    unittest.main()
