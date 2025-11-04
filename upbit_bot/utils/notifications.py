"""Notification helpers for trade events."""

from __future__ import annotations

import abc
import json
import logging
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class Notifier(abc.ABC):
    """Abstract base class for sending notifications."""

    @abc.abstractmethod
    def send(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError


class SlackNotifier(Notifier):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, message: str, **kwargs: Any) -> None:
        payload = {"text": message}
        payload.update(kwargs)
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=5)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to send Slack notification: %s", exc)


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, message: str, **kwargs: Any) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": message}
        payload.update(kwargs)
        try:
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to send Telegram notification: %s", exc)


class ConsoleNotifier(Notifier):
    """Fallback notifier that logs to stdout."""

    def send(self, message: str, **kwargs: Any) -> None:
        LOGGER.info("NOTIFY: %s | extra=%s", message, json.dumps(kwargs))
