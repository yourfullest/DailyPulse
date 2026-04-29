# DailyPulse

[English](README.en.md) | 中文

DailyPulse 是一个个人 AI 信息简报生成器。它可以抓取 RSS、公开网页、博客、微信公众号文章链接、小红书/X 的 RSSHub 源，用 DeepSeek 或其他 OpenAI 兼容 API 生成中文摘要，并通过邮件、Telegram 或 Webhook 推送。

## 功能

- 图形界面管理信息源、API Key 和发送渠道。
- 支持 `rss` 和 `web` 两类来源。
- 支持 DeepSeek / OpenAI 兼容 Chat Completions API。
- 支持邮件、Telegram Bot、企业微信/飞书/钉钉等 Webhook。
- 支持源码运行，也支持打包为 macOS `.dmg` 和 Windows `.exe`。

## 下载版使用

从 GitHub Releases 下载：

- macOS：`DailyPulse-macOS.dmg`
- Windows：`DailyPulse-Windows.zip`

首次打开后：

1. 填写 DeepSeek API Key。
2. 添加 RSS、网页或 RSSHub 信息源。
3. 点击“预览简报”查看效果。
4. 需要推送时，配置邮件、Telegram 或 Webhook。
5. 点击“发送一次”。

macOS 如果提示来自未认证开发者，可以右键 `DailyPulse.app` 选择“打开”。Windows 如果出现 SmartScreen 提示，可以选择“更多信息”后继续运行。正式分发时建议后续添加代码签名。

打包版会把用户配置保存在：

- macOS：`~/Library/Application Support/DailyPulse/`
- Windows：`%APPDATA%\DailyPulse\`

## 源码运行

需要 Python 3.10 或更高版本。

```bash
python3 daily_pulse_app.py
```

macOS 源码版也可以双击：

```text
run_app.command
```

Windows 源码版也可以双击：

```text
run_app.bat
```

命令行试跑：

```bash
cp config.deepseek.example.json config.json
cp .env.example .env
python3 daily_pulse.py --once --dry-run -c config.json
```

## DeepSeek 配置

在 `.env` 中填写：

```bash
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

`config.json` 中关键配置：

```json
"ai": {
  "endpoint": "https://api.deepseek.com/chat/completions",
  "model": "deepseek-v4-flash",
  "api_key_env": "DEEPSEEK_API_KEY"
}
```

## 配置来源

RSS 示例：

```json
{
  "name": "BBC News 中文",
  "type": "rss",
  "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
  "limit": 3
}
```

公开网页示例：

```json
{
  "name": "某篇公众号文章",
  "type": "web",
  "url": "https://mp.weixin.qq.com/s/xxxx",
  "limit": 1
}
```

小红书、X、公众号账号主页通常有登录、动态渲染和风控限制。建议使用 RSSHub、Follow、Feed43 等工具把可合法访问的公开内容转成 RSS，再填入 DailyPulse。

## 发送渠道

邮件：在 `delivery.email` 中启用 SMTP，并在 `.env` 中填写 `SMTP_USERNAME` 和 `SMTP_PASSWORD`。

Telegram：在 `delivery.telegram` 中启用，并在 `.env` 中填写 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`。

Webhook：适合企业微信、飞书、钉钉、Server 酱、PushPlus、WxPusher 等服务。在 `.env` 中填写 `WEBHOOK_URL`。

## 定时运行

图形界面中可以启动“App 内定时”，但需要 App 一直打开。

命令行长驻运行：

```bash
python3 daily_pulse.py --schedule -c config.json
```

也可以配合 cron、launchd、systemd 或 GitHub Actions。

## 构建下载版

macOS：

```bash
bash scripts/build_macos.sh
```

输出：

```text
dist/DailyPulse-macOS.dmg
```

Windows：

```powershell
.\scripts\build_windows.ps1
```

输出：

```text
dist\DailyPulse-Windows.zip
```

推送 tag 后，GitHub Actions 会自动构建 macOS 和 Windows 版本：

```bash
git tag v0.1.0
git push origin v0.1.0
```

## 安全提醒

不要提交 `.env` 和 `config.json`。它们包含用户自己的 API Key、Webhook 地址和个人订阅源，已经被 `.gitignore` 排除。

