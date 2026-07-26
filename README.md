# Epic Free Games Telegram Bot

Checks Epic Games Store daily for new free games and sends a Telegram notification.

## Setup

1. **Create a Telegram bot** via [@BotFather](https://t.me/BotFather) and get the token.
2. **Get your chat ID** — message [@userinfobot](https://t.me/userinfobot) or your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. **Fork this repo** and add two **GitHub Secrets** (`Settings → Secrets and variables → Actions`):

   | Secret | Value |
   |--------|-------|
   | `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
   | `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

4. **Push to GitHub** — the workflow runs daily at 10 AM IST. You can also trigger it manually from the Actions tab.

## Files

- `check_games.py` — The checker script
- `.github/workflows/check.yml` — GitHub Actions workflow
- `state.json` — Tracks previously seen games (only new ones trigger notifications)
- `requirements.txt` — Python dependencies
