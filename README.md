# Free Games & Deals Telegram Bot

Automated tracker that checks store platforms daily for **100% free games**, **steep discount deals (90%+ off)**, and **free game key drops**, delivering instant rich notifications via Telegram.

---

## 🎮 Supported Stores & Coverage

| Platform | Free Games (100% Off) | Deep Deals (90–99% Off) | Key Drops | Source Type |
| :--- | :---: | :---: | :---: | :--- |
| **Epic Games Store** | ✅ | ✅ | — | Official Promo API & GraphQL Catalog |
| **Amazon Luna** | ✅ | — | — | Luna Prime Gaming GraphQL |
| **Steam** | ✅ | ✅ | — | Steam Search API (paged & rate-limited) |
| **Xbox (PC)** | ✅ | ✅ | — | Xbox Deals & Microsoft DisplayCatalog |
| **itch.io** | ✅ | — | — | itch.io On-Sale Scraper |
| **STOVE Store** | ✅ | — | — | OnStove Store Search API |
| **Lenovo Legion Gaming** | — | — | ✅ | Legion Community Key Drops Scraper |

---

## 🚀 Key Features

- **Concurrent Multi-Source Checking**: Uses a worker pool to scrape and query all stores in parallel.
- **Neon PostgreSQL State Storage**: Prevents duplicate notifications by tracking previously detected games, deals, and key drops.
- **Efficient Batching**: Uses batched database upserts (`execute_values`) and thread-safe connection pooling.
- **Robust Rate-Limit & Error Handling**:
  - Automatic backoff and retries on store rate limits (HTTP 429).
  - Handles Telegram API rate limits with automatic sleep & retry (`retry_after` awareness).
  - Sends immediate Telegram alert messages if any source fetch or database connection fails.
- **Rich Telegram Notifications**: Formatted HTML alerts with promotional thumbnails, price comparisons, discounts, and direct claim links.

---

## 🛠️ Setup & Deployment

### 1. Prerequisites & Secrets
You will need:
1. A **Telegram Bot Token** from [@BotFather](https://t.me/BotFather).
2. Your **Telegram Chat ID** (obtainable via [@userinfobot](https://t.me/userinfobot) or `https://api.telegram.org/bot<TOKEN>/getUpdates`).
3. A **PostgreSQL / Neon Database URL** (e.g. `postgresql://user:pass@ep-xyz.neon.tech/neondb?sslmode=require`).

Configure the following environment variables (or **GitHub Repository Secrets** in `Settings → Secrets and variables → Actions`):

| Secret / Environment Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot API token from BotFather |
| `TELEGRAM_CHAT_ID` | Telegram chat or channel ID |
| `DATABASE_URL` | PostgreSQL connection string (Neon DB) |

### 2. GitHub Actions Automation
The repository includes `.github/workflows/check.yml` configured to run automatically:
- **Scheduled**: Runs daily at 10:00 AM IST (04:30 AM UTC).
- **Manual Trigger**: Can be run on demand from the **Actions** tab (`workflow_dispatch`).
- **Concurrency Control**: Prevents overlapping execution.

---

## 💻 Local Usage & CLI Commands

Install requirements:
```bash
pip install -r requirements.txt
```

Run the bot with optional command-line flags:

```bash
# Run full check across all stores
python check_games.py

# Test run without sending Telegram messages or writing to DB
python check_games.py --dry-run

# Check specific store(s) only
python check_games.py --source steam --source epic

# List all available source platforms
python check_games.py --list-sources
```

---

## 🗄️ Database Architecture

The application uses three dedicated tables in PostgreSQL:

- **`tracked_games`**: Stores active 100% off free games across Epic, Luna, itch.io, STOVE, Steam, and Xbox.
- **`tracked_deals`**: Stores active 90%+ discount deals across Steam, Epic, and Xbox. Automatically purges entries older than 1 year.
- **`tracked_key_drops`**: Stores claimed and active promotional key drops from Lenovo Legion.

---

## 📁 Project Structure

- `check_games.py` — Core orchestrator, concurrent scraper engine, Telegram formatting & rate-limit dispatcher.
- `db.py` — Database management layer, connection lifecycle, and batched upsert queries.
- `.github/workflows/check.yml` — GitHub Actions CI/CD cron workflow.
- `requirements.txt` — Python dependencies (`requests`, `psycopg2-binary`, `beautifulsoup4`, `python-dotenv`).



