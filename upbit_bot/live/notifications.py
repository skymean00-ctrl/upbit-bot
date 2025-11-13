"""Notification helpers for Slack and e-mail alerts."""
from __future__ import annotations

import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, List, Protocol
from urllib import request


class NotificationError(RuntimeError):
    """Raised when sending a notification fails."""


class Notifier(Protocol):
    def send(self, message: str) -> None:  # pragma: no cover - protocol hook
        ...


@dataclass
class SlackNotifier:
    webhook_url: str
    timeout: int = 5

    def send(self, message: str) -> None:
        payload = json.dumps({"text": message}).encode("utf-8")
        req = request.Request(
            self.webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:  # type: ignore[attr-defined]
                if resp.status >= 400:
                    raise NotificationError(f"Slack error: {resp.status}")
        except Exception as exc:  # pragma: no cover - network failure
            raise NotificationError("Failed to send Slack notification") from exc


@dataclass
class EmailNotifier:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: Iterable[str]
    use_tls: bool = True

    def send(self, message: str) -> None:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = ", ".join(self.recipients)
        email["Subject"] = "Upbit Bot Alert"
        email.set_content(message)

        context = ssl.create_default_context()
        try:
            if self.use_tls:
                with smtplib.SMTP_SSL(self.host, self.port, context=context) as server:
                    server.login(self.username, self.password)
                    server.send_message(email)
            else:  # pragma: no cover - rarely configured
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                    server.send_message(email)
        except Exception as exc:  # pragma: no cover - network failure
            raise NotificationError("Failed to send email notification") from exc


@dataclass
class NotificationManager:
    notifiers: List[Notifier]

    def notify(self, message: str) -> None:
        errors: List[Exception] = []
        for notifier in self.notifiers:
            try:
                notifier.send(message)
            except Exception as exc:  # pragma: no cover - fan-out errors
                errors.append(exc)
        if errors:
            raise NotificationError(
                "; ".join(str(error) for error in errors)
            ) from errors[0]


__all__ = [
    "NotificationError",
    "Notifier",
    "SlackNotifier",
    "EmailNotifier",
    "NotificationManager",
]
