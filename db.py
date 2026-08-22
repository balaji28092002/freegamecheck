"""Database interface for Neon PostgreSQL state storage."""

import os
import sys
import threading
from datetime import datetime
from typing import Any, Optional

try:
    import psycopg2
    from psycopg2.extras import Json, execute_values
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tracked_games (
    id VARCHAR(255) PRIMARY KEY,
    platform VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    claim_url TEXT,
    thumbnail TEXT,
    original_price VARCHAR(100),
    currency VARCHAR(20),
    expiry TIMESTAMP WITH TIME ZONE,
    raw_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracked_games_platform ON tracked_games(platform);
"""

CREATE_DEALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tracked_deals (
    id VARCHAR(255) PRIMARY KEY,
    platform VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    claim_url TEXT,
    thumbnail TEXT,
    original_price VARCHAR(100),
    discounted_price VARCHAR(100),
    discount_pct VARCHAR(20),
    currency VARCHAR(20),
    expiry TIMESTAMP WITH TIME ZONE,
    raw_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracked_deals_platform ON tracked_deals(platform);
"""

CREATE_KEY_DROPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tracked_key_drops (
    id VARCHAR(255) PRIMARY KEY,
    platform VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    claim_url TEXT,
    thumbnail TEXT,
    expiry TIMESTAMP WITH TIME ZONE,
    raw_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracked_key_drops_platform ON tracked_key_drops(platform);
"""

UPSERT_GAMES_BATCH_SQL = """
INSERT INTO tracked_games (
    id, platform, title, description, claim_url, thumbnail,
    original_price, currency, expiry, raw_data
) VALUES %s
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    claim_url = EXCLUDED.claim_url,
    thumbnail = EXCLUDED.thumbnail,
    original_price = EXCLUDED.original_price,
    currency = EXCLUDED.currency,
    expiry = EXCLUDED.expiry,
    raw_data = EXCLUDED.raw_data,
    updated_at = CURRENT_TIMESTAMP;
"""

UPSERT_DEALS_BATCH_SQL = """
INSERT INTO tracked_deals (
    id, platform, title, description, claim_url, thumbnail,
    original_price, discounted_price, discount_pct, currency, expiry, raw_data
) VALUES %s
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    claim_url = EXCLUDED.claim_url,
    thumbnail = EXCLUDED.thumbnail,
    original_price = EXCLUDED.original_price,
    discounted_price = EXCLUDED.discounted_price,
    discount_pct = EXCLUDED.discount_pct,
    currency = EXCLUDED.currency,
    expiry = EXCLUDED.expiry,
    raw_data = EXCLUDED.raw_data,
    updated_at = CURRENT_TIMESTAMP;
"""

UPSERT_KEY_DROPS_BATCH_SQL = """
INSERT INTO tracked_key_drops (
    id, platform, title, description, claim_url, thumbnail,
    expiry, raw_data
) VALUES %s
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    claim_url = EXCLUDED.claim_url,
    thumbnail = EXCLUDED.thumbnail,
    expiry = EXCLUDED.expiry,
    raw_data = EXCLUDED.raw_data,
    updated_at = CURRENT_TIMESTAMP;
"""

PURGE_OLD_DEALS_SQL = """
DELETE FROM tracked_deals
WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '1 year';
"""

# The connection is shared for the whole run; a lock serializes access because
# source checks run concurrently in threads and psycopg2 connections are not thread-safe.
_DB_LOCK = threading.Lock()
_conn = None
_db_initialized = False


def is_db_enabled() -> bool:
    """Check if DATABASE_URL environment variable is configured and psycopg2 is available."""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    return bool(db_url) and HAS_PSYCOPG2


def get_connection():
    """Establish connection to PostgreSQL/Neon DB."""
    if not HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 package is required to connect to PostgreSQL database.")

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set.")

    return psycopg2.connect(db_url)


def _shared_connection():
    """Return the run-wide connection, reconnecting if it was closed."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = get_connection()
    return _conn


def _reset_connection() -> None:
    """Roll back any aborted transaction and drop the shared connection."""
    global _conn
    if _conn is not None:
        try:
            _conn.rollback()
        except Exception:
            pass
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def _report_failure(operation: str, e: Exception) -> None:
    _reset_connection()
    print(f"  ⚠️ Failed to {operation}: {e}", file=sys.stderr)


def close_connection() -> None:
    """Close the run-wide shared connection, if open."""
    with _DB_LOCK:
        _reset_connection()


def init_db() -> None:
    """Create tracked_games, tracked_deals, and tracked_key_drops tables and indexes once per run."""
    global _db_initialized
    if not is_db_enabled() or _db_initialized:
        return

    with _DB_LOCK:
        if _db_initialized:
            return
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE_SQL)
                cur.execute(CREATE_DEALS_TABLE_SQL)
                cur.execute(CREATE_KEY_DROPS_TABLE_SQL)
                cur.execute(PURGE_OLD_DEALS_SQL)
            conn.commit()
            _db_initialized = True
        except Exception as e:
            _report_failure("initialize database", e)


def load_known_game_ids(platform: str) -> set[str]:
    """Fetch set of known game IDs from DB for given platform ('epic', 'luna', 'itch', 'stove', 'steam', 'xbox')."""
    if not is_db_enabled():
        return set()

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_games WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
            conn.commit()
            return {row[0] for row in rows}
        except Exception as e:
            _report_failure("query known game IDs from DB", e)
            return set()


def load_known_deal_ids(platform: str) -> set[str]:
    """Fetch set of known deal IDs from tracked_deals table for given platform."""
    if not is_db_enabled():
        return set()

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_deals WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
            conn.commit()
            return {row[0] for row in rows}
        except Exception as e:
            _report_failure("query known deal IDs from DB", e)
            return set()


def load_known_key_drop_ids(platform: str) -> set[str]:
    """Fetch set of known key drop IDs from tracked_key_drops table for given platform."""
    if not is_db_enabled():
        return set()

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_key_drops WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
            conn.commit()
            return {row[0] for row in rows}
        except Exception as e:
            _report_failure("query known key drop IDs from DB", e)
            return set()


def purge_old_deals() -> None:
    """Purge deal entries older than 1 year from tracked_deals table alone."""
    if not is_db_enabled():
        return

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                cur.execute(PURGE_OLD_DEALS_SQL)
            conn.commit()
        except Exception as e:
            _report_failure("purge old deals from DB", e)


def _parse_expiry(raw_expiry: Any) -> Optional[datetime]:
    if not raw_expiry:
        return None
    try:
        return datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _prepare_game_params(platform: str, game: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert game dictionary into DB parameter map for tracked_games."""
    thumbnail = game.get("thumbnail", "")
    claim_url = game.get("claim_url", "")
    if platform == "epic":
        slug = game.get("product_slug") or game.get("url_slug", "")
        if slug and not claim_url:
            claim_url = f"https://store.epicgames.com/en-US/p/{slug}"
        elif not claim_url:
            claim_url = "https://store.epicgames.com/en-US/free-games"

        if not thumbnail:
            images = game.get("key_images", [])
            for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
                for img in images:
                    if img.get("type") == img_type:
                        thumbnail = img.get("url", "")
                        break
                if thumbnail:
                    break

    return {
        "id": game["id"],
        "platform": platform,
        "title": game.get("title", "Unknown Game"),
        "description": game.get("description", ""),
        "claim_url": claim_url,
        "thumbnail": thumbnail,
        "original_price": game.get("original_price", ""),
        "currency": game.get("currency", ""),
        "expiry": _parse_expiry(game.get("expiry")),
        "raw_data": Json(game),
    }


def _prepare_deal_params(platform: str, deal: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert deal dictionary into DB parameter map for tracked_deals."""
    return {
        "id": deal["id"],
        "platform": platform,
        "title": deal.get("title", "Unknown Game"),
        "description": deal.get("description", ""),
        "claim_url": deal.get("claim_url", ""),
        "thumbnail": deal.get("thumbnail", ""),
        "original_price": deal.get("original_price", ""),
        "discounted_price": deal.get("discounted_price", ""),
        "discount_pct": deal.get("discount_pct", ""),
        "currency": deal.get("currency", ""),
        "expiry": _parse_expiry(deal.get("expiry")),
        "raw_data": Json(deal),
    }


def _prepare_key_drop_params(platform: str, drop: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert key drop dictionary into DB parameter map for tracked_key_drops."""
    return {
        "id": drop["id"],
        "platform": platform,
        "title": drop.get("title", "Unknown Drop"),
        "description": drop.get("description", ""),
        "claim_url": drop.get("claim_url", ""),
        "thumbnail": drop.get("thumbnail", ""),
        "expiry": _parse_expiry(drop.get("expiry")),
        "raw_data": Json(drop),
    }


def save_games(platform: str, games: list[dict[str, Any]]) -> None:
    """Save or update free games in tracked_games DB table in a single batched upsert."""
    if not is_db_enabled() or not games:
        return

    init_db()

    tuples = [
        (
            p["id"], p["platform"], p["title"], p["description"], p["claim_url"],
            p["thumbnail"], p["original_price"], p["currency"], p["expiry"], p["raw_data"],
        )
        for p in (_prepare_game_params(platform, g) for g in games)
    ]

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                execute_values(cur, UPSERT_GAMES_BATCH_SQL, tuples)
            conn.commit()
        except Exception as e:
            _report_failure("save games to DB", e)


def save_deals(platform: str, deals: list[dict[str, Any]]) -> None:
    """Save or update 90%+ discount deals in tracked_deals DB table in a single batched upsert."""
    if not is_db_enabled() or not deals:
        return

    init_db()

    tuples = [
        (
            p["id"], p["platform"], p["title"], p["description"], p["claim_url"],
            p["thumbnail"], p["original_price"], p["discounted_price"], p["discount_pct"],
            p["currency"], p["expiry"], p["raw_data"],
        )
        for p in (_prepare_deal_params(platform, d) for d in deals)
    ]

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                execute_values(cur, UPSERT_DEALS_BATCH_SQL, tuples)
            conn.commit()
        except Exception as e:
            _report_failure("save deals to DB", e)


def save_key_drops(platform: str, drops: list[dict[str, Any]]) -> None:
    """Save or update game key drops in tracked_key_drops DB table in a single batched upsert."""
    if not is_db_enabled() or not drops:
        return

    init_db()

    tuples = [
        (
            p["id"], p["platform"], p["title"], p["description"], p["claim_url"],
            p["thumbnail"], p["expiry"], p["raw_data"],
        )
        for p in (_prepare_key_drop_params(platform, d) for d in drops)
    ]

    with _DB_LOCK:
        try:
            conn = _shared_connection()
            with conn.cursor() as cur:
                execute_values(cur, UPSERT_KEY_DROPS_BATCH_SQL, tuples)
            conn.commit()
        except Exception as e:
            _report_failure("save key drops to DB", e)
