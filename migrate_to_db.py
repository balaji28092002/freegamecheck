#!/usr/bin/env python3
"""Migrate existing state.json and state_luna.json files into Neon PostgreSQL database."""

import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db

STATE_FILE = Path(__file__).parent / "state.json"
STATE_LUNA_FILE = Path(__file__).parent / "state_luna.json"


def load_json_file(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ Error reading {path.name}: {e}", file=sys.stderr)
    return []


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    print("🚀 Starting Data Migration to Neon PostgreSQL Database...\n")

    if not db.is_db_enabled():
        print("❌ DATABASE_URL is not set or psycopg2 is missing.", file=sys.stderr)
        print("Please configure DATABASE_URL in your .env file or environment.", file=sys.stderr)
        return 1


    db.init_db()

    # 1. Migrate Epic Games (state.json)
    epic_games = load_json_file(STATE_FILE)
    print(f"📦 Found {len(epic_games)} Epic games in {STATE_FILE.name}")
    if epic_games:
        db.save_games("epic", epic_games)
        print(f"✅ Successfully migrated {len(epic_games)} Epic games to Neon DB.")

    # 2. Migrate Luna Games (state_luna.json)
    luna_games = load_json_file(STATE_LUNA_FILE)
    print(f"📦 Found {len(luna_games)} Amazon Luna games in {STATE_LUNA_FILE.name}")
    if luna_games:
        db.save_games("luna", luna_games)
        print(f"✅ Successfully migrated {len(luna_games)} Amazon Luna games to Neon DB.")

    # 3. Verification query
    epic_ids = db.load_known_game_ids("epic")
    luna_ids = db.load_known_game_ids("luna")
    print("\n📊 Database Status Summary:")
    print(f"  • Total Epic Games in DB: {len(epic_ids)}")
    print(f"  • Total Luna Games in DB: {len(luna_ids)}")
    print("\n🎉 Migration completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
