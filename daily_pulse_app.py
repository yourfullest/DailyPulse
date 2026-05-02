#!/usr/bin/env python3
"""
DailyPulse desktop app.

A CustomTkinter GUI for editing sources, configuring DeepSeek/OpenAI-compatible
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
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

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

# ── cc-switch dark theme palette ──
BG = "#1e1e24"
BG_CARD = "#27272e"
BG_INPUT = "#333340"
BG_SELECTED = "#3b3b48"
BG_SIDEBAR = "#1a1a20"
BORDER = "#3a3a44"
TEXT = "#f0f0f5"
TEXT_DIM = "#9ca3af"
TEXT_MUTED = "#6b7280"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#60a5fa"
SUCCESS = "#22c55e"
ERROR = "#ef4444"
WARNING = "#f59e0b"
ORANGE = "#f97316"
ORANGE_HOVER = "#fb923c"


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


# ──────────────────────────────────────────────
#  CustomTkinter dialogs
# ──────────────────────────────────────────────

class SourceDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, source: dict[str, Any] | None = None):
        super().__init__(parent)
        self.title(title)
        self.geometry("480x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.source = source or {"name": "", "type": "rss", "url": "", "limit": 5}
        self.result: dict[str, Any] | None = None

        self.name_var = tk.StringVar(value=str(self.source.get("name", "")))
        self.type_var = tk.StringVar(value=str(self.source.get("type", "rss")))
        self.url_var = tk.StringVar(value=str(self.source.get("url", "")))
        self.limit_var = tk.StringVar(value=str(self.source.get("limit", 5)))

        frame = ctk.CTkFrame(self, fg_color=BG)
        frame.pack(fill="both", expand=True, padx=20, pady=16)
        frame.columnconfigure(1, weight=1)

        labels = ["名称", "类型", "URL", "条数"]
        for i, lbl in enumerate(labels):
            ctk.CTkLabel(frame, text=lbl, text_color=TEXT_DIM,
                         font=ctk.CTkFont(size=12)).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=8)

        ctk.CTkEntry(frame, textvariable=self.name_var, width=300,
                     fg_color=BG_INPUT, text_color=TEXT,
                     border_color=BORDER).grid(row=0, column=1, sticky="ew", pady=8)

        ctk.CTkComboBox(frame, variable=self.type_var, values=["rss", "web"],
                        fg_color=BG_INPUT, text_color=TEXT,
                        border_color=BORDER, width=300,
                        button_color=ACCENT, dropdown_fg_color=BG_CARD).grid(row=1, column=1, sticky="ew", pady=8)

        ctk.CTkEntry(frame, textvariable=self.url_var, width=300,
                     fg_color=BG_INPUT, text_color=TEXT,
                     border_color=BORDER).grid(row=2, column=1, sticky="ew", pady=8)

        ctk.CTkEntry(frame, textvariable=self.limit_var, width=300,
                     fg_color=BG_INPUT, text_color=TEXT,
                     border_color=BORDER).grid(row=3, column=1, sticky="ew", pady=8)

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ctk.CTkButton(btn_frame, text="取消", width=80, fg_color=BG_INPUT,
                      hover_color="#44444e", command=self._cancel).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_frame, text="确定", width=80, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self._ok).pack(side="right")

    def _ok(self) -> None:
        if not self.name_var.get().strip():
            messagebox.showerror("缺少名称", "请填写信息源名称。", parent=self)
            return
        if not self.url_var.get().strip().startswith(("http://", "https://")):
            messagebox.showerror("URL 不正确", "URL 需要以 http:// 或 https:// 开头。", parent=self)
            return
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            messagebox.showerror("条数不正确", "条数需要是整数。", parent=self)
            return
        if limit < 1:
            messagebox.showerror("条数不正确", "条数至少为 1。", parent=self)
            return
        self.result = {
            "name": self.name_var.get().strip(),
            "type": self.type_var.get().strip(),
            "url": self.url_var.get().strip(),
            "limit": limit,
        }
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class DeliveryDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, delivery: dict[str, Any], env_data: dict[str, str]):
        super().__init__(parent)
        self.title(title)
        self.geometry("560x440")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.delivery = delivery
        self.env_data = env_data
        self.result: dict[str, Any] | None = None
        self.env_updates: dict[str, str] = {}
        self._env_vars: dict[str, tk.StringVar] = {}

        frame = ctk.CTkFrame(self, fg_color=BG)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        tabview = ctk.CTkTabview(frame, fg_color=BG_CARD,
                                 segmented_button_fg_color=BG_INPUT,
                                 segmented_button_selected_color=ACCENT,
                                 segmented_button_selected_hover_color=ACCENT_HOVER,
                                 segmented_button_unselected_color=BG_INPUT,
                                 text_color=TEXT)
        tabview.pack(fill="both", expand=True)

        email_tab = tabview.add("邮件")
        telegram_tab = tabview.add("Telegram")
        webhook_tab = tabview.add("Webhook")

        self._build_email_tab(email_tab)
        self._build_telegram_tab(telegram_tab)
        self._build_webhook_tab(webhook_tab)

        # Bottom buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(btn_frame, text="取消", width=80, fg_color=BG_INPUT,
                      hover_color="#44444e", command=self._cancel).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_frame, text="确定", width=80, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self._ok).pack(side="right")

    def _labeled_entry(self, parent, label: str, var: tk.StringVar, row: int, show: str | None = None):
        ctk.CTkLabel(parent, text=label, text_color=TEXT_DIM,
                     font=ctk.CTkFont(size=12)).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
        ctk.CTkEntry(parent, textvariable=var, fg_color=BG_INPUT, text_color=TEXT,
                     border_color=BORDER, show=show if show else "").grid(row=row, column=1, sticky="ew", pady=6)

    def _env_entry(self, parent, label: str, env_key: str, row: int, show: str | None = None) -> None:
        var = tk.StringVar(value=self.env_data.get(env_key, ""))
        self._env_vars[env_key] = var
        self._labeled_entry(parent, label, var, row, show)

    def _build_email_tab(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        email = self.delivery.get("email", {})

        self.email_enabled = tk.BooleanVar(value=bool(email.get("enabled")))
        ctk.CTkCheckBox(parent, text="启用邮件发送", variable=self.email_enabled,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        checkmark_color="#ffffff").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.email_host_var = tk.StringVar(value=email.get("smtp_host", ""))
        self.email_port_var = tk.StringVar(value=str(email.get("smtp_port", 465)))
        self.email_from_var = tk.StringVar(value=email.get("from", ""))

        self._labeled_entry(parent, "SMTP 主机", self.email_host_var, 1)
        self._labeled_entry(parent, "SMTP 端口", self.email_port_var, 2)
        self._labeled_entry(parent, "发件人", self.email_from_var, 3)
        self._env_entry(parent, "SMTP 用户名", "SMTP_USERNAME", 4)
        self._env_entry(parent, "SMTP 密码", "SMTP_PASSWORD", 5, show="*")

        ctk.CTkLabel(parent, text="收件人", text_color=TEXT_DIM,
                     font=ctk.CTkFont(size=12)).grid(row=6, column=0, sticky="nw", padx=(0, 10), pady=6)
        recipients = email.get("to", [])
        recipients_text = "\n".join(recipients) if isinstance(recipients, list) else str(recipients)
        self.email_to_text = ctk.CTkTextbox(parent, height=60, fg_color=BG_INPUT, text_color=TEXT,
                                            border_color=BORDER, border_width=1)
        self.email_to_text.grid(row=6, column=1, sticky="ew", pady=6)
        self.email_to_text.insert("1.0", recipients_text)

        ctk.CTkButton(parent, text="测试邮件", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      command=self.test_email).grid(row=7, column=1, sticky="w", pady=(8, 0))

    def _build_telegram_tab(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        telegram = self.delivery.get("telegram", {})

        self.telegram_enabled = tk.BooleanVar(value=bool(telegram.get("enabled")))
        ctk.CTkCheckBox(parent, text="启用 Telegram 推送", variable=self.telegram_enabled,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        checkmark_color="#ffffff").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self._env_entry(parent, "Bot Token", "TELEGRAM_BOT_TOKEN", 1, show="*")
        self._env_entry(parent, "Chat ID", "TELEGRAM_CHAT_ID", 2)

        ctk.CTkButton(parent, text="测试 Telegram", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      command=self.test_telegram).grid(row=3, column=1, sticky="w", pady=(8, 0))

    def _build_webhook_tab(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        webhook = self.delivery.get("webhook", {})

        self.webhook_enabled = tk.BooleanVar(value=bool(webhook.get("enabled")))
        self.webhook_url_var = tk.StringVar(value=webhook.get("url", ""))
        self.webhook_url_env_var = tk.StringVar(value=webhook.get("url_env", "WEBHOOK_URL"))

        ctk.CTkCheckBox(parent, text="启用 Webhook 发送", variable=self.webhook_enabled,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        checkmark_color="#ffffff").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self._labeled_entry(parent, "URL", self.webhook_url_var, 1)
        self._labeled_entry(parent, "URL 环境变量名", self.webhook_url_env_var, 2)
        self._env_entry(parent, "URL 环境变量值", "WEBHOOK_URL", 3)

        ctk.CTkLabel(parent, text="自定义 Payload (JSON, 用 ${body} 占位)",
                     text_color=TEXT_DIM, font=ctk.CTkFont(size=12)).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 4))
        self.webhook_payload_text = ctk.CTkTextbox(parent, height=100, fg_color=BG_INPUT, text_color=TEXT,
                                                    border_color=BORDER, border_width=1)
        self.webhook_payload_text.grid(row=5, column=0, columnspan=2, sticky="ew")
        payload = webhook.get("payload")
        if payload:
            self.webhook_payload_text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))

        ctk.CTkButton(parent, text="测试 Webhook", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      command=self.test_webhook).grid(row=6, column=1, sticky="w", pady=(8, 0))

    def _collect(self) -> dict[str, Any]:
        def _split_recipients() -> list[str]:
            raw = self.email_to_text.get("1.0", "end").strip()
            return [line.strip() for line in raw.splitlines() if line.strip()]

        payload_raw = self.webhook_payload_text.get("1.0", "end").strip()
        payload = json.loads(payload_raw) if payload_raw else None

        return {
            "email": {
                "enabled": self.email_enabled.get(),
                "smtp_host": self.email_host_var.get().strip(),
                "smtp_port": int(self.email_port_var.get().strip() or "465"),
                "from": self.email_from_var.get().strip(),
                "username_env": "SMTP_USERNAME",
                "password_env": "SMTP_PASSWORD",
                "to": _split_recipients(),
            },
            "telegram": {
                "enabled": self.telegram_enabled.get(),
                "token_env": "TELEGRAM_BOT_TOKEN",
                "chat_id_env": "TELEGRAM_CHAT_ID",
            },
            "webhook": {
                "enabled": self.webhook_enabled.get(),
                "url": self.webhook_url_var.get().strip(),
                "url_env": self.webhook_url_env_var.get().strip() or "WEBHOOK_URL",
                "payload": payload,
            },
        }

    def _ok(self) -> None:
        try:
            self.result = self._collect()
        except json.JSONDecodeError as exc:
            messagebox.showerror("JSON 错误", f"自定义 payload 格式错误：{exc}", parent=self)
            return
        except ValueError as exc:
            messagebox.showerror("值错误", str(exc), parent=self)
            return
        self.env_updates = {k: v.get().strip() for k, v in self._env_vars.items() if v.get().strip()}
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _run_test(self, label: str, worker: Callable[[], None]) -> None:
        try:
            worker()
            messagebox.showinfo(label, f"{label} 成功。", parent=self)
        except Exception as exc:
            messagebox.showerror(label, f"{label} 失败：{exc}", parent=self)

    def test_email(self) -> None:
        config = load_config()
        delivery = self._collect()
        config["delivery"] = delivery
        for k, v in self._env_vars.items():
            os.environ[k] = v.get().strip()

        def worker() -> None:
            daily_pulse.send_email(config, "DailyPulse 测试邮件", "这是一封测试邮件。")

        self._run_test("邮件测试", worker)

    def test_telegram(self) -> None:
        config = load_config()
        delivery = self._collect()
        config["delivery"] = delivery
        for k, v in self._env_vars.items():
            os.environ[k] = v.get().strip()

        def worker() -> None:
            daily_pulse.send_telegram(config, "DailyPulse 测试消息")

        self._run_test("Telegram 测试", worker)

    def test_webhook(self) -> None:
        config = load_config()
        delivery = self._collect()
        config["delivery"] = delivery
        for k, v in self._env_vars.items():
            os.environ[k] = v.get().strip()

        def worker() -> None:
            daily_pulse.send_webhook(config, "DailyPulse 测试 Webhook")

        self._run_test("Webhook 测试", worker)


# ──────────────────────────────────────────────
#  Main Application
# ──────────────────────────────────────────────

class DailyPulseApp(ctk.CTk):
    NAV_ITEMS = ["总览", "信息源", "配置", "发送", "历史"]
    NAV_ICONS = {"总览": " ", "信息源": " ", "配置": "⚙", "发送": " ", "历史": " "}

    def __init__(self) -> None:
        super().__init__()

        # Appearance
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("DailyPulse")
        self.geometry("1100x720")
        self.minsize(880, 580)
        self.configure(fg_color=BG)

        self.config_data = load_config()
        self.env_data = read_env()
        self.pending_env_updates: dict[str, str] = {}
        self.sources: list[dict[str, Any]] = list(self.config_data.get("sources", []))
        self.source_health: dict[tuple[str, str], str] = {}
        self.worker_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_digest = ""
        self.timer_after_id: str | None = None
        self.skipped_version: str | None = None
        self.source_count_var = tk.StringVar(value="0 个来源")
        self.model_badge_var = tk.StringVar(value="模型未配置")
        self.current_nav = tk.StringVar(value=self.NAV_ITEMS[0])
        self._selected_source_idx: int | None = None

        self._load_window_icon()
        self._build_ui()
        self._load_fields()
        self._refresh_sources()
        self.show_initial_empty_state()
        self._poll_worker_queue()
        self.after(1200, self.check_for_updates)

    # ── Icon ──

    def _load_window_icon(self) -> None:
        if not APP_ICON_PNG.exists():
            return
        try:
            self._app_icon = tk.PhotoImage(file=str(APP_ICON_PNG))
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            pass

    # ── UI Construction ──

    def _build_ui(self) -> None:
        main = ctk.CTkFrame(self, fg_color=BG)
        main.pack(fill="both", expand=True)

        self._build_sidebar(main)
        self._build_content(main)

    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        sidebar = ctk.CTkFrame(parent, fg_color=BG_SIDEBAR, width=200, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # App header
        header = ctk.CTkFrame(sidebar, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(24, 8))
        ctk.CTkLabel(header, text=" ", font=ctk.CTkFont(size=20)).pack(side="left")
        ctk.CTkLabel(header, text="DailyPulse", text_color=TEXT,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(8, 0))

        # Separator
        ctk.CTkFrame(sidebar, fg_color=BORDER, height=1).pack(fill="x", padx=16, pady=(12, 0))

        # Nav section label
        ctk.CTkLabel(sidebar, text="导航", text_color=TEXT_MUTED,
                     font=ctk.CTkFont(size=10)).pack(anchor="w", padx=20, pady=(16, 4))

        # Nav items
        self._sidebar_buttons: dict[str, ctk.CTkButton] = {}
        for item in self.NAV_ITEMS:
            icon = self.NAV_ICONS.get(item, "")
            is_selected = item == self.current_nav.get()
            btn = ctk.CTkButton(
                sidebar,
                text=f"{icon}  {item}",
                anchor="w",
                fg_color=BG_SELECTED if is_selected else "transparent",
                text_color=TEXT if is_selected else TEXT_MUTED,
                hover_color="#2d2d36",
                font=ctk.CTkFont(size=13),
                height=36,
                corner_radius=8,
                command=lambda i=item: self._navigate(i),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self._sidebar_buttons[item] = btn

        # Bottom info
        bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=16)
        ctk.CTkFrame(bottom, fg_color=BORDER, height=1).pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(bottom, textvariable=self.source_count_var, text_color=TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).pack(anchor="w")
        ctk.CTkLabel(bottom, textvariable=self.model_badge_var, text_color=TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 0))

    def _build_content(self, parent: ctk.CTkFrame) -> None:
        content = ctk.CTkFrame(parent, fg_color=BG)
        content.pack(side="left", fill="both", expand=True)

        # Action bar
        action_bar = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=0)
        action_bar.pack(fill="x")
        ab = ctk.CTkFrame(action_bar, fg_color="transparent")
        ab.pack(fill="x", padx=20, pady=10)

        btn_style = dict(height=32, corner_radius=8, font=ctk.CTkFont(size=12, weight="bold"))
        ctk.CTkButton(ab, text="预览简报", fg_color=ORANGE, hover_color=ORANGE_HOVER,
                      **btn_style, command=self.preview_digest).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="发送", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      **btn_style, command=self.send_digest).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="保存配置", fg_color=BG_INPUT, hover_color="#44444e",
                      **btn_style, command=self.save_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="存文件", fg_color=BG_INPUT, hover_color="#44444e",
                      **btn_style, command=self.save_digest_file).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="定时", fg_color=BG_INPUT, hover_color="#44444e",
                      **btn_style, command=self.start_timer).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="停止", fg_color=BG_INPUT, hover_color="#44444e",
                      **btn_style, command=self.stop_timer).pack(side="left")

        # Separator
        ctk.CTkFrame(content, fg_color=BORDER, height=1).pack(fill="x")

        # Panels container
        self._panels: dict[str, ctk.CTkFrame] = {}
        self._panels_frame = ctk.CTkFrame(content, fg_color=BG)
        self._panels_frame.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        self._build_overview_panel()
        self._build_sources_panel()
        self._build_settings_panel()
        self._build_delivery_panel()
        self._build_history_panel()

        # Status bar
        self.status_var = tk.StringVar(value="就绪")
        ctk.CTkFrame(content, fg_color=BORDER, height=1).pack(fill="x", side="bottom")
        status_bar = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        status_bar.pack(fill="x", side="bottom", padx=20, pady=(4, 8))
        ctk.CTkLabel(status_bar, textvariable=self.status_var, text_color=TEXT_MUTED,
                     font=ctk.CTkFont(size=11)).pack(anchor="w")

        self._navigate(self.current_nav.get())

    # ── Panels ──

    def _build_overview_panel(self) -> None:
        panel = ctk.CTkFrame(self._panels_frame, fg_color=BG)
        self._panels["总览"] = panel

        ctk.CTkLabel(panel, text="简报预览", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 12))

        output_font = "Consolas" if sys.platform.startswith("win") else "Menlo"
        self.output = ctk.CTkTextbox(
            panel,
            font=ctk.CTkFont(family=output_font, size=13),
            fg_color=BG_CARD,
            text_color=TEXT,
            border_color=BORDER,
            border_width=1,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        self.output.pack(fill="both", expand=True)

    def _build_sources_panel(self) -> None:
        panel = ctk.CTkFrame(self._panels_frame, fg_color=BG)
        self._panels["信息源"] = panel

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hdr, text="信息源", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ 添加", width=80, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.add_source).pack(side="right")

        self._source_list_frame = ctk.CTkScrollableFrame(
            panel, fg_color=BG_CARD, corner_radius=8,
            border_width=1, border_color=BORDER,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=ACCENT,
        )
        self._source_list_frame.pack(fill="both", expand=True)

        btn_frame = ctk.CTkFrame(panel, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(8, 0))
        for text, cmd in [("编辑", self.edit_source), ("删除", self.delete_source),
                          ("上移", lambda: self.move_source(-1)), ("下移", lambda: self.move_source(1))]:
            ctk.CTkButton(btn_frame, text=text, width=60, fg_color=BG_INPUT,
                          hover_color="#44444e", height=30,
                          font=ctk.CTkFont(size=12),
                          command=cmd).pack(side="left", padx=(0, 6))

    def _build_settings_panel(self) -> None:
        panel = ctk.CTkFrame(self._panels_frame, fg_color=BG)
        self._panels["配置"] = panel

        ctk.CTkLabel(panel, text="API 与基本配置", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 12))

        card = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True)

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=16)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

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

        entry_cfg = dict(fg_color=BG_INPUT, text_color=TEXT, border_color=BORDER, height=32)
        fields = [
            ("标题", ctk.CTkEntry(form, textvariable=self.title_var, **entry_cfg)),
            ("摘要字数", ctk.CTkEntry(form, textvariable=self.words_var, width=100, **entry_cfg)),
            ("摘要风格", None),
            ("总条数上限", ctk.CTkEntry(form, textvariable=self.max_items_var, width=100, **entry_cfg)),
            ("每日时间", ctk.CTkEntry(form, textvariable=self.schedule_var, width=100, **entry_cfg)),
            ("AI Endpoint", ctk.CTkEntry(form, textvariable=self.endpoint_var, **entry_cfg)),
            ("AI Model", ctk.CTkEntry(form, textvariable=self.model_var, width=160, **entry_cfg)),
            ("密钥变量名", ctk.CTkEntry(form, textvariable=self.api_key_env_var, width=160, **entry_cfg)),
            ("API Key", ctk.CTkEntry(form, textvariable=self.api_key_var, show="*", width=200, **entry_cfg)),
        ]

        for idx, (label, widget) in enumerate(fields):
            row = idx // 2
            col = (idx % 2) * 2
            ctk.CTkLabel(form, text=label, text_color=TEXT_DIM,
                         font=ctk.CTkFont(size=11)).grid(row=row, column=col, sticky="w", padx=(0, 10), pady=10)
            if label == "摘要风格":
                ctk.CTkComboBox(
                    form, variable=self.summary_style_var,
                    values=["标准", "简洁", "深度", "行动"],
                    fg_color=BG_INPUT, text_color=TEXT, border_color=BORDER,
                    button_color=ACCENT, dropdown_fg_color=BG_CARD,
                    width=120, height=32,
                ).grid(row=row, column=col + 1, sticky="w", padx=(0, 18), pady=10)
            else:
                widget.grid(row=row, column=col + 1, sticky="ew", padx=(0, 18), pady=10)

    def _build_delivery_panel(self) -> None:
        panel = ctk.CTkFrame(self._panels_frame, fg_color=BG)
        self._panels["发送"] = panel

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hdr, text="发送渠道", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="详细配置...", width=100, fg_color=BG_INPUT,
                      hover_color="#44444e",
                      font=ctk.CTkFont(size=12),
                      command=self.open_delivery_settings).pack(side="right")

        card = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x")

        for icon, label, var, desc in [
            (" ", "邮件", self.email_enabled_var, "通过 SMTP 发送简报到邮箱"),
            (" ", "Telegram", self.telegram_enabled_var, "通过 Telegram Bot 推送"),
            (" ", "Webhook", self.webhook_enabled_var, "企业微信 / 飞书 / 钉钉等"),
        ]:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=2)

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", pady=8)

            left = ctk.CTkFrame(inner, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(left, text=f"{icon}  {label}", text_color=TEXT,
                         font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(left, text=desc, text_color=TEXT_MUTED,
                         font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w")

            ctk.CTkSwitch(inner, text="", variable=var, onvalue=True, offvalue=False,
                          fg_color=BORDER, progress_color=ACCENT,
                          button_color="#ffffff", button_hover_color="#dddddd",
                          width=40).pack(side="right")

            # Separator
            ctk.CTkFrame(card, fg_color=BORDER, height=1).pack(fill="x", padx=16)

    def _build_history_panel(self) -> None:
        panel = ctk.CTkFrame(self._panels_frame, fg_color=BG)
        self._panels["历史"] = panel

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hdr, text="历史简报", text_color=TEXT,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="刷新", width=60, fg_color=BG_INPUT,
                      hover_color="#44444e",
                      font=ctk.CTkFont(size=12),
                      command=self._refresh_history_list).pack(side="right")

        card = ctk.CTkFrame(panel, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True)

        self.history_listbox = tk.Listbox(
            card, activestyle="none", exportselection=False,
            bg=BG_CARD, fg=TEXT, selectbackground=ACCENT, selectforeground="#ffffff",
            relief="flat", borderwidth=0, highlightthickness=0,
            font=("Segoe UI", 11),
        )
        self.history_listbox.pack(fill="both", expand=True, padx=12, pady=12)
        self.history_listbox.bind("<Double-1>", lambda _e: self._load_history_selected())

        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(btn_frame, text="载入", width=70, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      command=self._load_history_selected).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_frame, text="删除", width=70, fg_color="#dc2626",
                      hover_color="#ef4444",
                      command=self._delete_history_selected).pack(side="left")

    # ── Navigation ──

    def _navigate(self, name: str) -> None:
        self.current_nav.set(name)
        for item_name, btn in self._sidebar_buttons.items():
            is_active = item_name == name
            btn.configure(
                fg_color=BG_SELECTED if is_active else "transparent",
                text_color=TEXT if is_active else TEXT_MUTED,
            )
        for panel_name, panel in self._panels.items():
            if panel_name == name:
                panel.pack(fill="both", expand=True)
            else:
                panel.pack_forget()
        if name == "信息源":
            self._refresh_sources()
        if name == "历史":
            self._refresh_history_list()

    # ── Fields & Sources ──

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
        self.model_badge_var.set(f"  {self.model_var.get() or '未配置'}")
        self.api_key_env_var.set(key_name)
        self.api_key_var.set(self.env_data.get(key_name, os.getenv(key_name, "")))
        self.email_enabled_var.set(bool(delivery.get("email", {}).get("enabled", False)))
        self.telegram_enabled_var.set(bool(delivery.get("telegram", {}).get("enabled", False)))
        self.webhook_enabled_var.set(bool(delivery.get("webhook", {}).get("enabled", False)))

    def _refresh_sources(self) -> None:
        for widget in self._source_list_frame.winfo_children():
            widget.destroy()

        if not self.sources:
            ctk.CTkLabel(self._source_list_frame, text="暂无信息源，点击上方 + 添加。",
                         text_color=TEXT_MUTED, font=ctk.CTkFont(size=12)).pack(pady=32)
            self.source_count_var.set("0 个来源")
            return

        for idx, source in enumerate(self.sources):
            key = self.source_key(source)
            health = self.source_health.get(key, "")
            src_type = source.get("type", "rss").upper()
            subtitle = f"{src_type} · {source.get('url', '')}"
            if health:
                subtitle += f"  ({health})"

            row = ctk.CTkFrame(self._source_list_frame, fg_color="transparent", cursor="hand2")
            row.pack(fill="x", padx=4, pady=2)

            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", pady=6)

            # Number badge
            ctk.CTkLabel(inner, text=f" {idx + 1}", text_color=TEXT_MUTED,
                         font=ctk.CTkFont(size=14), width=36).pack(side="left")

            # Text
            text_frame = ctk.CTkFrame(inner, fg_color="transparent")
            text_frame.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_frame, text=source.get("name", ""), text_color=TEXT,
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(text_frame, text=subtitle, text_color=TEXT_MUTED,
                         font=ctk.CTkFont(size=10), anchor="w").pack(anchor="w")

            # Click handler
            def _make_click(i=idx):
                def _handler(_e=None):
                    self._select_source(i)
                return _handler

            for w in [row, inner, text_frame] + list(inner.winfo_children()) + list(text_frame.winfo_children()):
                w.bind("<Button-1>", _make_click(idx))

            # Separator
            ctk.CTkFrame(self._source_list_frame, fg_color=BORDER, height=1).pack(fill="x", padx=12)

        self.source_count_var.set(f"{len(self.sources)} 个来源")

    def _select_source(self, index: int) -> None:
        self._selected_source_idx = index

    def source_key(self, source: dict[str, Any]) -> tuple[str, str]:
        return (source.get("name", ""), source.get("url", ""))

    def update_source_health(self, health: dict[str, str]) -> None:
        self.source_health.update(health)
        self._refresh_sources()

    def selected_source_index(self) -> int | None:
        return getattr(self, "_selected_source_idx", None)

    def add_source(self) -> None:
        dialog = SourceDialog(self, "添加信息源")
        self.wait_window(dialog)
        if dialog.result:
            self.sources.append(dialog.result)
            self._refresh_sources()
            self.set_status("已添加信息源，记得保存配置。")

    def edit_source(self) -> None:
        index = self.selected_source_index()
        if index is None:
            messagebox.showinfo("请选择信息源", "先在列表里选择一个信息源。", parent=self)
            return
        dialog = SourceDialog(self, "编辑信息源", self.sources[index])
        self.wait_window(dialog)
        if dialog.result:
            self.sources[index] = dialog.result
            self._refresh_sources()
            self.set_status("已编辑信息源，记得保存配置。")

    def delete_source(self) -> None:
        index = self.selected_source_index()
        if index is None:
            messagebox.showinfo("请选择信息源", "先在列表里选择一个信息源。", parent=self)
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
        self._selected_source_idx = new_index
        self._refresh_sources()

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
        self.wait_window(dialog)
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
        self.model_badge_var.set(f"  {config.get('ai', {}).get('model', '未配置')}")
        if self.timer_after_id:
            self.schedule_next_run()
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
            self.set_output("暂无简报。点击「预览简报」生成今天的 DailyPulse。\n")
            return
        self.set_output(
            "欢迎使用 DailyPulse。\n\n"
            "先在「信息源」中添加 RSS 或网页来源，再点击「预览简报」生成第一份简报。\n"
        )

    # ── Background workers ──

    def run_background(self, label: str, worker: Callable[[], Any]) -> None:
        self.set_status(label)

        def target() -> None:
            try:
                result = worker()
                self.worker_queue.put(("ok", result))
            except Exception as exc:
                self.worker_queue.put(("error", exc))

        threading.Thread(target=target, daemon=True).start()

    _POLL_INTERVAL_MS = 250

    def _poll_worker_queue(self) -> None:
        try:
            kind, payload = self.worker_queue.get_nowait()
        except queue.Empty:
            self.after(self._POLL_INTERVAL_MS, self._poll_worker_queue)
            return

        if kind == "ok":
            if isinstance(payload, tuple) and payload[0] == "digest":
                self.current_digest = payload[1]
                self.set_output(payload[1])
                self.update_source_health(payload[2])
                self.save_history_entry(payload[1])
                self.set_status("简报已生成并保存到历史。")
                self._navigate("总览")
            elif isinstance(payload, tuple) and payload[0] == "sent":
                self.current_digest = payload[1]
                self.set_output(payload[1])
                self.update_source_health(payload[2])
                self.save_history_entry(payload[1])
                self.set_status("简报已发送并保存到历史。")
                self._navigate("总览")
            elif isinstance(payload, str):
                self.current_digest = payload
                self.set_output(payload)
                self.save_history_entry(payload)
                self.set_status("简报已生成并保存到历史。")
        else:
            self.set_status("运行失败。")
            messagebox.showerror("运行失败", str(payload), parent=self)
        self.after(0, self._poll_worker_queue)

    # ── Update checking ──

    def check_for_updates(self) -> None:
        def worker() -> None:
            try:
                release = fetch_latest_release()
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                return
            if not release or not is_newer_version(release["tag_name"]):
                return
            if release["tag_name"] == self.skipped_version:
                return
            self.after(0, lambda: self.prompt_for_update(release))

        threading.Thread(target=worker, daemon=True).start()

    def prompt_for_update(self, release: dict[str, str]) -> None:
        latest = release["tag_name"]
        url = release.get("html_url") or UPDATE_PAGE_URL
        result = messagebox.askyesnocancel(
            "发现新版本",
            f"DailyPulse 有新版本 {latest} 可用。\n\n当前版本：v{APP_VERSION}",
            parent=self,
        )
        if result is True:
            webbrowser.open(url)
            self.set_status(f"已打开 {latest} 下载页面。")
        elif result is False:
            self.skipped_version = latest
            self.set_status(f"已跳过版本 {latest}。")

    # ── Digest generation ──

    def health_key_from_result(self, result: daily_pulse.SourceFetchResult) -> tuple[str, str]:
        return (result.source.name, result.source.url)

    def source_health_from_results(self, results: list[daily_pulse.SourceFetchResult]) -> dict[tuple[str, str], str]:
        health: dict[tuple[str, str], str] = {}
        for result in results:
            if result.error:
                health[self.health_key_from_result(result)] = "失败"
            elif result.items:
                health[self.health_key_from_result(result)] = f"正常 {len(result.items)}"
            else:
                health[self.health_key_from_result(result)] = "空内容"
        return health

    def generate_digest(self) -> tuple[str, dict[tuple[str, str], str]]:
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

        def worker() -> tuple[str, str, dict[tuple[str, str], str]]:
            digest, health = self.generate_digest()
            return ("digest", digest, health)

        self.run_background("正在抓取来源并生成简报...", worker)

    def send_digest(self) -> None:
        if not self.save_all():
            return
        if not messagebox.askyesno("发送简报", "确定按当前发送渠道发送一次简报吗？", parent=self):
            return

        def worker() -> tuple[str, str, dict[tuple[str, str], str]]:
            digest, health = self.generate_digest()
            daily_pulse.send_digest(self.build_config_from_fields(), digest)
            return ("sent", digest, health)

        self.run_background("正在生成并发送简报...", worker)

    def save_digest_file(self) -> None:
        body = self.output.get("1.0", "end").strip()
        if not body:
            messagebox.showinfo("没有内容", "先点「预览简报」生成内容。", parent=self)
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

    # ── History ──

    MAX_HISTORY_FILES = 90

    def save_history_entry(self, body: str) -> None:
        if not body.strip() or body.startswith("欢迎使用 DailyPulse"):
            return
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        path = HISTORY_DIR / f"daily-pulse-{dt.datetime.now():%Y-%m-%d-%H%M%S-%f}.txt"
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        self._cleanup_history()

    def _cleanup_history(self) -> None:
        files = sorted(HISTORY_DIR.glob("daily-pulse-*.txt"))
        excess = len(files) - self.MAX_HISTORY_FILES
        if excess > 0:
            for path in files[:excess]:
                path.unlink(missing_ok=True)

    def history_files(self) -> list[Path]:
        if not HISTORY_DIR.exists():
            return []
        return sorted(HISTORY_DIR.glob("daily-pulse-*.txt"), reverse=True)

    def _refresh_history_list(self) -> None:
        self.history_listbox.delete(0, "end")
        for path in self.history_files():
            self.history_listbox.insert("end", path.stem.replace("daily-pulse-", ""))

    def _selected_history_file(self) -> Path | None:
        sel = self.history_listbox.curselection()
        if not sel:
            return None
        files = self.history_files()
        idx = sel[0]
        return files[idx] if idx < len(files) else None

    def _load_history_selected(self) -> None:
        path = self._selected_history_file()
        if path is None:
            messagebox.showinfo("请选择", "先在列表里选择一份历史简报。", parent=self)
            return
        body = path.read_text(encoding="utf-8")
        self.current_digest = body
        self.set_output(body)
        self.set_status(f"已载入历史简报 {path.name}。")
        self._navigate("总览")

    def _delete_history_selected(self) -> None:
        path = self._selected_history_file()
        if path is None:
            return
        if not messagebox.askyesno("删除", f"确定删除 {path.name} 吗？", parent=self):
            return
        path.unlink(missing_ok=True)
        self._refresh_history_list()
        self.set_status(f"已删除 {path.name}。")

    def open_history(self) -> None:
        self._navigate("历史")

    # ── Timer ──

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
        def worker() -> tuple[str, str, dict[tuple[str, str], str]]:
            digest, health = self.generate_digest()
            daily_pulse.send_digest(self.build_config_from_fields(), digest)
            return ("sent", digest, health)

        self.run_background("定时任务正在生成并发送简报...", worker)
        self.schedule_next_run()


def main() -> None:
    app = DailyPulseApp()
    app.mainloop()


if __name__ == "__main__":
    raise SystemExit(main())
