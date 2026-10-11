"""
Outbound notifications: email (SMTP) and ntfy push.

Why this exists: between 2026-07-22 and 2026-10-11 the paper account sat at
exactly $100,000.00 with zero orders and nobody noticed for eleven weeks,
because the only signal of life was a journal file on a server no one opened.
Anything the bot does from now on produces a message a human will see.

Configuration is entirely by environment (see .env.example). Three channels,
any combination, none of them requires a Google account:

  TELEGRAM_BOT_TOKEN   from @BotFather; carries the FULL report in monospace
  TELEGRAM_CHAT_ID     your chat with the bot (empty = Telegram disabled)
  NTFY_TOPIC           ntfy topic name; short push + alerts (empty = disabled)
  NTFY_SERVER          default https://ntfy.sh
  NOTIFY_EMAIL_TO      recipient address (empty = email disabled)
  SMTP_HOST/PORT/USER/PASSWORD   any SMTP provider (STARTTLS)

Every send is best-effort and logged: a notification failure must never stop
a rebalance or a trading day. Secrets are read here and nowhere else, and the
dataclass repr hides them (the same lesson as BrokerConfig).
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    email_to: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    ntfy_topic: str = field(default="", repr=False)
    ntfy_server: str = "https://ntfy.sh"
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    timeout_s: float = 20.0

    @classmethod
    def from_env(cls) -> "NotifyConfig":
        cfg = cls()
        cfg.email_to = os.getenv("NOTIFY_EMAIL_TO", cfg.email_to).strip()
        cfg.smtp_host = os.getenv("SMTP_HOST", cfg.smtp_host).strip() or cfg.smtp_host
        try:
            cfg.smtp_port = int(os.getenv("SMTP_PORT", str(cfg.smtp_port)))
        except ValueError:
            pass
        cfg.smtp_user = os.getenv("SMTP_USER", cfg.smtp_user).strip()
        cfg.smtp_password = os.getenv("SMTP_PASSWORD", cfg.smtp_password).strip()
        cfg.ntfy_topic = os.getenv("NTFY_TOPIC", cfg.ntfy_topic).strip()
        cfg.ntfy_server = (os.getenv("NTFY_SERVER", cfg.ntfy_server).strip()
                           or cfg.ntfy_server).rstrip("/")
        cfg.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", cfg.telegram_token).strip()
        cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", cfg.telegram_chat_id).strip()
        return cfg

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_to and self.smtp_user and self.smtp_password)

    @property
    def push_enabled(self) -> bool:
        return bool(self.ntfy_topic)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def describe(self) -> str:
        """Human-readable channel status with NO secrets (for logs/heartbeat)."""
        parts = []
        if self.telegram_enabled:
            parts.append("telegram")
        elif self.telegram_token or self.telegram_chat_id:
            parts.append("telegram (need BOTH TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
        if self.push_enabled:
            parts.append(f"push->{self.ntfy_server}/<topic>")
        if self.email_enabled:
            parts.append(f"email->{self.email_to}")
        elif self.email_to:
            parts.append("email (SMTP_USER/SMTP_PASSWORD missing)")
        return ", ".join(parts) or "no channels configured"


class Notifier:
    """Send the same message to every configured channel. Returns per-channel
    success so callers can log what actually went out."""

    def __init__(self, config: Optional[NotifyConfig] = None,
                 session: Optional[requests.Session] = None):
        self.config = config or NotifyConfig.from_env()
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    def send(self, subject: str, body: str, *, push_body: Optional[str] = None,
             priority: str = "default", tags: Optional[List[str]] = None) -> Dict[str, bool]:
        """Telegram and email get the full body; push gets `push_body` (or the
        body truncated), since a phone notification has a few hundred
        characters."""
        results: Dict[str, bool] = {}
        if self.config.telegram_enabled:
            results["telegram"] = self.send_telegram(subject, body)
        if self.config.email_enabled:
            results["email"] = self.send_email(subject, body)
        if self.config.push_enabled:
            results["push"] = self.send_push(subject, push_body or _truncate(body),
                                             priority=priority, tags=tags)
        if not results:
            logger.warning("Notifier: no channels configured; message dropped: %s", subject)
        return results

    def alert(self, subject: str, body: str) -> Dict[str, bool]:
        """High-priority variant for failures (auth errors, rejected orders)."""
        return self.send(subject, body, priority="high", tags=["warning"])

    # ------------------------------------------------------------------
    def send_email(self, subject: str, body: str) -> bool:
        c = self.config
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.smtp_user
        msg["To"] = c.email_to
        msg.set_content(body)
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=c.timeout_s) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(c.smtp_user, c.smtp_password)
                s.send_message(msg)
            logger.info("Notifier: email sent (%s)", subject)
            return True
        except Exception as exc:  # noqa: BLE001 - never let a notification kill the caller
            logger.error("Notifier: email failed (%s): %s", subject, _scrub(str(exc), c))
            return False

    def send_push(self, title: str, body: str, *, priority: str = "default",
                  tags: Optional[List[str]] = None) -> bool:
        c = self.config
        url = f"{c.ntfy_server}/{c.ntfy_topic}"
        headers = {
            # ntfy headers are latin-1; keep the title ASCII-safe.
            "Title": title.encode("ascii", "replace").decode("ascii"),
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        try:
            r = self._session.post(url, data=body.encode("utf-8"), headers=headers,
                                   timeout=c.timeout_s)
            r.raise_for_status()
            logger.info("Notifier: push sent (%s)", title)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Notifier: push failed (%s): %s", title, _scrub(str(exc), c))
            return False


    def send_telegram(self, subject: str, body: str) -> bool:
        """Full report as one or more <pre> messages (Telegram caps a message
        at 4096 chars), so the fixed-width tables stay aligned on a phone."""
        c = self.config
        url = f"https://api.telegram.org/bot{c.telegram_token}/sendMessage"
        ok = True
        # Escape BEFORE chunking so entity expansion can't push a chunk past
        # the 4096 limit; chunks split on line boundaries so entities survive.
        for i, chunk in enumerate(_chunks(_html(body), TELEGRAM_CHUNK)):
            head = f"<b>{_html(subject)[:200]}</b>\n" if i == 0 else ""
            payload = {"chat_id": c.telegram_chat_id, "parse_mode": "HTML",
                       "disable_web_page_preview": True,
                       "text": f"{head}<pre>{chunk}</pre>"}
            try:
                r = self._session.post(url, json=payload, timeout=c.timeout_s)
                r.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logger.error("Notifier: telegram failed (%s): %s", subject, _scrub(str(exc), c))
                ok = False
                break
        if ok:
            logger.info("Notifier: telegram sent (%s)", subject)
        return ok


TELEGRAM_CHUNK = 3800   # + header/<pre> wrapper stays under 4096


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _chunks(text: str, n: int) -> List[str]:
    """Split on line boundaries into pieces of at most n characters."""
    text = text.strip() or "(empty)"
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > n:                      # a single over-long line
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:n])
            line = line[n:]
        if len(cur) + len(line) > n:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out


def _truncate(text: str, n: int = 900) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _scrub(text: str, c: NotifyConfig) -> str:
    """Keep secrets out of error logs even if a library echoes them."""
    for secret in (c.smtp_password, c.ntfy_topic, c.telegram_token):
        if secret:
            text = text.replace(secret, "***")
    return text
