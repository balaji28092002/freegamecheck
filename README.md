# Free Games Telegram Bot (Epic Games, Amazon Luna, itch.io & STOVE Store)

Checks Epic Games Store, Amazon Luna, itch.io, and STOVE Store daily for 100% off / free games and sends Telegram notifications.

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

- `check_games.py` — Main checker script (Epic Games, Amazon Luna, itch.io, STOVE Store)
- `db.py` — Neon PostgreSQL database tracking layer
- `.github/workflows/check.yml` — GitHub Actions workflow
- `requirements.txt` — Python dependencies
