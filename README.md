# Free Games Telegram Bot (Epic Games, Amazon Luna, itch.io, STOVE Store, Steam, Xbox PC & Lenovo Legion)

Checks Epic Games Store, Amazon Luna, itch.io, STOVE Store, Steam, Xbox Store (PC Games), and Lenovo Legion Key Drops daily for 100% off free games, 90%+ discount deals, and game key drops, sending notifications via Telegram.

## Setup

1. **Create a Telegram bot** via [@BotFather](https://t.me/BotFather) and get the token.
2. **Get your chat ID** — message [@userinfobot](https://t.me/userinfobot) or your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. **Fork this repo** and add two **GitHub Secrets** (`Settings → Secrets and variables → Actions`):

   | Secret | Value |
   |--------|-------|
   | `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
   | `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

4. **Push to GitHub** — the workflow runs daily at 10 AM IST. You can also trigger it manually from the Actions tab.

## Database Tables (Neon PostgreSQL)

- `tracked_games` — Tracks 100% off free store games (Epic Games, Amazon Luna, itch.io, STOVE, Steam, Xbox PC)
- `tracked_deals` — Tracks 90%+ discount sales (Steam, Epic Games, Xbox PC)
- `tracked_key_drops` — Tracks 100% free game key drops & community voucher drops (Lenovo Legion)

## Files

- `check_games.py` — Main checker script (Epic Games, Amazon Luna, itch.io, STOVE Store, Steam, Xbox PC, Lenovo Legion)
- `db.py` — Neon PostgreSQL database tracking layer (`tracked_games`, `tracked_deals`, `tracked_key_drops`)
- `.github/workflows/check.yml` — GitHub Actions workflow
- `requirements.txt` — Python dependencies


