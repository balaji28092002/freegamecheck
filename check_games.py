#!/usr/bin/env python3
"""Check Epic Games Store and Amazon Luna free games, notify via Telegram."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import re
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db


EPIC_API_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=en-US&country=IN&allowCountry=IN"
)

LUNA_PAGE_URL = "https://luna.amazon.com/claims/home"
LUNA_GRAPHQL_URL = "https://luna.amazon.com/graphql"
LUNA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
LUNA_GRAPHQL_QUERY = """
query OffersContext_Offers_And_Items($dateOverride: Time, $pageSize: Int) {
  games: items(collectionType: FREE_GAMES, dateOverride: $dateOverride, pageSize: $pageSize) {
    items {
      id
      isFGWP
      category
      assets {
        id
        title
        externalClaimLink
        shortformDescription
        cardMedia {
          defaultMedia {
            src1x
            src2x
            type
            __typename
          }
          __typename
        }
        __typename
      }
      offers {
        id
        startTime
        endTime
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")



# ---------------------------------------------------------------------------
# Epic Games Store
# ---------------------------------------------------------------------------

def get_epic_free_games() -> list[dict] | None:
    try:
        resp = requests.get(EPIC_API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ Failed to fetch Epic API: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Invalid JSON from Epic API: {e}", file=sys.stderr)
        return None

    try:
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError) as e:
        print(f"  ⚠️ Unexpected Epic API response structure: {e}", file=sys.stderr)
        return None

    now = datetime.now(timezone.utc)
    free = []
    for el in elements:
        try:
            price = el.get("price", {}) or {}
            total = price.get("totalPrice", {}) or {}
            if total.get("discountPrice") != 0:
                continue

            promotions = el.get("promotions", {}) or {}
            active = promotions.get("promotionalOffers", []) or []
            is_active_free = False
            expiry = None
            for offer_group in active:
                for offer in offer_group.get("promotionalOffers", []) or []:
                    discount = offer.get("discountSetting", {}) or {}
                    if discount.get("discountPercentage") != 0:
                        continue
                    end = offer.get("endDate")
                    if end:
                        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                        if end_dt > now:
                            is_active_free = True
                            expiry = end

            if is_active_free:
                free.append({
                    "id": el.get("id"),
                    "title": el.get("title", "Unknown Game"),
                    "description": el.get("description", ""),
                    "original_price": total.get("fmtPrice", {}).get("originalPrice", ""),
                    "currency": total.get("currencyCode", "INR"),
                    "product_slug": el.get("productSlug", ""),
                    "url_slug": el.get("urlSlug", ""),
                    "expiry": expiry,
                    "key_images": el.get("keyImages", []),
                })
        except Exception as e:
            print(f"  ⚠️ Skipping malformed Epic game entry: {e}", file=sys.stderr)
            continue

    return free


# ---------------------------------------------------------------------------
# Amazon Luna
# ---------------------------------------------------------------------------

def _get_luna_csrf_token(session: requests.Session) -> str:
    resp = session.get(LUNA_PAGE_URL, timeout=30)
    resp.raise_for_status()
    match = re.search(r'name=["\']csrf-key["\']\s+value=["\']([^"\']+)', resp.text)
    if not match:
        raise ValueError("Could not find CSRF token on Luna page")
    return match.group(1)


def get_luna_free_games() -> list[dict] | None:
    session = requests.Session()
    session.headers.update({
        "User-Agent": LUNA_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        csrf_token = _get_luna_csrf_token(session)
    except Exception as e:
        print(f"  ⚠️ Failed to get Luna CSRF token: {e}", file=sys.stderr)
        return None

    try:
        resp = session.post(
            LUNA_GRAPHQL_URL,
            json={
                "operationName": "OffersContext_Offers_And_Items",
                "variables": {"pageSize": 999},
                "extensions": {},
                "query": LUNA_GRAPHQL_QUERY,
            },
            headers={
                "Content-Type": "application/json",
                "csrf-token": csrf_token,
                "client-id": "CarboniteApp",
                "Referer": LUNA_PAGE_URL,
                "Origin": "https://luna.amazon.com",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ Failed to fetch Luna GraphQL: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Invalid JSON from Luna GraphQL: {e}", file=sys.stderr)
        return None

    try:
        items = data["data"]["games"]["items"]
    except (KeyError, TypeError) as e:
        print(f"  ⚠️ Unexpected Luna GraphQL response structure: {e}", file=sys.stderr)
        return None

    free = []
    for item in items:
        try:
            if not item.get("isFGWP"):
                continue

            assets = item.get("assets", {}) or {}
            title = assets.get("title", "")
            if not title:
                continue

            card_media = assets.get("cardMedia", {}) or {}
            default_media = card_media.get("defaultMedia", {}) or {}
            thumbnail = default_media.get("src1x", "")

            offers = item.get("offers", []) or []
            expiry = offers[0].get("endTime") if offers else None

            free.append({
                "id": f"luna_{item.get('id', '')}",
                "title": title,
                "description": assets.get("shortformDescription", ""),
                "claim_url": assets.get("externalClaimLink", ""),
                "thumbnail": thumbnail,
                "expiry": expiry,
            })
        except Exception as e:
            print(f"  ⚠️ Skipping malformed Luna game entry: {e}", file=sys.stderr)
            continue

    return free


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

def _send_photo(thumbnail: str, message: str) -> bool:
    if not thumbnail:
        return False
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": thumbnail,
            "caption": message,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    return resp.ok


def _send_text(message: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_telegram_epic(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game["description"][:200] + ("…" if len(game["description"]) > 200 else "")
    original = game["original_price"]
    expiry_dt = datetime.fromisoformat(game["expiry"].replace("Z", "+00:00"))
    expires = expiry_dt.strftime("%B %d, %Y at %I:%M %p UTC")

    slug = game.get("product_slug") or game.get("url_slug", "")
    if slug:
        claim_url = f"https://store.epicgames.com/en-US/p/{slug}"
    else:
        claim_url = "https://store.epicgames.com/en-US/free-games"

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

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_luna(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game.get("description", "")
    claim_url = game.get("claim_url", "")
    thumbnail = game.get("thumbnail", "")
    expiry = game.get("expiry", "")

    expires_str = ""
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            expires_str = f"⏳ Free until: {expiry_dt.strftime('%B %d, %Y')}\n\n"
        except (ValueError, TypeError):
            pass

    message = (
        f"🎮 <b>New Free Game on Amazon Luna!</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"🆓 Free with Prime\n"
        f"{expires_str}"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _send_alert(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        _send_text(f"⚠️ <b>Bot Alert</b>\n\n{message}")
    except Exception:
        pass


def check_source(name: str, fetch_fn, send_fn, platform: str) -> int:
    print(f"\n[{datetime.now().isoformat()}] Checking {name} free games…")

    known_ids = db.load_known_game_ids(platform)
    print(f"  [DB] Known {name} games count: {len(known_ids)}")

    try:
        current = fetch_fn()
    except Exception as e:
        print(f"  ❌ Failed to fetch {name}: {e}", file=sys.stderr)
        _send_alert(f"Failed to fetch {name} free games:\n<code>{e}</code>")
        return 0

    if current is None:
        print(f"  ❌ {name} fetch returned no data (possible API change)", file=sys.stderr)
        _send_alert(
            f"{name} fetch returned no data.\n"
            "The API endpoint may have changed or is unreachable."
        )
        return 0

    current_ids = {g["id"] for g in current}
    print(f"  Current free games: {[g['title'] for g in current]}")

    new_ids = current_ids - known_ids
    notified = 0
    if new_ids:
        print(f"  New free games detected: {new_ids}")
        for game in current:
            if game["id"] in new_ids:
                print(f"  Notifying about: {game['title']}")
                try:
                    send_fn(game)
                    print(f"  ✅ Notification sent for {game['title']}")
                    notified += 1
                except Exception as e:
                    print(f"  ❌ Failed to send notification: {e}", file=sys.stderr)
    else:
        print("  No new free games found.")

    db.save_games(platform, current)
    print(f"  [DB] Saved active {name} games to database.")

    return notified


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    if not db.is_db_enabled():
        err_msg = "NeonDB is not configured or DATABASE_URL environment variable is missing."
        print(f"❌ {err_msg}", file=sys.stderr)
        _send_alert(f"<b>NeonDB Not Configured</b>\n\n{err_msg}\nPlease set <code>DATABASE_URL</code> in environment / GitHub Secrets.")
        return 1

    total = 0
    total += check_source("Epic Games", get_epic_free_games, send_telegram_epic, "epic")
    total += check_source("Amazon Luna", get_luna_free_games, send_telegram_luna, "luna")
    return 0




if __name__ == "__main__":
    sys.exit(main())
