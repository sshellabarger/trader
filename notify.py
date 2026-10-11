"""
Outbound notifications: email (SMTP) and ntfy push.

Why this exists: between 2026-07-22 and 2026-10-11 the paper account sat at
exactly $100,000.00 with zero orders and nobody noticed for eleven weeks,
because the only signal of life was a journal file on a server no one opened.
Anything the bot does from now on produces a message a human will see.

Configuration is entirely by environment (see .env.example):

  NOTIFY_EMAIL_TO      recipient address (empty = email disabled)
  SMTP_HOST            default smtp.gmail.com
  SMTP_PORT            default 587 (STARTTLS)
  SMTP_USER            the sending account (a Gmail address)
  SMTP_PASSWORD        a Gmail APP password, never the account password
  NTFY_TOPIC           ntfy topic name (empty = push disabled)
  NTFY_SERVER          default https://ntfy.sh

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
        return cfg

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_to and self.smtp_user and self.smtp_password)

    @property
    def push_enabled(self) -> bool:
        return bool(self.ntfy_topic)

    def describe(self) -> str:
        """Human-readable channel status with NO secrets (for logs/heartbeat)."""
        parts = []
        if self.email_enabled:
            parts.append(f"email->{self.email_to}")
        elif self.email_to:
            parts.append("email (SMTP_USER/SMTP_PASSWORD missing)")
        if self.push_enabled:
            parts.append(f"push->{self.ntfy_server}/<topic>")
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
        """Email gets the full body; push gets `push_body` (or the body
        truncated), since a phone notification has a few hundred characters."""
        results: Dict[str, bool] = {}
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


def _truncate(text: str, n: int = 900) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _scrub(text: str, c: NotifyConfig) -> str:
    """Keep secrets out of error logs even if a library echoes them."""
    for secret in (c.smtp_password, c.ntfy_topic):
        if secret:
            text = text.replace(secret, "***")
    return text
