import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DERIV_SYMBOLS = (
    "R_10,R_25,R_50,R_75,R_100,"
    "1HZ10V,1HZ25V,1HZ50V,1HZ75V,1HZ100V,"
    "JD10,JD25,JD50,JD75,JD100"
)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _symbols_env() -> list[str]:
    raw = os.getenv("DERIV_SYMBOLS", DEFAULT_DERIV_SYMBOLS)
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    DEBUG = _bool_env("FLASK_DEBUG", True)
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", "5000"))

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'signals.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DERIV_WS_URL = os.getenv(
        "DERIV_WS_URL",
        "wss://api.derivws.com/trading/v1/options/ws/public",
    )
    DERIV_PRODUCT_TYPE = os.getenv("DERIV_PRODUCT_TYPE", "").strip()
    DERIV_SYMBOLS = _symbols_env()

    CANDLE_1H_COUNT = int(os.getenv("CANDLE_1H_COUNT", "120"))
    CANDLE_15M_COUNT = int(os.getenv("CANDLE_15M_COUNT", "160"))
    SIGNAL_COOLDOWN_SECONDS = int(os.getenv("SIGNAL_COOLDOWN_SECONDS", "600"))
    SIGNAL_MIN_CONFIDENCE = float(os.getenv("SIGNAL_MIN_CONFIDENCE", "80"))
    SIGNAL_DURATION_MINUTES = int(os.getenv("SIGNAL_DURATION_MINUTES", "5"))
    SIGNAL_RETEST_CANDLES = int(os.getenv("SIGNAL_RETEST_CANDLES", "6"))

    GMAIL_ALERTS_ENABLED = _bool_env("GMAIL_ALERTS_ENABLED", False)
    GMAIL_SMTP_HOST = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com")
    GMAIL_SMTP_PORT = int(os.getenv("GMAIL_SMTP_PORT", "587"))
    GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
    ALERT_TO_EMAILS = _csv_env("ALERT_TO_EMAILS")
