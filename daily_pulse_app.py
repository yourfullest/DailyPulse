#!/usr/bin/env python3
"""
DailyPulse desktop app.

A small Tkinter GUI for editing sources, configuring DeepSeek/OpenAI-compatible
AI settings, previewing digests, and sending them without touching JSON by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, Callable

import daily_pulse


APP_NAME = "DailyPulse"
APP_VERSION = "0.1.6"
UPDATE_API_URL = "https://api.github.com/repos/yourfullest/DailyPulse/releases/latest"
UPDATE_PAGE_URL = "https://github.com/yourfullest/DailyPulse/releases/latest"
IS_BUNDLED = bool(getattr(sys, "frozen", False))
SOURCE_ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


APP_DATA_DIR = user_data_dir() if IS_BUNDLED else SOURCE_ROOT
CONFIG_PATH = APP_DATA_DIR / "config.json"
ENV_PATH = APP_DATA_DIR / ".env"
HISTORY_DIR = APP_DATA_DIR / "history"
ASSET_ROOT = RESOURCE_ROOT / "assets"
APP_ICON_PNG = ASSET_ROOT / "DailyPulse.png"

BG = "#f4f7f4"
SURFACE = "#ffffff"
SURFACE_ALT = "#f7faf7"
TEXT = "#1d2733"
MUTED = "#66746f"
BORDER = "#d6e0dc"
ACCENT = "#0f8f8c"
ACCENT_DARK = "#0a6666"
TEAL = "#138a7e"
CORAL = "#f16f51"
MINT_SOFT = "#dff6ef"
SKY_SOFT = "#e3f1ff"
CORAL_SOFT = "#ffe7df"
STATUS_BG = "#edf4ef"
OUTPUT_BG = "#fbfcf8"


def parse_version(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+)*)", value)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    candidate_parts = list(parse_version(candidate))
    current_parts = list(parse_version(current))
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts.extend([0] * (width - len(candidate_parts)))
    current_parts.extend([0] * (width - len(current_parts)))
    return candidate_parts > current_parts


def fetch_latest_release(timeout: int = 8) -> dict[str, str] | None:
    req = urllib.request.Request(
        UPDATE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=daily_pulse.ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag_name = str(data.get("tag_name", "")).strip()
    if not tag_name or data.get("draft") or data.get("prerelease"):
        return None
    return {
        "tag_name": tag_name,
        "name": str(data.get("name") or tag_name),
        "html_url": str(data.get("html_url") or UPDATE_PAGE_URL),
    }


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            next_lines.append(raw)
            continue
        key, _ = raw.split("=", 1)
        key = key.strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            next_lines.append(raw)

    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")

    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return daily_pulse.load_config(str(CONFIG_PATH))
    example = RESOURCE_ROOT / "config.deepseek.example.json"
    if example.exists():
        return daily_pulse.load_config(str(example))
    return {
        "title": "DailyPulse 个人信息简报",
        "summary_words": 450,
        "summary_style": "standard",
        "max_total_items": 30,
        "schedule": {"time": "08:00"},
        "ai": {
            "endpoint": "https://api.deepseek.com/chat/completions",
            "model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.2,
            "max_context_chars": 12000,
        },
        "sources": [],
        "delivery": {"email": {"enabled": False}, "telegram": {"enabled": False}, "webhook": {"enabled": False}},
    }


class SourceDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, title: str, source: dict[str, Any] | None = None):
        self.source = source or {"name": "", "type": "rss", "url": "", "limit": 5}
        self.result: dict[str, Any] | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget:
        self.name_var = tk.StringVar(value=str(self.source.get("name", "")))
        self.type_var = tk.StringVar(value=str(self.source.get("type", "rss")))
        self.url_var = tk.StringVar(value=str(self.source.get("url", "")))
        self.limit_var = tk.StringVar(value=str(self.source.get("limit", 5)))

        fields = [
            ("名称", ttk.Entry(master, textvariable=self.name_var, width=42)),
            ("类型", ttk.Combobox(master, textvariable=self.type_var, values=("rss", "web"), state="readonly", width=39)),
            ("URL", ttk.Entry(master, textvariable=self.url_var, width=42)),
            ("条数", ttk.Entry(master, textvariable=self.limit_var, width=42)),
        ]
        for row, (label, widget) in enumerate(fields):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
        master.columnconfigure(1, weight=1)
        return fields[0][1]

    def validate(self) -> bool:
        if not self.name_var.get().strip():
            messagebox.showerror("缺少名称", "请填写信息源名称。", parent=self)
            return False
        if not self.url_var.get().strip().startswith(("http://", "https://")):
            messagebox.showerror("URL 不正确", "URL 需要以 http:// 或 https:// 开头。", parent=self)
            return False
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            messagebox.showerror("条数不正确", "条数需要是整数。", parent=self)
            return False
        if limit < 1:
            messagebox.showerror("条数不正确", "条数至少为 1。", parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = {
            "name": self.name_var.get().strip(),
            "type": self.type_var.get().strip(),
            "url": self.url_var.get().strip(),
            "limit": int(self.limit_var.get()),
        }


class DeliveryDialog(simpledialog.Dialog):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        delivery: dict[str, Any],
        env_data: dict[str, str],
    ):
        self.delivery = delivery
        self.env_data = env_data
        self.result: dict[str, Any] | None = None
        self.env_updates: dict[str, str] = {}
        super().__init__(parent, title)

    def env_value(self, name: str) -> str:
        return self.env_data.get(name, os.getenv(name, ""))

    def body(self, master: tk.Frame) -> tk.Widget:
        master.columnconfigure(0, weight=1)
        notebook = ttk.Notebook(master)
        notebook.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self._build_email_tab(notebook)
        self._build_telegram_tab(notebook)
        self._build_webhook_tab(notebook)
        self.test_status_var = tk.StringVar(value="测试发送会使用当前弹窗里的字段。")
        ttk.Label(master, textvariable=self.test_status_var, style="MutedSurface.TLabel").grid(
            row=1, column=0, sticky="w", padx=4, pady=(8, 0)
        )
        return self.email_host_entry

    def add_entry(
        self,
        parent: tk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=variable, show=show, width=44)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        return entry

    def _build_email_tab(self, notebook: ttk.Notebook) -> None:
        email = self.delivery.get("email", {})
        frame = ttk.Frame(notebook, padding=12)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text="邮件")

        username_env = str(email.get("username_env", "SMTP_USERNAME"))
        password_env = str(email.get("password_env", "SMTP_PASSWORD"))
        recipients = email.get("to", [])
        if isinstance(recipients, str):
            recipients_text = recipients
        else:
            recipients_text = "\n".join(str(item) for item in recipients)

        self.email_enabled_var = tk.BooleanVar(value=bool(email.get("enabled", False)))
        self.email_host_var = tk.StringVar(value=str(email.get("smtp_host", "smtp.gmail.com")))
        self.email_port_var = tk.StringVar(value=str(email.get("smtp_port", 465)))
        self.email_username_env_var = tk.StringVar(value=username_env)
        self.email_username_var = tk.StringVar(value=self.env_value(username_env))
        self.email_password_env_var = tk.StringVar(value=password_env)
        self.email_password_var = tk.StringVar(value=self.env_value(password_env))
        self.email_from_var = tk.StringVar(value=str(email.get("from", "")))

        ttk.Checkbutton(frame, text="启用邮件发送", variable=self.email_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self.email_host_entry = self.add_entry(frame, 1, "SMTP Host", self.email_host_var)
        self.add_entry(frame, 2, "SMTP Port", self.email_port_var)
        self.add_entry(frame, 3, "用户名变量", self.email_username_env_var)
        self.add_entry(frame, 4, "SMTP 用户名", self.email_username_var)
        self.add_entry(frame, 5, "密码变量", self.email_password_env_var)
        self.add_entry(frame, 6, "SMTP 密码", self.email_password_var, show="*")
        self.add_entry(frame, 7, "发件人", self.email_from_var)
        ttk.Label(frame, text="收件人").grid(row=8, column=0, sticky="nw", padx=(0, 10), pady=5)
        self.email_to_text = tk.Text(frame, width=44, height=4, wrap="word")
        self.email_to_text.grid(row=8, column=1, sticky="ew", pady=5)
        self.email_to_text.insert("1.0", recipients_text)
        ttk.Button(frame, text="测试邮件", command=self.test_email, style="Secondary.TButton").grid(
            row=9, column=1, sticky="w", pady=(10, 0)
        )

    def _build_telegram_tab(self, notebook: ttk.Notebook) -> None:
        telegram = self.delivery.get("telegram", {})
        frame = ttk.Frame(notebook, padding=12)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text="Telegram")

        token_env = str(telegram.get("token_env", "TELEGRAM_BOT_TOKEN"))
        chat_id_env = str(telegram.get("chat_id_env", "TELEGRAM_CHAT_ID"))
        self.telegram_enabled_var = tk.BooleanVar(value=bool(telegram.get("enabled", False)))
        self.telegram_token_env_var = tk.StringVar(value=token_env)
        self.telegram_token_var = tk.StringVar(value=self.env_value(token_env))
        self.telegram_chat_id_env_var = tk.StringVar(value=chat_id_env)
        self.telegram_chat_id_var = tk.StringVar(value=self.env_value(chat_id_env))

        ttk.Checkbutton(frame, text="启用 Telegram 发送", variable=self.telegram_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self.add_entry(frame, 1, "Bot Token 变量", self.telegram_token_env_var)
        self.add_entry(frame, 2, "Bot Token", self.telegram_token_var, show="*")
        self.add_entry(frame, 3, "Chat ID 变量", self.telegram_chat_id_env_var)
        self.add_entry(frame, 4, "Chat ID", self.telegram_chat_id_var)
        ttk.Button(frame, text="测试 Telegram", command=self.test_telegram, style="Secondary.TButton").grid(
            row=5, column=1, sticky="w", pady=(10, 0)
        )

    def _build_webhook_tab(self, notebook: ttk.Notebook) -> None:
        webhook = self.delivery.get("webhook", {})
        frame = ttk.Frame(notebook, padding=12)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text="Webhook")

        url_env = str(webhook.get("url_env", "WEBHOOK_URL"))
        self.webhook_enabled_var = tk.BooleanVar(value=bool(webhook.get("enabled", False)))
        self.webhook_url_env_var = tk.StringVar(value=url_env)
        self.webhook_url_var = tk.StringVar(value=self.env_value(url_env))
        self.webhook_direct_url_var = tk.StringVar(value=str(webhook.get("url", "")))

        ttk.Checkbutton(frame, text="启用 Webhook 发送", variable=self.webhook_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self.add_entry(frame, 1, "URL 变量", self.webhook_url_env_var)
        self.add_entry(frame, 2, "Webhook URL", self.webhook_url_var, show="*")
        self.add_entry(frame, 3, "备用明文 URL", self.webhook_direct_url_var)
        ttk.Button(frame, text="测试 Webhook", command=self.test_webhook, style="Secondary.TButton").grid(
            row=4, column=1, sticky="w", pady=(10, 0)
        )

    def validate_url(self, value: str, label: str) -> bool:
        if value and not value.startswith(("http://", "https://")):
            messagebox.showerror("URL 不正确", f"{label} 需要以 http:// 或 https:// 开头。", parent=self)
            return False
        return True

    def validate(self) -> bool:
        if not self.validate_email_fields(require_enabled=bool(self.email_enabled_var.get())):
            return False
        if not self.validate_telegram_fields(require_enabled=bool(self.telegram_enabled_var.get())):
            return False
        if not self.validate_webhook_fields(require_enabled=bool(self.webhook_enabled_var.get())):
            return False
        return True

    def validate_email_fields(self, *, require_enabled: bool) -> bool:
        try:
            port = int(self.email_port_var.get().strip())
        except ValueError:
            messagebox.showerror("SMTP Port 不正确", "SMTP Port 需要是整数。", parent=self)
            return False
        if port < 1 or port > 65535:
            messagebox.showerror("SMTP Port 不正确", "SMTP Port 需要在 1 到 65535 之间。", parent=self)
            return False

        if require_enabled:
            recipients = self.email_recipients()
            if not self.email_host_var.get().strip() or not recipients:
                messagebox.showerror("邮件配置不完整", "启用邮件时需要填写 SMTP Host 和收件人。", parent=self)
                return False
            if not self.email_username_var.get().strip() or not self.email_password_var.get().strip():
                messagebox.showerror("邮件配置不完整", "启用邮件时需要填写 SMTP 用户名和密码。", parent=self)
                return False
        return True

    def validate_telegram_fields(self, *, require_enabled: bool) -> bool:
        if require_enabled:
            if not self.telegram_token_var.get().strip() or not self.telegram_chat_id_var.get().strip():
                messagebox.showerror("Telegram 配置不完整", "启用 Telegram 时需要填写 Bot Token 和 Chat ID。", parent=self)
                return False
        return True

    def validate_webhook_fields(self, *, require_enabled: bool) -> bool:
        webhook_url = self.webhook_url_var.get().strip()
        direct_url = self.webhook_direct_url_var.get().strip()
        if not self.validate_url(webhook_url, "Webhook URL"):
            return False
        if not self.validate_url(direct_url, "备用明文 URL"):
            return False
        if require_enabled and not webhook_url and not direct_url:
            messagebox.showerror("Webhook 配置不完整", "启用 Webhook 时需要填写 Webhook URL。", parent=self)
            return False
        return True

    def email_recipients(self) -> list[str]:
        raw = self.email_to_text.get("1.0", "end").strip()
        rows = raw.replace(",", "\n").splitlines()
        return [row.strip() for row in rows if row.strip()]

    def remember_secret(self, env_name: str, value: str) -> None:
        env_name = env_name.strip()
        value = value.strip()
        if env_name and value:
            self.env_updates[env_name] = value

    def current_delivery_config(self) -> tuple[dict[str, Any], dict[str, str]]:
        email_username_env = self.email_username_env_var.get().strip() or "SMTP_USERNAME"
        email_password_env = self.email_password_env_var.get().strip() or "SMTP_PASSWORD"
        telegram_token_env = self.telegram_token_env_var.get().strip() or "TELEGRAM_BOT_TOKEN"
        telegram_chat_id_env = self.telegram_chat_id_env_var.get().strip() or "TELEGRAM_CHAT_ID"
        webhook_url_env = self.webhook_url_env_var.get().strip() or "WEBHOOK_URL"

        result = dict(self.delivery)
        try:
            smtp_port = int(self.email_port_var.get().strip())
        except ValueError:
            smtp_port = 465
        result["email"] = {
            "enabled": bool(self.email_enabled_var.get()),
            "smtp_host": self.email_host_var.get().strip(),
            "smtp_port": smtp_port,
            "username_env": email_username_env,
            "password_env": email_password_env,
            "from": self.email_from_var.get().strip(),
            "to": self.email_recipients(),
        }
        telegram = dict(self.delivery.get("telegram", {}))
        telegram.update(
            {
                "enabled": bool(self.telegram_enabled_var.get()),
                "token_env": telegram_token_env,
                "chat_id_env": telegram_chat_id_env,
            }
        )
        result["telegram"] = telegram

        webhook = dict(self.delivery.get("webhook", {}))
        webhook.update(
            {
                "enabled": bool(self.webhook_enabled_var.get()),
                "url_env": webhook_url_env,
                "url": self.webhook_direct_url_var.get().strip(),
            }
        )
        result["webhook"] = webhook

        env_updates: dict[str, str] = {}
        for env_name, value in (
            (email_username_env, self.email_username_var.get()),
            (email_password_env, self.email_password_var.get()),
            (telegram_token_env, self.telegram_token_var.get()),
            (telegram_chat_id_env, self.telegram_chat_id_var.get()),
            (webhook_url_env, self.webhook_url_var.get()),
        ):
            env_name = env_name.strip()
            value = value.strip()
            if env_name and value:
                env_updates[env_name] = value
        return result, env_updates

    def apply(self) -> None:
        self.result, self.env_updates = self.current_delivery_config()

    def run_with_env(self, updates: dict[str, str], worker: Callable[[], None]) -> None:
        previous = {key: os.environ.get(key) for key in updates}
        try:
            os.environ.update(updates)
            worker()
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def run_test(self, label: str, worker: Callable[[], None]) -> None:
        self.test_status_var.set(f"正在测试 {label}...")

        def target() -> None:
            try:
                worker()
            except Exception as exc:
                self.after(0, lambda: self.finish_test(label, exc))
            else:
                self.after(0, lambda: self.finish_test(label, None))

        threading.Thread(target=target, daemon=True).start()

    def finish_test(self, label: str, error: Exception | None) -> None:
        if error:
            self.test_status_var.set(f"{label} 测试失败。")
            messagebox.showerror(f"{label} 测试失败", str(error), parent=self)
            return
        self.test_status_var.set(f"{label} 测试已发送。")
        messagebox.showinfo(f"{label} 测试已发送", "测试消息已经提交给发送渠道。", parent=self)

    def test_email(self) -> None:
        if not self.validate_email_fields(require_enabled=True):
            return
        delivery, env_updates = self.current_delivery_config()
        delivery["email"]["enabled"] = True

        def worker() -> None:
            self.run_with_env(
                env_updates,
                lambda: daily_pulse.send_email(
                    {"delivery": delivery},
                    "DailyPulse 测试邮件",
                    f"DailyPulse 测试邮件发送成功。\n\n时间: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
                ),
            )

        self.run_test("邮件", worker)

    def test_telegram(self) -> None:
        if not self.validate_telegram_fields(require_enabled=True):
            return
        delivery, env_updates = self.current_delivery_config()
        delivery["telegram"]["enabled"] = True

        def worker() -> None:
            self.run_with_env(
                env_updates,
                lambda: daily_pulse.send_telegram(
                    {"delivery": delivery},
                    f"DailyPulse Telegram 测试发送成功。\n时间: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
                ),
            )

        self.run_test("Telegram", worker)

    def test_webhook(self) -> None:
        if not self.validate_webhook_fields(require_enabled=True):
            return
        delivery, env_updates = self.current_delivery_config()
        delivery["webhook"]["enabled"] = True

        def worker() -> None:
            self.run_with_env(
                env_updates,
                lambda: daily_pulse.send_webhook(
                    {"delivery": delivery},
                    f"DailyPulse Webhook 测试发送成功。\n时间: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
                ),
            )

        self.run_test("Webhook", worker)


class HistoryWindow(tk.Toplevel):
    def __init__(self, parent: "DailyPulseApp", files: list[Path]):
        super().__init__(parent)
        self.parent_app = parent
        self.files = files
        self.title("历史简报")
        self.geometry("820x520")
        self.minsize(680, 420)
        self.configure(bg=BG)

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(root, width=28, activestyle="dotbox", exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        for path in files:
            self.listbox.insert("end", path.stem.replace("daily-pulse-", ""))
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self.load_selected_preview())

        self.preview = scrolledtext.ScrolledText(
            root,
            wrap="word",
            height=14,
            background=OUTPUT_BG,
            foreground=TEXT,
            padx=12,
            pady=12,
            relief="flat",
            borderwidth=0,
        )
        self.preview.grid(row=0, column=1, sticky="nsew")

        buttons = ttk.Frame(root)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="载入预览", command=self.load_into_app, style="Accent.TButton").grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="关闭", command=self.destroy, style="Secondary.TButton").grid(row=0, column=1)

        if files:
            self.listbox.selection_set(0)
            self.load_selected_preview()

    def selected_file(self) -> Path | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        return self.files[selection[0]]

    def load_selected_preview(self) -> None:
        path = self.selected_file()
        if path is None:
            return
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", path.read_text(encoding="utf-8"))

    def load_into_app(self) -> None:
        path = self.selected_file()
        if path is None:
            return
        body = path.read_text(encoding="utf-8")
        self.parent_app.current_digest = body
        self.parent_app.set_output(body)
        self.parent_app.set_status(f"已载入历史简报 {path.name}。")
        self.destroy()


class DailyPulseApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DailyPulse")
        self.geometry("1120x760")
        self.minsize(940, 620)
        self.configure(bg=BG)

        self.config_data = load_config()
        self.env_data = read_env()
        self.pending_env_updates: dict[str, str] = {}
        self.sources: list[dict[str, Any]] = list(self.config_data.get("sources", []))
        self.source_health: dict[str, str] = {}
        self.worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_digest = ""
        self.timer_after_id: str | None = None
        self.app_icon: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        self.source_count_var = tk.StringVar(value="0 个来源")
        self.model_badge_var = tk.StringVar(value="模型未配置")
        self.sources_expanded_var = tk.BooleanVar(value=False)
        self.settings_expanded_var = tk.BooleanVar(value=False)
        self.sources_toggle_var = tk.StringVar(value="展开信息源")
        self.settings_toggle_var = tk.StringVar(value="展开配置")

        self._load_window_icon()
        self._configure_style()
        self._build_ui()
        self._load_fields()
        self._refresh_sources()
        self.show_initial_empty_state()
        self._poll_worker_queue()
        self.after(1200, self.check_for_updates)

    def _load_window_icon(self) -> None:
        if not APP_ICON_PNG.exists():
            return
        try:
            self.app_icon = tk.PhotoImage(file=str(APP_ICON_PNG))
            self.iconphoto(True, self.app_icon)
            self.header_icon = self.app_icon.subsample(24, 24)
        except tk.TclError:
            self.app_icon = None
            self.header_icon = None

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Hero.TFrame", background=BG)
        style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER, relief="solid")
        style.configure(
            "TLabelframe.Label",
            background=SURFACE,
            foreground=TEXT,
            font=("TkDefaultFont", 13, "bold"),
        )
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("MutedSurface.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("HeroTitle.TLabel", background=BG, foreground=TEXT, font=("TkDefaultFont", 25, "bold"))
        style.configure("Badge.TLabel", background=CORAL_SOFT, foreground="#9f3f2d", padding=(10, 5))
        style.configure("Count.TLabel", background=MINT_SOFT, foreground=TEAL, padding=(10, 5))
        style.configure("TButton", padding=(12, 7), font=("TkDefaultFont", 11))
        style.map("TButton", background=[("active", SKY_SOFT)])
        style.configure("Accent.TButton", background=ACCENT, foreground=SURFACE, font=("TkDefaultFont", 11, "bold"))
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)],
            foreground=[("active", SURFACE), ("pressed", SURFACE)],
        )
        style.configure("Secondary.TButton", background=SURFACE_ALT, foreground=TEXT)
        style.configure("Status.TLabel", foreground="#38514d", background=STATUS_BG, padding=(12, 8))
        style.configure(
            "Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=32,
            bordercolor=BORDER,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=SKY_SOFT,
            foreground="#335061",
            font=("TkDefaultFont", 11, "bold"),
            padding=(8, 8),
        )
        style.map("Treeview", background=[("selected", MINT_SOFT)], foreground=[("selected", TEXT)])
        style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, padding=6)
        style.configure("TCombobox", fieldbackground=SURFACE, foreground=TEXT, padding=6)
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="Hero.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        if self.header_icon:
            ttk.Label(header, image=self.header_icon, background=BG).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        ttk.Label(header, text="DailyPulse", style="HeroTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="把信息源整理成一份可发送的 AI 简报", style="Muted.TLabel", font=("TkDefaultFont", 12)).grid(
            row=1, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Label(header, textvariable=self.source_count_var, style="Count.TLabel").grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Label(header, textvariable=self.model_badge_var, style="Badge.TLabel").grid(row=0, column=3, sticky="e", padx=(8, 0))

        controls_box = ttk.LabelFrame(root, text="控制台", padding=10)
        controls_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        controls_box.columnconfigure(0, weight=1)

        control_bar = ttk.Frame(controls_box, style="Surface.TFrame")
        control_bar.grid(row=0, column=0, sticky="ew")
        control_bar.columnconfigure(8, weight=1)

        ttk.Button(
            control_bar,
            textvariable=self.sources_toggle_var,
            command=self.toggle_sources_panel,
            style="Secondary.TButton",
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            control_bar,
            textvariable=self.settings_toggle_var,
            command=self.toggle_settings_panel,
            style="Secondary.TButton",
        ).grid(row=0, column=1, padx=(0, 14))
        ttk.Button(control_bar, text="保存", command=self.save_all, style="Secondary.TButton").grid(row=0, column=2, padx=(0, 8))
        ttk.Button(control_bar, text="预览", style="Accent.TButton", command=self.preview_digest).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(control_bar, text="发送", command=self.send_digest, style="Secondary.TButton").grid(row=0, column=4, padx=(0, 8))
        ttk.Button(control_bar, text="存文件", command=self.save_digest_file, style="Secondary.TButton").grid(row=0, column=5, padx=(0, 8))
        ttk.Button(control_bar, text="历史", command=self.open_history, style="Secondary.TButton").grid(row=0, column=6, padx=(0, 8))
        ttk.Button(control_bar, text="启动定时", command=self.start_timer, style="Secondary.TButton").grid(row=0, column=7, padx=(0, 8))
        ttk.Button(control_bar, text="停止定时", command=self.stop_timer, style="Secondary.TButton").grid(row=0, column=8, sticky="w")

        self.sources_panel = ttk.Frame(controls_box, style="Surface.TFrame")
        self.sources_panel.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.sources_panel.columnconfigure(0, weight=1)

        columns = ("name", "status", "type", "limit", "url")
        self.source_tree = ttk.Treeview(self.sources_panel, columns=columns, show="headings", selectmode="browse", height=5)
        self.source_tree.heading("name", text="名称")
        self.source_tree.heading("status", text="状态")
        self.source_tree.heading("type", text="类型")
        self.source_tree.heading("limit", text="条数")
        self.source_tree.heading("url", text="URL")
        self.source_tree.column("name", width=150, minwidth=110)
        self.source_tree.column("status", width=110, minwidth=90, anchor="center")
        self.source_tree.column("type", width=70, minwidth=60, anchor="center")
        self.source_tree.column("limit", width=58, minwidth=50, anchor="center")
        self.source_tree.column("url", width=360, minwidth=240)
        self.source_tree.grid(row=0, column=0, sticky="ew")
        self.source_tree.bind("<Double-1>", lambda _event: self.edit_source())

        source_scroll = ttk.Scrollbar(self.sources_panel, orient="vertical", command=self.source_tree.yview)
        source_scroll.grid(row=0, column=1, sticky="ns")
        self.source_tree.configure(yscrollcommand=source_scroll.set)

        source_buttons = ttk.Frame(self.sources_panel, style="Surface.TFrame")
        source_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for idx, (text, command) in enumerate(
            [
                ("添加", self.add_source),
                ("编辑", self.edit_source),
                ("删除", self.delete_source),
                ("上移", lambda: self.move_source(-1)),
                ("下移", lambda: self.move_source(1)),
            ]
        ):
            ttk.Button(source_buttons, text=text, command=command, style="Secondary.TButton").grid(row=0, column=idx, padx=(0, 8))

        self.settings_panel = ttk.Frame(controls_box, style="Surface.TFrame")
        self.settings_panel.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.settings_panel.columnconfigure(1, weight=1)
        self.settings_panel.columnconfigure(3, weight=1)

        self.title_var = tk.StringVar()
        self.words_var = tk.StringVar()
        self.summary_style_var = tk.StringVar()
        self.max_items_var = tk.StringVar()
        self.schedule_var = tk.StringVar()
        self.endpoint_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.api_key_env_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.email_enabled_var = tk.BooleanVar()
        self.telegram_enabled_var = tk.BooleanVar()
        self.webhook_enabled_var = tk.BooleanVar()

        settings = [
            ("标题", ttk.Entry(self.settings_panel, textvariable=self.title_var)),
            ("摘要字数", ttk.Entry(self.settings_panel, textvariable=self.words_var)),
            (
                "摘要风格",
                ttk.Combobox(
                    self.settings_panel,
                    textvariable=self.summary_style_var,
                    values=("标准", "简洁", "深度", "行动"),
                    state="readonly",
                ),
            ),
            ("总条数上限", ttk.Entry(self.settings_panel, textvariable=self.max_items_var)),
            ("每日时间", ttk.Entry(self.settings_panel, textvariable=self.schedule_var)),
            ("AI Endpoint", ttk.Entry(self.settings_panel, textvariable=self.endpoint_var)),
            ("AI Model", ttk.Entry(self.settings_panel, textvariable=self.model_var)),
            ("密钥变量名", ttk.Entry(self.settings_panel, textvariable=self.api_key_env_var)),
            ("API Key", ttk.Entry(self.settings_panel, textvariable=self.api_key_var, show="*")),
        ]
        for idx, (label, widget) in enumerate(settings):
            row = idx // 2
            col = (idx % 2) * 2
            ttk.Label(self.settings_panel, text=label, style="Surface.TLabel").grid(
                row=row, column=col, sticky="w", padx=(0, 10), pady=5
            )
            widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 18), pady=5)

        delivery_row_index = (len(settings) + 1) // 2
        ttk.Label(self.settings_panel, text="发送渠道", style="Surface.TLabel").grid(
            row=delivery_row_index, column=0, sticky="w", padx=(0, 10), pady=(12, 5)
        )
        delivery_row = ttk.Frame(self.settings_panel, style="Surface.TFrame")
        delivery_row.grid(row=delivery_row_index, column=1, columnspan=3, sticky="w", pady=(12, 5))
        ttk.Checkbutton(delivery_row, text="邮件", variable=self.email_enabled_var).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(delivery_row, text="Telegram", variable=self.telegram_enabled_var).grid(row=0, column=1, padx=(0, 12))
        ttk.Checkbutton(delivery_row, text="Webhook", variable=self.webhook_enabled_var).grid(row=0, column=2, padx=(0, 12))
        ttk.Button(delivery_row, text="配置...", command=self.open_delivery_settings, style="Secondary.TButton").grid(
            row=0, column=3
        )

        output_box = ttk.LabelFrame(root, text="简报预览", padding=10)
        output_box.grid(row=2, column=0, sticky="nsew")
        output_box.rowconfigure(0, weight=1)
        output_box.columnconfigure(0, weight=1)

        output_font = "Consolas" if sys.platform.startswith("win") else "Menlo"
        self.output = scrolledtext.ScrolledText(
            output_box,
            wrap="word",
            font=(output_font, 12),
            height=12,
            borderwidth=0,
            relief="flat",
            background=OUTPUT_BG,
            foreground=TEXT,
            insertbackground=ACCENT,
            padx=12,
            pady=12,
        )
        self.output.grid(row=0, column=0, sticky="nsew")

        self.status_var = tk.StringVar(value="就绪")
        self.status = ttk.Label(root, textvariable=self.status_var, style="Status.TLabel")
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.apply_panel_visibility()

    def update_toggle_labels(self) -> None:
        source_marker = "-" if self.sources_expanded_var.get() else "+"
        settings_marker = "-" if self.settings_expanded_var.get() else "+"
        self.sources_toggle_var.set(f"{source_marker} 信息源 ({len(self.sources)})")
        self.settings_toggle_var.set(f"{settings_marker} 配置")

    def apply_panel_visibility(self) -> None:
        if self.sources_expanded_var.get():
            self.sources_panel.grid()
        else:
            self.sources_panel.grid_remove()

        if self.settings_expanded_var.get():
            self.settings_panel.grid()
        else:
            self.settings_panel.grid_remove()
        self.update_toggle_labels()

    def toggle_sources_panel(self) -> None:
        self.sources_expanded_var.set(not self.sources_expanded_var.get())
        self.apply_panel_visibility()

    def toggle_settings_panel(self) -> None:
        self.settings_expanded_var.set(not self.settings_expanded_var.get())
        self.apply_panel_visibility()

    def _load_fields(self) -> None:
        ai = self.config_data.get("ai", {})
        delivery = self.config_data.get("delivery", {})
        key_name = ai.get("api_key_env", "DEEPSEEK_API_KEY")
        style_labels = {"brief": "简洁", "standard": "标准", "deep": "深度", "action": "行动"}

        self.title_var.set(self.config_data.get("title", "DailyPulse 个人信息简报"))
        self.words_var.set(str(self.config_data.get("summary_words", 450)))
        self.summary_style_var.set(style_labels.get(str(self.config_data.get("summary_style", "standard")), "标准"))
        self.max_items_var.set(str(self.config_data.get("max_total_items", 30)))
        self.schedule_var.set(self.config_data.get("schedule", {}).get("time", "08:00"))
        self.endpoint_var.set(ai.get("endpoint", "https://api.deepseek.com/chat/completions"))
        self.model_var.set(ai.get("model", "deepseek-v4-flash"))
        self.model_badge_var.set(f"模型 {self.model_var.get() or '未配置'}")
        self.api_key_env_var.set(key_name)
        self.api_key_var.set(self.env_data.get(key_name, os.getenv(key_name, "")))
        self.email_enabled_var.set(bool(delivery.get("email", {}).get("enabled", False)))
        self.telegram_enabled_var.set(bool(delivery.get("telegram", {}).get("enabled", False)))
        self.webhook_enabled_var.set(bool(delivery.get("webhook", {}).get("enabled", False)))

    def _refresh_sources(self) -> None:
        for item_id in self.source_tree.get_children():
            self.source_tree.delete(item_id)
        for source in self.sources:
            key = self.source_key(source)
            self.source_tree.insert(
                "",
                "end",
                values=(
                    source.get("name", ""),
                    self.source_health.get(key, "未检查"),
                    source.get("type", "rss"),
                    source.get("limit", 5),
                    source.get("url", ""),
                ),
            )
        self.source_count_var.set(f"{len(self.sources)} 个来源")
        self.update_toggle_labels()

    def source_key(self, source: dict[str, Any]) -> str:
        return f"{source.get('name', '')}\n{source.get('url', '')}"

    def update_source_health(self, health: dict[str, str]) -> None:
        self.source_health.update(health)
        self._refresh_sources()

    def selected_source_index(self) -> int | None:
        selection = self.source_tree.selection()
        if not selection:
            return None
        return self.source_tree.index(selection[0])

    def add_source(self) -> None:
        dialog = SourceDialog(self, "添加信息源")
        if dialog.result:
            self.sources.append(dialog.result)
            self._refresh_sources()
            self.set_status("已添加信息源，记得保存配置。")

    def edit_source(self) -> None:
        index = self.selected_source_index()
        if index is None:
            messagebox.showinfo("请选择信息源", "先在左侧列表里选择一个信息源。", parent=self)
            return
        dialog = SourceDialog(self, "编辑信息源", self.sources[index])
        if dialog.result:
            self.sources[index] = dialog.result
            self._refresh_sources()
            self.set_status("已编辑信息源，记得保存配置。")

    def delete_source(self) -> None:
        index = self.selected_source_index()
        if index is None:
            messagebox.showinfo("请选择信息源", "先在左侧列表里选择一个信息源。", parent=self)
            return
        source = self.sources[index]
        if not messagebox.askyesno("删除信息源", f"确定删除「{source.get('name', '')}」吗？", parent=self):
            return
        del self.sources[index]
        self._refresh_sources()
        self.set_status("已删除信息源，记得保存配置。")

    def move_source(self, delta: int) -> None:
        index = self.selected_source_index()
        if index is None:
            return
        new_index = index + delta
        if new_index < 0 or new_index >= len(self.sources):
            return
        self.sources[index], self.sources[new_index] = self.sources[new_index], self.sources[index]
        self._refresh_sources()
        item_id = self.source_tree.get_children()[new_index]
        self.source_tree.selection_set(item_id)
        self.source_tree.focus(item_id)

    def build_config_from_fields(self) -> dict[str, Any]:
        config = dict(self.config_data)
        config["title"] = self.title_var.get().strip() or "DailyPulse 个人信息简报"
        config["summary_words"] = int(self.words_var.get().strip() or "450")
        style_values = {"简洁": "brief", "标准": "standard", "深度": "deep", "行动": "action"}
        config["summary_style"] = style_values.get(self.summary_style_var.get().strip(), "standard")
        config["max_total_items"] = int(self.max_items_var.get().strip() or "30")
        config["schedule"] = dict(config.get("schedule", {}))
        config["schedule"]["time"] = self.schedule_var.get().strip() or "08:00"
        config["sources"] = self.sources

        ai = dict(config.get("ai", {}))
        ai["endpoint"] = self.endpoint_var.get().strip()
        ai["model"] = self.model_var.get().strip()
        ai["api_key_env"] = self.api_key_env_var.get().strip() or "DEEPSEEK_API_KEY"
        ai["temperature"] = float(ai.get("temperature", 0.2))
        ai["max_context_chars"] = int(ai.get("max_context_chars", 12000))
        config["ai"] = ai

        delivery = dict(config.get("delivery", {}))
        for key, var in (
            ("email", self.email_enabled_var),
            ("telegram", self.telegram_enabled_var),
            ("webhook", self.webhook_enabled_var),
        ):
            row = dict(delivery.get(key, {}))
            row["enabled"] = bool(var.get())
            delivery[key] = row
        config["delivery"] = delivery
        return config

    def open_delivery_settings(self) -> None:
        try:
            config = self.build_config_from_fields()
        except ValueError as exc:
            messagebox.showerror("配置不正确", f"请检查数字字段：{exc}", parent=self)
            return
        dialog = DeliveryDialog(self, "发送渠道配置", config.get("delivery", {}), self.env_data | self.pending_env_updates)
        if dialog.result is None:
            return
        self.config_data = dict(self.config_data)
        self.config_data["delivery"] = dialog.result
        self.pending_env_updates.update(dialog.env_updates)
        self.email_enabled_var.set(bool(dialog.result.get("email", {}).get("enabled", False)))
        self.telegram_enabled_var.set(bool(dialog.result.get("telegram", {}).get("enabled", False)))
        self.webhook_enabled_var.set(bool(dialog.result.get("webhook", {}).get("enabled", False)))
        self.set_status("已更新发送渠道配置，记得保存配置。")

    def save_all(self) -> bool:
        try:
            config = self.build_config_from_fields()
        except ValueError as exc:
            messagebox.showerror("配置不正确", f"请检查数字字段：{exc}", parent=self)
            return False

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        key_name = config.get("ai", {}).get("api_key_env", "DEEPSEEK_API_KEY")
        env_updates = dict(self.pending_env_updates)
        env_updates[key_name] = self.api_key_var.get().strip()
        write_env(env_updates)
        for env_key, env_value in env_updates.items():
            os.environ[env_key] = env_value
        self.pending_env_updates.clear()
        self.config_data = config
        self.env_data = read_env()
        self.model_badge_var.set(f"模型 {config.get('ai', {}).get('model', '未配置')}")
        self.set_status(f"已保存 {CONFIG_PATH.name} 和 {ENV_PATH.name}。")
        return True

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.update_idletasks()

    def set_output(self, text: str) -> None:
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)

    def show_initial_empty_state(self) -> None:
        if self.sources:
            self.set_output("暂无简报。点击“预览”生成今天的 DailyPulse。\n")
            return
        self.set_output(
            "欢迎使用 DailyPulse。\n\n"
            "先展开“信息源”添加 RSS 或网页来源，再点击“预览”生成第一份简报。\n"
        )

    def run_background(self, label: str, worker: Callable[[], Any]) -> None:
        self.set_status(label)

        def target() -> None:
            try:
                result = worker()
                self.worker_queue.put(("ok", result))
            except Exception as exc:
                self.worker_queue.put(("error", exc))

        threading.Thread(target=target, daemon=True).start()

    def _poll_worker_queue(self) -> None:
        try:
            kind, payload = self.worker_queue.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_worker_queue)
            return

        if kind == "ok":
            if isinstance(payload, tuple) and payload[0] == "digest":
                self.current_digest = payload[1]
                self.set_output(payload[1])
                self.update_source_health(payload[2])
                self.save_history_entry(payload[1])
                self.set_status("简报已生成并保存到历史。")
            elif isinstance(payload, tuple) and payload[0] == "sent":
                self.current_digest = payload[1]
                self.set_output(payload[1])
                self.update_source_health(payload[2])
                self.save_history_entry(payload[1])
                self.set_status("简报已发送并保存到历史。")
            elif isinstance(payload, str):
                self.current_digest = payload
                self.set_output(payload)
                self.save_history_entry(payload)
                self.set_status("简报已生成并保存到历史。")
        else:
            self.set_status("运行失败。")
            messagebox.showerror("运行失败", str(payload), parent=self)
        self.after(150, self._poll_worker_queue)

    def check_for_updates(self) -> None:
        def worker() -> None:
            try:
                release = fetch_latest_release()
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                return
            if not release or not is_newer_version(release["tag_name"]):
                return
            self.after(0, lambda: self.prompt_for_update(release))

        threading.Thread(target=worker, daemon=True).start()

    def prompt_for_update(self, release: dict[str, str]) -> None:
        latest = release["tag_name"]
        url = release.get("html_url") or UPDATE_PAGE_URL
        should_open = messagebox.askyesno(
            "发现新版本",
            f"DailyPulse 有新版本 {latest} 可用。\n\n当前版本：v{APP_VERSION}\n是否打开下载页面？",
            parent=self,
        )
        if should_open:
            webbrowser.open(url)
            self.set_status(f"已打开 {latest} 下载页面。")

    def health_key_from_result(self, result: daily_pulse.SourceFetchResult) -> str:
        return f"{result.source.name}\n{result.source.url}"

    def source_health_from_results(self, results: list[daily_pulse.SourceFetchResult]) -> dict[str, str]:
        health: dict[str, str] = {}
        for result in results:
            if result.error:
                health[self.health_key_from_result(result)] = "失败"
            elif result.items:
                health[self.health_key_from_result(result)] = f"正常 {len(result.items)}"
            else:
                health[self.health_key_from_result(result)] = "空内容"
        return health

    def generate_digest(self) -> tuple[str, dict[str, str]]:
        config = self.build_config_from_fields()
        key_name = config.get("ai", {}).get("api_key_env", "DEEPSEEK_API_KEY")
        os.environ[key_name] = self.api_key_var.get().strip()
        daily_pulse.load_env(str(ENV_PATH))
        results = daily_pulse.collect_source_results(config)
        items: list[daily_pulse.Item] = []
        errors: list[str] = []
        for result in results:
            items.extend(result.items)
            if result.error:
                errors.append(result.error)
        if not items:
            details = "\n".join(errors) if errors else "没有抓取到可用内容。"
            raise RuntimeError(details)
        max_total = int(config.get("max_total_items", 30))
        digest = daily_pulse.build_digest(config, items[:max_total], errors)
        return digest, self.source_health_from_results(results)

    def preview_digest(self) -> None:
        if not self.save_all():
            return

        def worker() -> tuple[str, str, dict[str, str]]:
            digest, health = self.generate_digest()
            return ("digest", digest, health)

        self.run_background("正在抓取来源并生成简报...", worker)

    def send_digest(self) -> None:
        if not self.save_all():
            return
        if not messagebox.askyesno("发送简报", "确定按当前发送渠道发送一次简报吗？", parent=self):
            return

        def worker() -> tuple[str, str, dict[str, str]]:
            digest, health = self.generate_digest()
            daily_pulse.send_digest(self.build_config_from_fields(), digest)
            return ("sent", digest, health)

        self.run_background("正在生成并发送简报...", worker)

    def save_digest_file(self) -> None:
        body = self.output.get("1.0", "end").strip()
        if not body:
            messagebox.showinfo("没有内容", "先点“预览简报”生成内容。", parent=self)
            return
        default_name = f"daily-pulse-{dt.datetime.now():%Y-%m-%d}.txt"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="保存简报",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=(("Text", "*.txt"), ("All files", "*.*")),
        )
        if path:
            Path(path).write_text(body + "\n", encoding="utf-8")
            self.set_status(f"已保存到 {path}")

    def save_history_entry(self, body: str) -> None:
        if not body.strip() or body.startswith("欢迎使用 DailyPulse"):
            return
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        path = HISTORY_DIR / f"daily-pulse-{dt.datetime.now():%Y-%m-%d-%H%M%S-%f}.txt"
        path.write_text(body.rstrip() + "\n", encoding="utf-8")

    def history_files(self) -> list[Path]:
        if not HISTORY_DIR.exists():
            return []
        return sorted(HISTORY_DIR.glob("daily-pulse-*.txt"), reverse=True)

    def open_history(self) -> None:
        files = self.history_files()
        if not files:
            messagebox.showinfo("暂无历史", "生成简报后会自动保存在历史里。", parent=self)
            return
        HistoryWindow(self, files)

    def start_timer(self) -> None:
        self.stop_timer(silent=True)
        if not self.save_all():
            return
        self.schedule_next_run()

    def stop_timer(self, silent: bool = False) -> None:
        if self.timer_after_id:
            self.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        if not silent:
            self.set_status("App 内定时已停止。")

    def schedule_next_run(self) -> None:
        run_at = self.schedule_var.get().strip() or "08:00"
        try:
            hour, minute = [int(part) for part in run_at.split(":", 1)]
        except ValueError:
            messagebox.showerror("时间不正确", "每日时间请使用 HH:MM，例如 08:00。", parent=self)
            return
        wait_seconds = daily_pulse.seconds_until(hour, minute)
        self.timer_after_id = self.after(int(wait_seconds * 1000), self._timer_run)
        self.set_status(f"App 内定时已启动，下一次运行在 {hour:02d}:{minute:02d}。")

    def _timer_run(self) -> None:
        def worker() -> tuple[str, str]:
            digest = self.generate_digest()
            daily_pulse.send_digest(self.build_config_from_fields(), digest)
            return ("sent", digest)

        self.run_background("定时任务正在生成并发送简报...", worker)
        self.schedule_next_run()


def main() -> None:
    app = DailyPulseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
