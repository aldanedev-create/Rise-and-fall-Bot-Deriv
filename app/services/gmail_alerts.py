from __future__ import annotations

import smtplib
import threading
from email.message import EmailMessage
from typing import Any


class GmailAlertService:
    def __init__(self, config: Any) -> None:
        self.enabled = bool(config["GMAIL_ALERTS_ENABLED"])
        self.smtp_host = config["GMAIL_SMTP_HOST"]
        self.smtp_port = config["GMAIL_SMTP_PORT"]
        self.sender = config["GMAIL_ADDRESS"]
        self.password = config["GMAIL_APP_PASSWORD"]
        self.recipients = config["ALERT_TO_EMAILS"]

    def configured(self) -> bool:
        return bool(self.enabled and self.sender and self.password and self.recipients)

    def send_signal(self, signal: dict) -> bool:
        if not self.configured():
            return False

        thread = threading.Thread(
            target=self._send_signal,
            args=(signal,),
            daemon=True,
            name="gmail-signal-alert",
        )
        thread.start()
        return True

    def _send_signal(self, signal: dict) -> None:
        direction = signal.get("direction", "SIGNAL")
        symbol = signal.get("symbol", "")
        duration = (signal.get("indicators") or {}).get("duration_minutes", 5)
        subject = f"Deriv {direction} Signal | {symbol} | {duration}m"

        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = subject
        message.set_content(self._body(signal))

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(self.sender, self.password)
            smtp.send_message(message)

    @staticmethod
    def _body(signal: dict) -> str:
        indicators = signal.get("indicators") or {}
        zone = indicators.get("zone") or {}
        weights = indicators.get("confidence_weights") or {}

        lines = [
            "Deriv Rise/Fall Signal",
            "",
            f"Symbol: {signal.get('symbol')}",
            f"Direction: {signal.get('direction')}",
            f"Duration: {indicators.get('duration_minutes', 5)} minutes",
            f"Confidence: {signal.get('confidence')}%",
            f"Price: {signal.get('price')}",
            "",
            "Strategy:",
            "1H trend + 15M close-only BOS + 15M engulfing/pin bar",
            "",
            f"1H Trend: {indicators.get('trend')} ({indicators.get('trend_reason')})",
            f"15M BOS: {indicators.get('bos')}",
            f"Confirmation: {indicators.get('confirmation')}",
            f"Entry setup: {indicators.get('entry_setup')}",
            f"Entry zone: {zone.get('kind')} {zone.get('low')} - {zone.get('high')}",
            "",
            "Confidence weights:",
            f"1H trend: {weights.get('1h_trend', 40)}%",
            f"15M BOS: {weights.get('15m_bos', 30)}%",
            f"15M confirmation: {weights.get('15m_confirmation', 30)}%",
            "",
            f"Reason: {signal.get('reason')}",
            "",
            "Scan-only alert. No auto trading was executed.",
        ]
        return "\n".join(str(line) for line in lines)
