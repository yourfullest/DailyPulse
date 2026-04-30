import os
import time
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

from daily_pulse_app import is_newer_version, parse_version
from daily_pulse import (
    Item,
    Source,
    build_digest,
    collect_items,
    collect_source_results,
    fetch_source,
    parse_rss,
    substitute_body,
    summary_style_instruction,
    describe_http_error,
    fallback_summary,
)


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

    def test_collect_source_results_reports_empty_and_error_states(self):
        config = {
            "sources": [
                {"name": "Empty", "url": "https://example.com/empty", "type": "rss"},
                {"name": "Broken", "url": "https://example.com/broken", "type": "rss"},
            ],
        }

        def fake_fetch(source):
            if source.name == "Broken":
                return [], "Broken: timeout"
            return [], None

        with patch("daily_pulse.fetch_source", side_effect=fake_fetch):
            results = collect_source_results(config)

        self.assertEqual([result.source.name for result in results], ["Empty", "Broken"])
        self.assertEqual(results[0].items, [])
        self.assertIsNone(results[0].error)
        self.assertEqual(results[1].error, "Broken: timeout")

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

    def test_summary_style_instruction(self):
        self.assertIn("简洁", summary_style_instruction({"summary_style": "brief"}))
        self.assertIn("可执行事项", summary_style_instruction({"summary_style": "action"}))

    # -- New tests --

    def test_parse_version_edge_cases(self):
        self.assertEqual(parse_version("no-version-here"), (0,))
        self.assertEqual(parse_version("1.2.3.4"), (1, 2, 3, 4))
        self.assertEqual(parse_version("v10"), (10,))
        self.assertEqual(parse_version(""), (0,))

    def test_is_newer_version_different_lengths(self):
        self.assertTrue(is_newer_version("v1.0.1", "1.0"))
        self.assertFalse(is_newer_version("v1.0", "1.0.1"))

    def test_fetch_source_handles_timeout(self):
        source = Source(name="Slow", url="https://example.com/slow")
        with patch("daily_pulse.request_text", side_effect=TimeoutError("timed out")):
            items, error = fetch_source(source)
        self.assertEqual(items, [])
        self.assertIn("Slow", error)
        self.assertIn("timed out", error)

    def test_fetch_source_handles_value_error(self):
        source = Source(name="BadRSS", url="https://example.com/bad")
        with patch("daily_pulse.request_text", return_value="<not xml"):
            items, error = fetch_source(source)
        self.assertEqual(items, [])
        self.assertIn("BadRSS", error)

    def test_fetch_source_unknown_type(self):
        source = Source(name="Unknown", url="https://example.com/u", type="json")
        with patch("daily_pulse.request_text", return_value="{}"):
            items, error = fetch_source(source)
        self.assertEqual(items, [])
        self.assertIn("未知", error)

    def test_describe_http_error(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"error": "bad request"}'
        exc = urllib.error.HTTPError("url", 400, "Bad Request", {}, mock_resp)
        result = describe_http_error(exc)
        self.assertIn("HTTP 400", result)
        self.assertIn("bad request", result)

    def test_fallback_summary_with_reason(self):
        items = [
            Item(title="Test", url="https://example.com/t", source="Src", excerpt="text"),
        ]
        result = fallback_summary({}, items, reason="API key missing")
        self.assertIn("API key missing", result)
        self.assertIn("Test", result)

    def test_fallback_summary_truncates(self):
        items = [
            Item(title=f"Item {i}", url=f"https://example.com/{i}", source="Src", excerpt="x" * 200)
            for i in range(20)
        ]
        result = fallback_summary({"summary_words": 100}, items)
        self.assertLessEqual(len(result), 220)

    def test_parse_rss_handles_empty_feed(self):
        raw = """<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"""
        items = parse_rss(Source(name="Empty", url="https://example.com/e"), raw)
        self.assertEqual(items, [])

    def test_parse_rss_invalid_xml(self):
        with self.assertRaises(ValueError):
            parse_rss(Source(name="Bad", url="https://example.com/b"), "not xml at all")


if __name__ == "__main__":
    unittest.main()
