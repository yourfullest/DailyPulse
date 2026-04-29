# DailyPulse

English | [中文](README.zh-CN.md)

DailyPulse is a personal AI briefing generator. It fetches RSS feeds, public web pages, blogs, WeChat article links, and RSSHub feeds for platforms such as Xiaohongshu or X, summarizes them with DeepSeek or any OpenAI-compatible API, and delivers the digest by email, Telegram, or webhook.

## Features

- Desktop GUI for managing sources, API keys, and delivery channels.
- Supports `rss` and `web` sources.
- Supports DeepSeek and OpenAI-compatible Chat Completions APIs.
- Sends digests by email, Telegram Bot, or generic webhook.
- Can run from source or be packaged as a macOS `.dmg` and Windows `.exe`.

## Download

Download from GitHub Releases:

- macOS: `DailyPulse-macOS.dmg`
- Windows: `DailyPulse-Windows.zip`

First run:

1. Enter your DeepSeek API key.
2. Add RSS, web page, or RSSHub sources.
3. Click Preview Digest.
4. Configure email, Telegram, or webhook delivery if needed.
5. Click Send Once.

On macOS, if the app is blocked because it is from an unidentified developer, right-click `DailyPulse.app` and choose Open. On Windows, SmartScreen may show a warning until the app is code-signed.

Packaged builds store user configuration in:

- macOS: `~/Library/Application Support/DailyPulse/`
- Windows: `%APPDATA%\DailyPulse\`

## Run From Source

Python 3.10 or newer is required.

```bash
python3 daily_pulse_app.py
```

On macOS, you can also double-click:

```text
run_app.command
```

On Windows, you can also double-click:

```text
run_app.bat
```

CLI dry run:

```bash
cp config.deepseek.example.json config.json
cp .env.example .env
python3 daily_pulse.py --once --dry-run -c config.json
```

## DeepSeek Setup

Add your key to `.env`:

```bash
DEEPSEEK_API_KEY=your_deepseek_api_key
```

Important `config.json` fields:

```json
"ai": {
  "endpoint": "https://api.deepseek.com/chat/completions",
  "model": "deepseek-v4-flash",
  "api_key_env": "DEEPSEEK_API_KEY"
}
```

## Sources

RSS example:

```json
{
  "name": "BBC News Chinese",
  "type": "rss",
  "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
  "limit": 3
}
```

Public web page example:

```json
{
  "name": "A WeChat article",
  "type": "web",
  "url": "https://mp.weixin.qq.com/s/xxxx",
  "limit": 1
}
```

Xiaohongshu, X, and WeChat account pages often require login, dynamic rendering, or anti-crawling handling. For those sources, RSSHub, Follow, Feed43, or an official export/API workflow is usually more reliable.

## Delivery

Email: enable `delivery.email` and set `SMTP_USERNAME` and `SMTP_PASSWORD` in `.env`.

Telegram: enable `delivery.telegram` and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

Webhook: works with services such as WeCom, Feishu, DingTalk, ServerChan, PushPlus, and WxPusher. Set `WEBHOOK_URL` in `.env`.

## Scheduling

The desktop app includes an in-app daily scheduler, but the app must remain open.

Long-running CLI scheduler:

```bash
python3 daily_pulse.py --schedule -c config.json
```

You can also use cron, launchd, systemd, or GitHub Actions.

## Build Desktop Apps

macOS:

```bash
bash scripts/build_macos.sh
```

Output:

```text
dist/DailyPulse-macOS.dmg
```

Windows:

```powershell
.\scripts\build_windows.ps1
```

Output:

```text
dist\DailyPulse-Windows.zip
```

Pushing a tag triggers GitHub Actions to build both macOS and Windows releases:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Security

Do not commit `.env` or `config.json`. They contain local API keys, webhook URLs, and personal sources, and are already excluded by `.gitignore`.

