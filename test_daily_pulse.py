import unittest

from daily_pulse import Source, parse_rss, substitute_body


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


if __name__ == "__main__":
    unittest.main()
