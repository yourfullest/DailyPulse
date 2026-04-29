# DailyPulse

Personal AI briefing generator. Fetch RSS feeds, public pages, blogs, WeChat article links, Xiaohongshu/X via RSSHub, summarize them with an OpenAI-compatible API such as DeepSeek, and send the digest by email, Telegram, or webhook.

个人 AI 信息简报生成器。抓取 RSS、公开网页、博客、微信公众号文章链接、小红书/X 的 RSSHub 源，用 DeepSeek 等 OpenAI 兼容 API 生成摘要，并通过邮件、Telegram 或 Webhook 推送。

## Language

- [English](README.en.md)
- [中文](README.zh-CN.md)

## Quick Download

Download from GitHub Releases:

- macOS: `DailyPulse-macOS.dmg`
- Windows: `DailyPulse-Windows.zip`

从 GitHub Releases 下载：

- macOS：`DailyPulse-macOS.dmg`
- Windows：`DailyPulse-Windows.zip`

## Developer Quick Start

```bash
python3 daily_pulse_app.py
```

Command line:

```bash
cp config.deepseek.example.json config.json
cp .env.example .env
python3 daily_pulse.py --once --dry-run -c config.json
```

