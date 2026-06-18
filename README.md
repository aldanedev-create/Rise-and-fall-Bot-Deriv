# Deriv Rise/Fall Signal Bot

A Flask + Flask-SocketIO dashboard that scans Deriv index candle data and generates Rise/Fall signals.

This project is scan-only. It uses Deriv public candle market data, does not authenticate Deriv accounts, does not read ticks, and does not call trading endpoints such as `proposal`, `buy`, `sell`, or `proposal_open_contract`.

## Strategy

Signals use this structure:

```text
1H candles = main trend direction only: uptrend or downtrend
15M candles = sideways/ranging filter, support/resistance, BOS, entry confirmation
Signal duration = 5 minutes
Alert = Gmail when configured
```

RISE signal:

```text
1H trend = Uptrend
15M candle closes above resistance
15M candle gives Bullish Engulfing, Bullish Pin Bar, or strong bullish close
15M market is not sideways
```

FALL signal:

```text
1H trend = Downtrend
15M candle closes below support
15M candle gives Bearish Engulfing, Bearish Pin Bar, or strong bearish close
15M market is not sideways
```

Confidence weighting:

```text
1H trend direction = 40%
15M BOS = 30%
15M engulfing/pin bar = 30%
```

## Stack

- Flask
- Flask-SocketIO
- SQLAlchemy / Flask-SQLAlchemy
- python-dotenv
- websockets
- HTML, CSS, and JavaScript
- Gmail SMTP alerts through Python's standard library

## Project Structure

```text
deriv_signal_bot/
  app/
    services/
      deriv_client.py
      gmail_alerts.py
      scanner.py
      signal_engine.py
    static/
      css/styles.css
      js/dashboard.js
    templates/
      index.html
    __init__.py
    extensions.py
    models.py
    routes.py
    sockets.py
  instance/
    .gitkeep
  .env.example
  .gitignore
  config.py
  README.md
  requirements.txt
  run.py
```

## Setup

```bash
cd deriv_signal_bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

Open `http://127.0.0.1:5000`.

## Gmail Alerts

Set these values in `.env` to enable Gmail alerts:

```text
GMAIL_ALERTS_ENABLED=True
GMAIL_ADDRESS=your_gmail@gmail.com
GMAIL_APP_PASSWORD=your_google_app_password
ALERT_TO_EMAILS=destination@example.com
```

Use a Gmail app password, not your normal Gmail password.

## Notes

- The scanner uses Deriv `ticks_history` with `style: candles`.
- Candle granularities are 3600 seconds for 1H and 900 seconds for 15M.
- The 1H timeframe is used only to resolve direction as uptrend or downtrend; the 15M timeframe decides sideways/ranging conditions and entry.
- Signals are stored in SQLite at `instance/signals.db` by default.
- Defaults include Volatility 10/25/50/75/100, Volatility 10/25/50/75/100 (1s), and Jump 10/25/50/75/100.
- You can change scanned symbols in `.env` with `DERIV_SYMBOLS=R_10,1HZ100V,JD100,...`.
- No martingale, auto trading, buy/sell execution, Deriv token, or account auth is included.

## Deriv API References

- Public WebSocket market data endpoint: https://developers.deriv.com/docs/options/websocket/
- Candle history endpoint: https://developers.deriv.com/docs/data/ticks-history/
- Active symbols market data: https://developers.deriv.com/docs/data/active-symbols/
