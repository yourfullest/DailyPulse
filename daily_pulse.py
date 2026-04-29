#!/usr/bin/env python3
"""
DailyPulse: personal briefing generator.

Fetches RSS feeds and public web pages, summarizes the collected items with an
AI-compatible endpoint or a deterministic fallback, then sends the digest by
email, Telegram, or webhook.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.message
import html
import json
import os
import re
import smtplib
import ssl
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable


USER_AGENT = "DailyPulse/1.0 (+https://localhost)"
DEFAULT_TIMEOUT = 20


@dataclass
class Source:
    name: str
    url: str
    type: str = "rss"
    limit: int = 5


@dataclass
class Item:
    title: str
    url: str
    source: str
    published: str = ""
    excerpt: str = ""


class TextExtractor(HTMLParser):
    """Small HTML text extractor for public pages."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "li", "h1", "h2", "h3", "article", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title += cleaned
        self.parts.append(cleaned)

    def text(self) -> str:
        body = html.unescape(" ".join(self.parts))
        body = re.sub(r"\s+", " ", body).strip()
        return body


def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def request_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def strip_html(raw: str) -> str:
    parser = TextExtractor()
    parser.feed(raw)
    return parser.text()


def parse_sources(config: dict[str, Any]) -> list[Source]:
    sources = []
    for row in config.get("sources", []):
        sources.append(
            Source(
                name=row["name"],
                url=row["url"],
                type=row.get("type", "rss").lower(),
                limit=int(row.get("limit", config.get("max_items_per_source", 5))),
            )
        )
    return sources


def get_child_text(parent: ET.Element, names: Iterable[str]) -> str:
    for child in parent:
        tag = child.tag.split("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def get_link(entry: ET.Element) -> str:
    direct = get_child_text(entry, {"link"})
    if direct:
        return direct
    for child in entry:
        tag = child.tag.split("}", 1)[-1].lower()
        if tag == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def parse_rss(source: Source, raw: str) -> list[Item]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"RSS 解析失败: {exc}") from exc

    entries: list[ET.Element] = []
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1].lower()
        if tag in {"item", "entry"}:
            entries.append(elem)

    items: list[Item] = []
    for entry in entries[: source.limit]:
        title = get_child_text(entry, {"title"}) or "(无标题)"
        link = get_link(entry) or source.url
        published = get_child_text(entry, {"pubdate", "published", "updated", "date"})
        description = get_child_text(entry, {"description", "summary", "content", "encoded"})
        excerpt = strip_html(description)[:800]
        items.append(
            Item(
                title=html.unescape(title),
                url=link,
                source=source.name,
                published=published,
                excerpt=excerpt,
            )
        )
    return items


def parse_web_page(source: Source, raw: str) -> list[Item]:
    parser = TextExtractor()
    parser.feed(raw)
    title = html.unescape(parser.title.strip()) or source.name
    text = parser.text()
    return [
        Item(
            title=title,
            url=source.url,
            source=source.name,
            excerpt=text[:1600],
        )
    ]


def fetch_source(source: Source) -> tuple[list[Item], str | None]:
    try:
        raw = request_text(source.url)
        if source.type == "rss":
            return parse_rss(source, raw), None
        if source.type == "web":
            return parse_web_page(source, raw), None
        return [], f"未知 source.type: {source.type}"
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [], f"{source.name}: {exc}"


def truncate(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def brief_context(items: list[Item], max_chars: int) -> str:
    blocks = []
    for idx, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"[{idx}] {item.title}",
                    f"来源: {item.source}",
                    f"时间: {item.published or '未知'}",
                    f"链接: {item.url}",
                    f"摘录: {truncate(item.excerpt, 900)}",
                ]
            )
        )
    return truncate("\n\n".join(blocks), max_chars)


def call_ai_summary(config: dict[str, Any], items: list[Item]) -> str | None:
    ai_config = config.get("ai", {})
    api_key_env = ai_config.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.getenv(api_key_env)
    if not api_key:
        return None

    endpoint = ai_config.get("endpoint", "https://api.openai.com/v1/chat/completions")
    model = ai_config.get("model", "gpt-4o-mini")
    max_context_chars = int(ai_config.get("max_context_chars", 12000))
    target_words = int(config.get("summary_words", 450))
    prompt = ai_config.get(
        "prompt",
        "你是我的个人信息秘书。请用中文生成一段信息简报，"
        "长度控制在几百字，聚焦事实、趋势、风险和我可能需要跟进的事项。"
        "不要编造来源中没有的信息。",
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"请基于以下来源生成约 {target_words} 字中文简报，并在文末保留"
                    "一个“消息源”小节，列出编号、标题、来源和链接。\n\n"
                    + brief_context(items, max_context_chars)
                ),
            },
        ],
        "temperature": float(ai_config.get("temperature", 0.2)),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(ai_config.get("timeout", 60))) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[warn] AI 摘要失败，改用本地摘要: {exc}", file=sys.stderr)
        return None

    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        print("[warn] AI 返回格式无法识别，改用本地摘要", file=sys.stderr)
        return None


def fallback_summary(config: dict[str, Any], items: list[Item]) -> str:
    target_words = int(config.get("summary_words", 450))
    max_items = int(config.get("fallback_item_count", 8))
    intro = f"今日共抓取 {len(items)} 条信息。未配置 AI 密钥，以下为本地提取式简报："
    lines = [intro]
    for item in items[:max_items]:
        excerpt = truncate(item.excerpt, 180)
        lines.append(f"- {item.source}：《{item.title}》。{excerpt}")

    body = "\n".join(lines)
    if len(body) > target_words * 2:
        body = truncate(body, target_words * 2)
    return body


def render_sources(items: list[Item]) -> str:
    rows = ["", "消息源:"]
    for idx, item in enumerate(items, 1):
        published = f" ({item.published})" if item.published else ""
        rows.append(f"{idx}. {item.title} - {item.source}{published}\n   {item.url}")
    return "\n".join(rows)


def build_digest(config: dict[str, Any], items: list[Item], errors: list[str]) -> str:
    title = config.get("title", "DailyPulse 个人信息简报")
    today = dt.datetime.now().strftime("%Y-%m-%d")
    summary = call_ai_summary(config, items) or fallback_summary(config, items)
    parts = [f"{title} | {today}", "", summary]
    if "消息源" not in summary:
        parts.append(render_sources(items))
    if errors and config.get("include_fetch_errors", True):
        parts.append("\n抓取提醒:")
        parts.extend(f"- {err}" for err in errors)
    return "\n".join(parts).strip() + "\n"


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    email_config = config.get("delivery", {}).get("email", {})
    if not email_config.get("enabled"):
        return

    host = email_config["smtp_host"]
    port = int(email_config.get("smtp_port", 465))
    username = os.getenv(email_config.get("username_env", "SMTP_USERNAME"), "")
    password = os.getenv(email_config.get("password_env", "SMTP_PASSWORD"), "")
    sender = email_config.get("from") or username
    recipients = email_config.get("to", [])
    if isinstance(recipients, str):
        recipients = [recipients]
    if not username or not password or not sender or not recipients:
        raise ValueError("邮件发送缺少 SMTP 用户名、密码、发件人或收件人")

    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(msg)


def post_json(url: str, payload: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def substitute_body(value: Any, body: str) -> Any:
    if isinstance(value, str):
        return value.replace("${body}", body)
    if isinstance(value, list):
        return [substitute_body(item, body) for item in value]
    if isinstance(value, dict):
        return {key: substitute_body(item, body) for key, item in value.items()}
    return value


def send_telegram(config: dict[str, Any], body: str) -> None:
    telegram = config.get("delivery", {}).get("telegram", {})
    if not telegram.get("enabled"):
        return
    token = os.getenv(telegram.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat_id = os.getenv(telegram.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if not token or not chat_id:
        raise ValueError("Telegram 发送缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram message length limit is 4096; split conservatively.
    for chunk in textwrap.wrap(body, width=3800, replace_whitespace=False, drop_whitespace=False):
        post_json(endpoint, {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True})


def send_webhook(config: dict[str, Any], body: str) -> None:
    webhook = config.get("delivery", {}).get("webhook", {})
    if not webhook.get("enabled"):
        return
    url = os.getenv(webhook.get("url_env", "WEBHOOK_URL"), webhook.get("url", ""))
    if not url:
        raise ValueError("Webhook 发送缺少 URL")
    payload_template = webhook.get("payload")
    if payload_template:
        payload = substitute_body(payload_template, body)
    else:
        payload = {"text": body}
    post_json(url, payload)


def send_digest(config: dict[str, Any], body: str) -> None:
    subject = f"{config.get('title', 'DailyPulse 个人信息简报')} | {dt.datetime.now():%Y-%m-%d}"
    send_email(config, subject, body)
    send_telegram(config, body)
    send_webhook(config, body)


def collect_items(config: dict[str, Any]) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    for source in parse_sources(config):
        source_items, error = fetch_source(source)
        items.extend(source_items)
        if error:
            errors.append(error)
    max_total = int(config.get("max_total_items", 30))
    return items[:max_total], errors


def run_once(config_path: str, dry_run: bool = False, output_path: str | None = None) -> int:
    load_env()
    config = load_config(config_path)
    items, errors = collect_items(config)
    if not items:
        print("没有抓取到可用内容。", file=sys.stderr)
        for err in errors:
            print(f"[warn] {err}", file=sys.stderr)
        return 2

    digest = build_digest(config, items, errors)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(digest)
    if dry_run:
        print(digest)
    else:
        send_digest(config, digest)
        print("简报已生成并发送。")
    return 0


def seconds_until(hour: int, minute: int) -> float:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def run_scheduler(config_path: str) -> None:
    load_env()
    config = load_config(config_path)
    schedule = config.get("schedule", {})
    run_at = schedule.get("time", "08:00")
    hour, minute = [int(part) for part in run_at.split(":", 1)]
    print(f"DailyPulse 定时任务已启动，每天 {hour:02d}:{minute:02d} 运行。")
    while True:
        wait_seconds = seconds_until(hour, minute)
        print(f"下一次运行还有 {int(wait_seconds)} 秒。")
        time.sleep(wait_seconds)
        try:
            run_once(config_path)
        except Exception as exc:  # Keep the long-running scheduler alive.
            print(f"[error] 本次运行失败: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="个人信息简报生成器")
    parser.add_argument("-c", "--config", default="config.example.json", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="立即运行一次")
    parser.add_argument("--schedule", action="store_true", help="按配置中的时间每天运行")
    parser.add_argument("--dry-run", action="store_true", help="打印简报，不发送")
    parser.add_argument("--output", help="把生成的简报写入指定文件")
    args = parser.parse_args(argv)

    if args.schedule:
        run_scheduler(args.config)
        return 0
    return run_once(args.config, dry_run=args.dry_run or not args.once, output_path=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
