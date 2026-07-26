#!/usr/bin/env python3
"""Check Epic Games Store free games and notify via Telegram if new ones appear."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=en-US&country=IN&allowCountry=IN"
)

STATE_FILE = Path(__file__).parent / "state.json"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def load_state() -> list[dict]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return []


def save_state(state: list[dict]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def get_free_games() -> list[dict]:
    resp = requests.get(API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    elements = data["data"]["Catalog"]["searchStore"]["elements"]
    now = datetime.now(timezone.utc)

    free = []
    for el in elements:
        price = el.get("price", {})
        total = price.get("totalPrice", {})
        if total.get("discountPrice") != 0:
            continue

        promotions = el.get("promotions", {}) or {}

        # Check active free promotions (100% off)
        active = promotions.get("promotionalOffers", [])
        is_active_free = False
        expiry = None
        for offer_group in active:
            for offer in offer_group.get("promotionalOffers", []):
                if offer.get("discountSetting", {}).get("discountPercentage") != 0:
                    continue
                end = offer.get("endDate")
                if end:
                    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    if end_dt > now:
                        is_active_free = True
                        expiry = end
        if is_active_free:
            free.append({
                "id": el["id"],
                "title": el["title"],
                "description": el.get("description", ""),
                "original_price": total.get("fmtPrice", {}).get("originalPrice", ""),
                "currency": total.get("currencyCode", "INR"),
                "product_slug": el.get("productSlug", ""),
                "url_slug": el.get("urlSlug", ""),
                "expiry": expiry,
                "key_images": el.get("keyImages", []),
            })

    return free


def send_telegram(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    # Build message
    title = game["title"]
    desc = game["description"][:200] + ("…" if len(game["description"]) > 200 else "")
    original = game["original_price"]
    expiry_dt = datetime.fromisoformat(game["expiry"].replace("Z", "+00:00"))
    expires = expiry_dt.strftime("%B %d, %Y at %I:%M %p UTC")

    # Build claim URL
    slug = game.get("product_slug") or game.get("url_slug", "")
    if slug:
        # Some slugs already have /home suffix, some don't
        claim_url = f"https://store.epicgames.com/en-US/p/{slug}"
    else:
        # Fallback: search page
        claim_url = "https://store.epicgames.com/en-US/free-games"

    # Find best image
    images = game.get("key_images", [])
    thumbnail = ""
    for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
        for img in images:
            if img.get("type") == img_type:
                thumbnail = img["url"]
                break
        if thumbnail:
            break

    message = (
        f"🎮 <b>New Free Game on Epic Games!</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"💰 Original Price: {original}\n"
        f"⏳ Free until: {expires}\n\n"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if thumbnail:
        payload["photo"] = thumbnail
        # Use sendPhoto for better rendering
        photo_resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": thumbnail,
                "caption": message,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if photo_resp.ok:
            return

    # Fallback to text-only
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()


def main() -> int:
    print(f"[{datetime.now().isoformat()}] Checking Epic Games free games…")

    known = load_state()
    known_ids = {g["id"] for g in known}
    print(f"  Known games: {[g['title'] for g in known]}")

    current = get_free_games()
    current_ids = {g["id"] for g in current}
    print(f"  Current free games: {[g['title'] for g in current]}")

    new_ids = current_ids - known_ids
    if not new_ids:
        print("  No new free games found.")
        # Still update state in case titles/prices changed
        save_state(current)
        return 0

    print(f"  New free games detected: {new_ids}")
    for game in current:
        if game["id"] in new_ids:
            print(f"  Notifying about: {game['title']}")
            try:
                send_telegram(game)
                print(f"  ✅ Notification sent for {game['title']}")
            except Exception as e:
                print(f"  ❌ Failed to send notification: {e}", file=sys.stderr)

    save_state(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
