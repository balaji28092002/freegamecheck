"""Database interface for Neon PostgreSQL state storage."""

import json
import os
import sys
from datetime import datetime
from typing import Any, Optional

try:
    import psycopg2
    from psycopg2.extras import Json
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

UPSERT_GAME_SQL = """
INSERT INTO tracked_games (
    id, platform, title, description, claim_url, thumbnail,
    original_price, currency, expiry, raw_data, updated_at
) VALUES (
    %(id)s, %(platform)s, %(title)s, %(description)s, %(claim_url)s, %(thumbnail)s,
    %(original_price)s, %(currency)s, %(expiry)s, %(raw_data)s, CURRENT_TIMESTAMP
)
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

UPSERT_DEAL_SQL = """
INSERT INTO tracked_deals (
    id, platform, title, description, claim_url, thumbnail,
    original_price, discounted_price, discount_pct, currency, expiry, raw_data, updated_at
) VALUES (
    %(id)s, %(platform)s, %(title)s, %(description)s, %(claim_url)s, %(thumbnail)s,
    %(original_price)s, %(discounted_price)s, %(discount_pct)s, %(currency)s, %(expiry)s, %(raw_data)s, CURRENT_TIMESTAMP
)
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

UPSERT_KEY_DROP_SQL = """
INSERT INTO tracked_key_drops (
    id, platform, title, description, claim_url, thumbnail,
    expiry, raw_data, updated_at
) VALUES (
    %(id)s, %(platform)s, %(title)s, %(description)s, %(claim_url)s, %(thumbnail)s,
    %(expiry)s, %(raw_data)s, CURRENT_TIMESTAMP
)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    claim_url = EXCLUDED.claim_url,
    thumbnail = EXCLUDED.thumbnail,
    expiry = EXCLUDED.expiry,
    raw_data = EXCLUDED.raw_data,
    updated_at = CURRENT_TIMESTAMP;
"""


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


def init_db() -> None:
    """Create tracked_games, tracked_deals, and tracked_key_drops tables and indexes if they do not exist."""
    if not is_db_enabled():
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE_SQL)
                cur.execute(CREATE_DEALS_TABLE_SQL)
                cur.execute(CREATE_KEY_DROPS_TABLE_SQL)
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ Failed to initialize database: {e}", file=sys.stderr)


def load_known_game_ids(platform: str) -> set[str]:
    """Fetch set of known game IDs from DB for given platform ('epic', 'luna', 'itch', 'stove', 'steam', 'xbox')."""
    if not is_db_enabled():
        return set()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_games WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
                return {row[0] for row in rows}
    except Exception as e:
        print(f"  ⚠️ Failed to query known game IDs from DB: {e}", file=sys.stderr)
        return set()


def load_known_deal_ids(platform: str) -> set[str]:
    """Fetch set of known deal IDs from tracked_deals table for given platform."""
    if not is_db_enabled():
        return set()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_deals WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
                return {row[0] for row in rows}
    except Exception as e:
        print(f"  ⚠️ Failed to query known deal IDs from DB: {e}", file=sys.stderr)
        return set()


def load_known_key_drop_ids(platform: str) -> set[str]:
    """Fetch set of known key drop IDs from tracked_key_drops table for given platform."""
    if not is_db_enabled():
        return set()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tracked_key_drops WHERE platform = %s;", (platform,))
                rows = cur.fetchall()
                return {row[0] for row in rows}
    except Exception as e:
        print(f"  ⚠️ Failed to query known key drop IDs from DB: {e}", file=sys.stderr)
        return set()


def _prepare_key_drop_params(platform: str, drop: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert key drop dictionary into DB parameter map for tracked_key_drops."""
    raw_expiry = drop.get("expiry")
    expiry_dt: Optional[datetime] = None
    if raw_expiry:
        try:
            expiry_dt = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expiry_dt = None

    return {
        "id": drop["id"],
        "platform": platform,
        "title": drop.get("title", "Unknown Drop"),
        "description": drop.get("description", ""),
        "claim_url": drop.get("claim_url", ""),
        "thumbnail": drop.get("thumbnail", ""),
        "expiry": expiry_dt,
        "raw_data": Json(drop),
    }


def save_key_drops(platform: str, drops: list[dict[str, Any]]) -> None:
    """Save or update game key drops in tracked_key_drops DB table."""
    if not is_db_enabled() or not drops:
        return

    init_db()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for drop in drops:
                    params = _prepare_key_drop_params(platform, drop)
                    cur.execute(UPSERT_KEY_DROP_SQL, params)
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ Failed to save key drops to DB: {e}", file=sys.stderr)



def _prepare_game_params(platform: str, game: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert game dictionary into DB parameter map for tracked_games."""
    raw_expiry = game.get("expiry")
    expiry_dt: Optional[datetime] = None
    if raw_expiry:
        try:
            expiry_dt = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expiry_dt = None

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
        "expiry": expiry_dt,
        "raw_data": Json(game),
    }


def _prepare_deal_params(platform: str, deal: dict[str, Any]) -> dict[str, Any]:
    """Helper to convert deal dictionary into DB parameter map for tracked_deals."""
    raw_expiry = deal.get("expiry")
    expiry_dt: Optional[datetime] = None
    if raw_expiry:
        try:
            expiry_dt = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expiry_dt = None

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
        "expiry": expiry_dt,
        "raw_data": Json(deal),
    }


def save_games(platform: str, games: list[dict[str, Any]]) -> None:
    """Save or update free games in tracked_games DB table."""
    if not is_db_enabled() or not games:
        return

    init_db()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for game in games:
                    params = _prepare_game_params(platform, game)
                    cur.execute(UPSERT_GAME_SQL, params)
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ Failed to save games to DB: {e}", file=sys.stderr)


PURGE_OLD_DEALS_SQL = """
DELETE FROM tracked_deals 
WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '1 year';
"""


def purge_old_deals() -> None:
    """Purge deal entries older than 1 year from tracked_deals table alone."""
    if not is_db_enabled():
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(PURGE_OLD_DEALS_SQL)
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ Failed to purge old deals from DB: {e}", file=sys.stderr)


def save_deals(platform: str, deals: list[dict[str, Any]]) -> None:
    """Save or update 90%+ discount deals in tracked_deals DB table and purge deals older than 1 year."""
    if not is_db_enabled() or not deals:
        return

    init_db()
    purge_old_deals()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for deal in deals:
                    params = _prepare_deal_params(platform, deal)
                    cur.execute(UPSERT_DEAL_SQL, params)
            conn.commit()
    except Exception as e:
        print(f"  ⚠️ Failed to save deals to DB: {e}", file=sys.stderr)


