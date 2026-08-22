#!/usr/bin/env python3
"""Check Epic Games Store, Amazon Luna, itch.io, STOVE Store, Steam, and Xbox PC free games & deals, notify via Telegram."""

import argparse
import html
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import re
import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_STEAM_DEAL_PAGES = 70          # Steam deals crawl depth (100 games per page)
STEAM_PAGE_PACE_SECONDS = 1.1      # Steady pace between Steam deal pages (429 avoidance)
STEAM_MAX_RETRIES = 4              # Retries per Steam page (429 backoff: attempt * 25s)
STEAM_REQUEST_TIMEOUT = 25
DEFAULT_REQUEST_TIMEOUT = 30
EPIC_CATALOG_PAGE_SIZE = 100       # Epic GraphQL on-sale catalog page size
EPIC_CATALOG_TIMEOUT = 30
XBOX_CATALOG_CHUNK_SIZE = 30       # Xbox DisplayCatalog bigIds per request
XBOX_CATALOG_CONCURRENCY = 4       # Parallel Xbox DisplayCatalog chunk fetches
XBOX_SALES_TIMEOUT = 30
ITCH_MAX_PAGES = 5
STOVE_MAX_PAGES = 5
DISCOUNT_DEAL_MIN_PCT = 90         # Deals are 90%..99% off (100% handled as free games)
DISCOUNT_DEAL_MAX_PCT = 99
TELEGRAM_SEND_TIMEOUT = 15
TELEGRAM_MAX_RETRIES = 3           # Retries on Telegram 429 (backoff: attempt * 5s)
DESC_MAX_LEN = 200

DRY_RUN = False                    # Set by --dry-run; suppresses all Telegram sends


EPIC_API_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=en-US&country=IN&allowCountry=IN"
)
EPIC_GRAPHQL_URL = "https://store.epicgames.com/graphql"

LUNA_PAGE_URL = "https://luna.amazon.com/claims/home"
LUNA_GRAPHQL_URL = "https://luna.amazon.com/graphql"
LUNA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
ITCH_ON_SALE_URL = "https://itch.io/games/on-sale?format=json"
ITCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
STOVE_API_URL = "https://api.onstove.com/store/v1.0/products/search"
STOVE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
STEAM_SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=50&specials=1&maxprice=free&cc=in&l=english&infinite=1"
)
STEAM_DEALS_SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count=100&specials=1&category1=998&cc=in&l=english&infinite=1"
)
STEAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
LENOVO_KEY_DROPS_URL = "https://gaming.lenovo.com/game-key-drops"
XBOX_SALES_URL = "https://www.xbox.com/en-in/promotions/sales/sales-and-specials?xr=shellnav"
XBOX_CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
XBOX_USER_AGENT = (
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

EPIC_CATALOG_ON_SALE_QUERY = """
query searchStoreQuery($allowCountries: String, $category: String, $count: Int, $country: String!, $locale: String, $sortBy: String, $sortDir: String, $start: Int, $onSale: Boolean) {
  Catalog {
    searchStore(allowCountries: $allowCountries, category: $category, count: $count, country: $country, locale: $locale, sortBy: $sortBy, sortDir: $sortDir, start: $start, onSale: $onSale) {
      paging {
        total
        count
      }
      elements {
        id
        title
        productSlug
        urlSlug
        offerType
        description
        keyImages {
          type
          url
        }
        catalogNs {
          mappings(pageType: "productHome") {
            pageSlug
            pageType
          }
        }
        price(country: $country) {
          totalPrice {
            originalPrice
            discountPrice
            fmtPrice(locale: $locale) {
              originalPrice
              discountPrice
            }
          }
        }
        promotions {
          promotionalOffers {
            promotionalOffers {
              startDate
              endDate
              discountSetting {
                discountType
                discountPercentage
              }
            }
          }
        }
      }
    }
  }
}
"""

_epic_cache: dict[str, Any] = {"fetched": False, "free_games": [], "discount_deals": []}
_EPIC_FETCH_LOCK = threading.Lock()


def _extract_epic_slug(el: dict) -> str:
    mappings = (el.get("catalogNs") or {}).get("mappings") or []
    for m in mappings:
        if m.get("pageSlug") and m.get("pageType") == "productHome":
            return m["pageSlug"]
    for m in mappings:
        if m.get("pageSlug"):
            return m["pageSlug"]

    pslug = el.get("productSlug")
    if pslug and not re.match(r"^[a-f0-9]{32}$", pslug):
        return pslug

    uslug = el.get("urlSlug")
    if uslug and not re.match(r"^[a-f0-9]{32}$", uslug):
        return uslug

    return pslug or uslug or ""


def _fetch_epic_data() -> bool:
    with _EPIC_FETCH_LOCK:
        return _fetch_epic_data_unlocked()

def _fetch_epic_data_unlocked() -> bool:
    if _epic_cache["fetched"]:
        return True

    now = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y_%m")
    free_games = []
    discount_deals = []
    seen_free = set()
    seen_deals = set()

    # 1. Fetch official curated weekly free games from Epic promotions API
    try:
        resp = requests.get(EPIC_API_URL, timeout=DEFAULT_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
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
                    game_id = el.get("id")
                    if game_id and game_id not in seen_free:
                        seen_free.add(game_id)
                        slug = _extract_epic_slug(el)
                        claim_url = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"

                        images = el.get("keyImages", []) or []
                        thumbnail = ""
                        for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
                            for img in images:
                                if img.get("type") == img_type:
                                    thumbnail = img.get("url", "")
                                    break
                            if thumbnail:
                                break

                        free_games.append({
                            "id": game_id,
                            "title": el.get("title", "Unknown Game"),
                            "description": el.get("description", ""),
                            "original_price": total.get("fmtPrice", {}).get("originalPrice", ""),
                            "currency": total.get("currencyCode", "INR"),
                            "claim_url": claim_url,
                            "thumbnail": thumbnail,
                            "product_slug": slug,
                            "url_slug": el.get("urlSlug", ""),
                            "expiry": expiry,
                            "key_images": images,
                        })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed Epic promotional entry: {e}", file=sys.stderr)
                continue
    except Exception as e:
        print(f"  ⚠️ Failed to fetch Epic promotions API: {e}", file=sys.stderr)

    # 2. Fetch full storewide on-sale catalog via GraphQL (handles 100% off sales & 90%+ discount deals)
    session = requests.Session()
    session.headers.update({
        "User-Agent": STEAM_USER_AGENT,
        "Content-Type": "application/json",
    })
    start = 0
    while True:
        variables = {
            "allowCountries": "IN",
            "category": "games/edition/base",
            "count": EPIC_CATALOG_PAGE_SIZE,
            "country": "IN",
            "locale": "en-US",
            "sortBy": "releaseDate",
            "sortDir": "DESC",
            "start": start,
            "onSale": True
        }

        try:
            resp = session.post(
                EPIC_GRAPHQL_URL,
                json={"query": EPIC_CATALOG_ON_SALE_QUERY, "variables": variables},
                timeout=EPIC_CATALOG_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ⚠️ Failed to fetch Epic GraphQL catalog (start {start}): {e}", file=sys.stderr)
            break
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Invalid JSON from Epic GraphQL catalog (start {start}): {e}", file=sys.stderr)
            break

        try:
            store_data = data["data"]["Catalog"]["searchStore"]
            elements = store_data.get("elements", [])
            total_store = store_data.get("paging", {}).get("total", 0)
        except (KeyError, TypeError) as e:
            print(f"  ⚠️ Unexpected Epic GraphQL response structure: {e}", file=sys.stderr)
            break

        if not elements:
            break

        for el in elements:
            try:
                game_id = el.get("id")
                if not game_id:
                    continue

                price = el.get("price", {}) or {}
                total_price = price.get("totalPrice", {}) or {}
                orig = total_price.get("originalPrice", 0)
                disc = total_price.get("discountPrice", 0)

                if orig <= 0:
                    continue

                slug = _extract_epic_slug(el)
                claim_url = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/"

                images = el.get("keyImages", []) or []
                thumbnail = ""
                for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
                    for img in images:
                        if img.get("type") == img_type:
                            thumbnail = img.get("url", "")
                            break
                    if thumbnail:
                        break

                fmt = total_price.get("fmtPrice", {}) or {}
                orig_fmt = fmt.get("originalPrice", f"₹{orig/100:.2f}")
                disc_fmt = fmt.get("discountPrice", f"₹{disc/100:.2f}")

                promotions = el.get("promotions", {}) or {}
                active = promotions.get("promotionalOffers", []) or []
                expiry = None
                for offer_group in active:
                    for offer in offer_group.get("promotionalOffers", []) or []:
                        end = offer.get("endDate")
                        if end:
                            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                            if end_dt > now:
                                expiry = end
                                break
                    if expiry:
                        break

                # 100% OFF Storewide Promotion (Free Game)
                if disc == 0:
                    if game_id not in seen_free:
                        seen_free.add(game_id)
                        free_games.append({
                            "id": game_id,
                            "title": el.get("title", "Unknown Game"),
                            "description": el.get("description", ""),
                            "original_price": orig_fmt,
                            "currency": "INR",
                            "claim_url": claim_url,
                            "thumbnail": thumbnail,
                            "product_slug": slug,
                            "url_slug": el.get("urlSlug", ""),
                            "expiry": expiry,
                            "key_images": images,
                        })
                # 90%+ Discount Deal
                elif disc < orig:
                    pct = int(round((1 - disc / orig) * 100))
                    if DISCOUNT_DEAL_MIN_PCT <= pct <= DISCOUNT_DEAL_MAX_PCT:
                        if game_id not in seen_deals:
                            seen_deals.add(game_id)
                            discount_deals.append({
                                "id": f"epic_deal_{month_prefix}_{game_id}",
                                "title": el.get("title", "Unknown Game"),
                                "description": el.get("description", ""),
                                "claim_url": claim_url,
                                "thumbnail": thumbnail,
                                "original_price": orig_fmt,
                                "discounted_price": disc_fmt,
                                "discount_pct": f"{pct}%",
                                "currency": "INR",
                                "expiry": expiry,
                            })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed Epic on-sale catalog entry: {e}", file=sys.stderr)
                continue

        start += len(elements)
        if start >= total_store:
            break

    _epic_cache["fetched"] = True
    _epic_cache["free_games"] = free_games
    _epic_cache["discount_deals"] = discount_deals
    return True


def get_epic_free_games() -> list[dict] | None:
    if not _fetch_epic_data():
        return None
    return _epic_cache["free_games"]


def get_epic_discount_deals() -> list[dict] | None:
    if not _fetch_epic_data():
        return None
    return _epic_cache["discount_deals"]


# ---------------------------------------------------------------------------
# Amazon Luna
# ---------------------------------------------------------------------------

def _get_luna_csrf_token(session: requests.Session) -> str:
    resp = session.get(LUNA_PAGE_URL, timeout=DEFAULT_REQUEST_TIMEOUT)
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
            timeout=DEFAULT_REQUEST_TIMEOUT,
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
# itch.io
# ---------------------------------------------------------------------------

def get_itch_free_games(max_pages: int = ITCH_MAX_PAGES) -> list[dict] | None:
    headers = {"User-Agent": ITCH_USER_AGENT}
    free_games = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"{ITCH_ON_SALE_URL}&page={page}"
        try:
            resp = requests.get(url, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ⚠️ Failed to fetch itch.io API (page {page}): {e}", file=sys.stderr)
            break
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Invalid JSON from itch.io API (page {page}): {e}", file=sys.stderr)
            break

        content = data.get("content", "")
        if not content:
            break

        soup = BeautifulSoup(content, "html.parser")
        cells = soup.find_all("div", class_="game_cell")
        if not cells:
            break

        for cell in cells:
            try:
                sale_tag_elem = cell.find(class_="sale_tag")
                if not sale_tag_elem:
                    continue

                sale_text = sale_tag_elem.text.strip()
                if "-100%" not in sale_text and "100%" not in sale_text:
                    continue

                game_id = cell.get("data-game_id")
                if not game_id or game_id in seen_ids:
                    continue
                seen_ids.add(game_id)

                title_elem = cell.find("a", class_="title")
                title = title_elem.text.strip() if title_elem else "Unknown Game"
                claim_url = title_elem["href"] if title_elem and "href" in title_elem.attrs else ""
                if claim_url and not claim_url.startswith("http"):
                    claim_url = f"https://itch.io{claim_url}"

                desc_elem = cell.find("div", class_="game_text")
                description = desc_elem.text.strip() if desc_elem else ""

                author_elem = cell.find("div", class_="game_author")
                author = author_elem.text.strip() if author_elem else ""

                img_elem = cell.find("img")
                thumbnail = ""
                if img_elem:
                    thumbnail = img_elem.get("data-lazy_src") or img_elem.get("src") or ""

                price_tag = cell.find("a", class_="price_tag")
                original_price = ""
                if price_tag and "title" in price_tag.attrs:
                    original_price = price_tag["title"]

                free_games.append({
                    "id": f"itch_{game_id}",
                    "title": title,
                    "description": description,
                    "claim_url": claim_url,
                    "thumbnail": thumbnail,
                    "author": author,
                    "original_price": original_price or "100% Off",
                    "expiry": None,
                })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed itch.io game entry: {e}", file=sys.stderr)
                continue

    return free_games


# ---------------------------------------------------------------------------
# STOVE Store
# ---------------------------------------------------------------------------

def get_stove_free_games(max_pages: int = STOVE_MAX_PAGES) -> list[dict] | None:
    free_games = []
    seen_ids = set()

    headers = {
        "User-Agent": STOVE_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "caller-id": "indie-web-store",
        "caller-detail": f"SSR_{int(time.time()*1000)}",
        "x-client-lang": "en",
        "x-lang": "en",
        "x-device-type": "P01",
        "x-nation": "US",
        "x-timezone": "Etc/UTC",
        "x-utc-offset": "0",
        "Referer": "https://store.onstove.com/",
    }

    for page in range(1, max_pages + 1):
        timestamp = int(time.time() * 1000)
        params = {
            "currency_code": "USD",
            "page": page,
            "size": 36,
            "direction": "DISCOUNT",
            "timestemp": timestamp,
        }

        try:
            resp = requests.get(STOVE_API_URL, headers=headers, params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ⚠️ Failed to fetch STOVE API (page {page}): {e}", file=sys.stderr)
            break
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Invalid JSON from STOVE API (page {page}): {e}", file=sys.stderr)
            break

        val = data.get("value", {}) if isinstance(data, dict) else {}
        contents = val.get("contents", []) if isinstance(val, dict) else []
        if not contents:
            break

        for item in contents:
            try:
                amount = item.get("amount", {}) or {}
                discount_rate = amount.get("discount_rate", 0)
                original_price = amount.get("original_price", 0)
                sales_price = amount.get("sales_price", None)
                paid = item.get("paid", False)

                is_100_percent_off = (discount_rate == 100) or (paid and sales_price == 0 and original_price > 0)
                if not is_100_percent_off:
                    continue

                product_no = item.get("product_no") or item.get("game_id")
                if not product_no or str(product_no) in seen_ids:
                    continue
                seen_ids.add(str(product_no))

                title = item.get("product_name", "Unknown Game")
                description = item.get("short_piece", "")
                claim_url = f"https://store.onstove.com/en/games/{product_no}"
                thumbnail = item.get("title_image_rectangle") or item.get("title_image_square") or ""

                discount_period = amount.get("discount_period", {}) or {}
                end_ms = discount_period.get("end")
                expiry_str = None
                if end_ms and end_ms < 30000000000000:
                    try:
                        expiry_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
                        expiry_str = expiry_dt.isoformat()
                    except (ValueError, OSError, OverflowError):
                        expiry_str = None

                orig_price_str = f"${original_price:.2f}" if original_price else "100% Off"

                free_games.append({
                    "id": f"stove_{product_no}",
                    "title": title,
                    "description": description,
                    "claim_url": claim_url,
                    "thumbnail": thumbnail,
                    "original_price": orig_price_str,
                    "expiry": expiry_str,
                })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed STOVE game entry: {e}", file=sys.stderr)
                continue

    return free_games


# ---------------------------------------------------------------------------
# Steam Store
# ---------------------------------------------------------------------------

def get_steam_free_games() -> list[dict] | None:
    headers = {
        "User-Agent": STEAM_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        resp = requests.get(STEAM_SEARCH_URL, headers=headers, timeout=DEFAULT_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ Failed to fetch Steam Search API: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Invalid JSON from Steam Search API: {e}", file=sys.stderr)
        return None

    content = data.get("results_html", "")
    if not content:
        return []

    soup = BeautifulSoup(content, "html.parser")
    rows = soup.find_all("a", class_="search_result_row")
    free_games = []
    seen_ids = set()

    for r in rows:
        try:
            ds_appid = r.get("data-ds-appid", "")
            if not ds_appid:
                continue
            appid = ds_appid.split(",")[0].strip()
            if not appid or appid in seen_ids:
                continue
            seen_ids.add(appid)

            title_elem = r.find("span", class_="title")
            title = title_elem.text.strip() if title_elem else "Unknown Game"

            price_elem = r.find("div", class_="discount_prices")
            strike_elem = price_elem.find("div", class_="discount_original_price") if price_elem else None
            original_price = strike_elem.text.strip() if strike_elem else "100% Off"

            thumbnail = f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
            claim_url = f"https://store.steampowered.com/app/{appid}/"

            free_games.append({
                "id": f"steam_{appid}",
                "title": title,
                "description": "",
                "claim_url": claim_url,
                "thumbnail": thumbnail,
                "original_price": original_price,
                "expiry": None,
            })
        except Exception as e:
            print(f"  ⚠️ Skipping malformed Steam game entry: {e}", file=sys.stderr)
            continue

    return free_games


def get_steam_discount_deals(max_pages: int = MAX_STEAM_DEAL_PAGES) -> list[dict] | None:
    session = requests.Session()
    session.headers.update({
        "User-Agent": STEAM_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    session.cookies.set("birthtime", "283993201", domain="store.steampowered.com")
    session.cookies.set("mature_content", "1", domain="store.steampowered.com")
    session.cookies.set("wants_mature_content", "1", domain="store.steampowered.com")

    deals = []
    seen_ids = set()
    total_store_games = None
    page = 0

    while page < max_pages:
        start = page * 100
        if total_store_games is not None and start >= total_store_games:
            break

        url = STEAM_DEALS_SEARCH_URL.format(start=start)
        success = False
        end_of_results = False
        last_error = None

        for attempt in range(1, STEAM_MAX_RETRIES + 1):
            try:
                resp = session.get(url, timeout=STEAM_REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    wait_sec = attempt * 25.0
                    print(
                        f"  ⚠️ Steam rate limit (429) on page {page} (attempt {attempt}/{STEAM_MAX_RETRIES}). Backing off {wait_sec}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait_sec)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if total_store_games is None:
                    total_store_games = data.get("total_count", 0)

                content = data.get("results_html", "")
                if not content or not content.strip():
                    end_of_results = True
                    success = True
                    break

                soup = BeautifulSoup(content, "html.parser")
                rows = soup.find_all("a", class_="search_result_row")
                if not rows:
                    end_of_results = True
                    success = True
                    break

                for r in rows:
                    try:
                        ds_appid = r.get("data-ds-appid", "")
                        if not ds_appid:
                            continue
                        appid = ds_appid.split(",")[0].strip()
                        if not appid or appid in seen_ids:
                            continue

                        pct_elem = r.find("div", class_="discount_pct")
                        if not pct_elem:
                            continue
                        pct_str = pct_elem.text.strip().replace("-", "").replace("%", "")
                        try:
                            pct = int(pct_str)
                        except ValueError:
                            continue

                        # Strictly DISCOUNT_DEAL_MIN_PCT..MAX_PCT% off (excluding 100% free games to avoid duplicate notification)
                        if not (DISCOUNT_DEAL_MIN_PCT <= pct <= DISCOUNT_DEAL_MAX_PCT):
                            continue

                        seen_ids.add(appid)
                        title_elem = r.find("span", class_="title")
                        title = title_elem.text.strip() if title_elem else "Unknown Game"

                        price_elem = r.find("div", class_="discount_prices")
                        strike_elem = price_elem.find("div", class_="discount_original_price") if price_elem else None
                        final_elem = price_elem.find("div", class_="discount_final_price") if price_elem else None

                        original_price = strike_elem.text.strip() if strike_elem else ""
                        discounted_price = final_elem.text.strip() if final_elem else ""

                        thumbnail = f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
                        claim_url = f"https://store.steampowered.com/app/{appid}/"

                        month_prefix = datetime.now(timezone.utc).strftime("%Y_%m")
                        deals.append({
                            "id": f"steam_deal_{month_prefix}_{appid}",
                            "title": title,
                            "description": "",
                            "claim_url": claim_url,
                            "thumbnail": thumbnail,
                            "original_price": original_price,
                            "discounted_price": discounted_price,
                            "discount_pct": f"{pct}%",
                            "expiry": None,
                        })
                    except Exception as e:
                        print(f"  ⚠️ Skipping malformed Steam deal entry: {e}", file=sys.stderr)
                        continue

                success = True
                break
            except Exception as e:
                last_error = e
                wait_sec = attempt * 20.0
                print(
                    f"  ⚠️ Error fetching Steam Deals page {page} (attempt {attempt}/{STEAM_MAX_RETRIES}): {e}. Retrying after {wait_sec}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_sec)

        if end_of_results:
            break

        if not success:
            err_msg = f"Steam Deals fetch encountered persistent error on page {page} after {STEAM_MAX_RETRIES} retries: {last_error}"
            print(f"  ❌ {err_msg}", file=sys.stderr)
            _send_alert(f"⚠️ <b>Steam Deals Alert</b>\n\n{err_msg}\nProceeding with {len(deals)} deals found so far.")
            break

        page += 1
        # Steady pace between pages to avoid hitting Steam's 429 rate limit
        time.sleep(STEAM_PAGE_PACE_SECONDS)

    return deals


# ---------------------------------------------------------------------------
# Lenovo Legion Key Drops
# ---------------------------------------------------------------------------

def get_lenovo_key_drops() -> list[dict] | None:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("  ⚠️ curl_cffi package not installed, skipping Lenovo key drops", file=sys.stderr)
        return []

    try:
        resp = cffi_requests.get(LENOVO_KEY_DROPS_URL, impersonate="chrome120", timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ Failed to fetch Lenovo Key Drops page: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    drops = []
    seen_ids = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/post/" in href or "key-drop" in href:
            title = a.text.strip()
            if not title or title.lower() in ("learn more", "claim now", "coming soon", "view details"):
                slug = href.split("/post/")[-1] if "/post/" in href else href.strip("/").split("/")[-1]
                clean_slug = re.sub(r"-[a-zA-Z0-9]{10,}$", "", slug)
                title = clean_slug.replace("-", " ").title()

            drop_id_str = href.strip("/").split("/")[-1]
            if not drop_id_str or drop_id_str in seen_ids:
                continue
            seen_ids.add(drop_id_str)

            claim_url = href if href.startswith("http") else f"https://gaming.lenovo.com{href}"

            parent = a.find_parent(["div", "article", "section", "li"])
            img = parent.find("img") if parent else None
            thumbnail = ""
            if img:
                thumbnail = img.get("src") or img.get("data-src") or ""

            drops.append({
                "id": f"lenovo_drop_{drop_id_str}",
                "title": title,
                "description": "Exclusive free Game Key Drop on Lenovo Legion Community!",
                "claim_url": claim_url,
                "thumbnail": thumbnail,
                "expiry": None,
            })

    return drops


# ---------------------------------------------------------------------------
# Xbox Store (PC Games & Deals)
# ---------------------------------------------------------------------------

_xbox_cache: dict[str, Any] = {"fetched": False, "free_games": [], "discount_deals": []}
_XBOX_FETCH_LOCK = threading.Lock()


def _is_xbox_pc_compatible(prod: dict, sku: dict, avail: dict) -> bool:
    props = prod.get("Properties") or {}
    if props.get("XboxXPA") is True:
        return True

    attrs = props.get("Attributes") or []
    for a in attrs:
        app_plat = a.get("ApplicablePlatforms") or []
        if any(p in ("Desktop", "PC", "Windows.Desktop") for p in app_plat):
            return True

    sku_props = sku.get("Properties") or {}
    packages = sku_props.get("Packages") or []
    for pkg in packages:
        deps = pkg.get("PlatformDependencies") or []
        for d in deps:
            pname = d.get("PlatformName", "")
            if any(p in pname for p in ("Desktop", "Universal", "PC")):
                return True

    cond = avail.get("Conditions") or {}
    client_cond = cond.get("ClientConditions") or {}
    platforms = client_cond.get("AllowedPlatforms") or []
    for pl in platforms:
        if isinstance(pl, dict):
            pname = pl.get("PlatformName", "")
            if any(p in pname for p in ("Desktop", "Universal", "PC")):
                return True
        elif isinstance(pl, str) and any(p in pl for p in ("Desktop", "Universal", "PC")):
            return True

    return False


def _fetch_xbox_data() -> bool:
    with _XBOX_FETCH_LOCK:
        return _fetch_xbox_data_unlocked()

def _fetch_xbox_data_unlocked() -> bool:
    if _xbox_cache["fetched"]:
        return True

    session = requests.Session()
    session.headers.update({"User-Agent": XBOX_USER_AGENT})
    try:
        resp = session.get(XBOX_SALES_URL, timeout=XBOX_SALES_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠️ Failed to fetch Xbox sales page: {e}", file=sys.stderr)
        return False

    pids = set(m.upper() for m in re.findall(r'/games/store/[a-zA-Z0-9-]+/([a-zA-Z0-9]{12})', resp.text, re.IGNORECASE))

    idx = resp.text.find("window.__PRELOADED_STATE__ = ")
    if idx != -1:
        try:
            start = idx + len("window.__PRELOADED_STATE__ = ")
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(resp.text[start:])
            content = data.get("content", {}) or {}
            channels = content.get("channels", {}).get("channelData", {}) or {}
            for ck, cv in channels.items():
                prods = cv.get("data", {}).get("products", []) or []
                for p in prods:
                    pid = p.get("productId")
                    if pid:
                        pids.add(pid.upper())
        except Exception as e:
            print(f"  ⚠️ Warning: Failed to parse Xbox PRELOADED_STATE: {e}", file=sys.stderr)

    if not pids:
        _xbox_cache["fetched"] = True
        _xbox_cache["free_games"] = []
        _xbox_cache["discount_deals"] = []
        return True

    all_pids = list(pids)
    chunks = [all_pids[i:i + XBOX_CATALOG_CHUNK_SIZE] for i in range(0, len(all_pids), XBOX_CATALOG_CHUNK_SIZE)]

    free_games = []
    discount_deals = []
    seen_free = set()
    seen_deals = set()
    month_prefix = datetime.now(timezone.utc).strftime("%Y_%m")

    def fetch_chunk_products(chunk: list[str]) -> list[dict]:
        params = {
            "bigIds": ",".join(chunk),
            "market": "IN",
            "languages": "en-in",
            "MS-CV": "DUMMY",
        }
        try:
            cat_resp = session.get(XBOX_CATALOG_URL, params=params, timeout=XBOX_SALES_TIMEOUT)
            cat_resp.raise_for_status()
            return cat_resp.json().get("Products", []) or []
        except Exception as e:
            print(f"  ⚠️ Failed to fetch Xbox DisplayCatalog for chunk: {e}", file=sys.stderr)
            return []

    with ThreadPoolExecutor(max_workers=min(XBOX_CATALOG_CONCURRENCY, len(chunks))) as pool:
        chunk_results = pool.map(fetch_chunk_products, chunks)

    for products in chunk_results:
        for p in products:
            try:
                pid = p.get("ProductId")
                if not pid:
                    continue

                loc_props = p.get("LocalizedProperties", []) or []
                loc = loc_props[0] if loc_props else {}
                title = loc.get("ProductTitle", "Unknown Game")
                desc = loc.get("ProductDescription", "")

                thumbnail = ""
                for img in loc.get("Images", []) or []:
                    purpose = img.get("ImagePurpose", "")
                    if purpose in ("Poster", "BoxArt", "FeaturePromotionalSquareArt", "Logo", "Tile"):
                        thumbnail = img.get("Uri", "")
                        if thumbnail.startswith("//"):
                            thumbnail = f"https:{thumbnail}"
                        break
                if not thumbnail and loc.get("Images"):
                    thumbnail = loc["Images"][0].get("Uri", "")
                    if thumbnail.startswith("//"):
                        thumbnail = f"https:{thumbnail}"

                claim_url = f"https://www.xbox.com/en-in/games/store/{pid}"

                for sku_item in p.get("DisplaySkuAvailabilities", []) or []:
                    sku = sku_item.get("Sku") or {}
                    for avail in sku_item.get("Availabilities", []) or []:
                        if not _is_xbox_pc_compatible(p, sku, avail):
                            continue

                        order = avail.get("OrderManagementData") or {}
                        price = order.get("Price") or {}
                        msrp = price.get("MSRP", 0.0)
                        list_price = price.get("ListPrice", 0.0)
                        currency = price.get("CurrencyCode", "INR")

                        avail_end = avail.get("Conditions", {}).get("EndDate")

                        # 100% Free Game Check
                        if list_price == 0 and msrp > 0:
                            if pid not in seen_free:
                                seen_free.add(pid)
                                orig_str = f"₹{msrp:.2f}" if currency == "INR" else f"{currency} {msrp}"
                                free_games.append({
                                    "id": f"xbox_{pid}",
                                    "title": title,
                                    "description": desc,
                                    "claim_url": claim_url,
                                    "thumbnail": thumbnail,
                                    "original_price": orig_str,
                                    "currency": currency,
                                    "expiry": avail_end,
                                })
                        # 90%+ Discount Deal Check
                        elif msrp > 0 and list_price > 0 and list_price < msrp:
                            pct = int(round((1 - (list_price / msrp)) * 100))
                            if DISCOUNT_DEAL_MIN_PCT <= pct <= DISCOUNT_DEAL_MAX_PCT:
                                if pid not in seen_deals:
                                    seen_deals.add(pid)
                                    orig_str = f"₹{msrp:.2f}" if currency == "INR" else f"{currency} {msrp}"
                                    disc_str = f"₹{list_price:.2f}" if currency == "INR" else f"{currency} {list_price}"
                                    discount_deals.append({
                                        "id": f"xbox_deal_{month_prefix}_{pid}",
                                        "title": title,
                                        "description": desc,
                                        "claim_url": claim_url,
                                        "thumbnail": thumbnail,
                                        "original_price": orig_str,
                                        "discounted_price": disc_str,
                                        "discount_pct": f"{pct}%",
                                        "currency": currency,
                                        "expiry": avail_end,
                                    })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed Xbox product: {e}", file=sys.stderr)
                continue

    _xbox_cache["fetched"] = True
    _xbox_cache["free_games"] = free_games
    _xbox_cache["discount_deals"] = discount_deals
    return True


def get_xbox_free_games() -> list[dict] | None:
    if not _fetch_xbox_data():
        return None
    return _xbox_cache["free_games"]


def get_xbox_discount_deals() -> list[dict] | None:
    if not _fetch_xbox_data():
        return None
    return _xbox_cache["discount_deals"]


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(str(text), quote=False)


def _truncate_desc(desc: str) -> str:
    if not desc:
        return ""
    return desc[:DESC_MAX_LEN] + ("…" if len(desc) > DESC_MAX_LEN else "")


def _format_expiry(expiry: str | None, include_time: bool = True) -> str:
    """Return a '⏳ Free until: …\\n\\n' line for an ISO expiry string, or ''."""
    if not expiry:
        return ""
    try:
        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    fmt = "%B %d, %Y at %I:%M %p UTC" if include_time else "%B %d, %Y"
    return f"⏳ Free until: {expiry_dt.strftime(fmt)}\n\n"


def _post_telegram(method: str, payload: dict) -> requests.Response:
    if DRY_RUN:
        print(f"  [dry-run] Would send Telegram {method}.", file=sys.stderr)
        raise RuntimeError("dry-run: Telegram send suppressed")

    last_resp = None
    for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
                json=payload,
                timeout=TELEGRAM_SEND_TIMEOUT,
            )
            last_resp = resp
            if resp.status_code == 429:
                wait_sec = attempt * 5
                retry_after = resp.headers.get("retry_after")
                if retry_after and retry_after.isdigit():
                    wait_sec = max(wait_sec, int(retry_after))
                print(
                    f"  ⚠️ Telegram rate limit (429), retry {attempt}/{TELEGRAM_MAX_RETRIES} after {wait_sec}s…",
                    file=sys.stderr,
                )
                time.sleep(wait_sec)
                continue
            return resp
        except requests.RequestException as e:
            print(f"  ⚠️ Telegram request error (attempt {attempt}/{TELEGRAM_MAX_RETRIES}): {e}", file=sys.stderr)
            last_resp = None
            time.sleep(attempt * 5)
    # Return the last 429 response if we have one; caller raises on !ok.
    if last_resp is not None:
        return last_resp
    raise RuntimeError(f"Telegram {method} failed after {TELEGRAM_MAX_RETRIES} attempts")


def _send_photo(thumbnail: str, message: str) -> bool:
    if not thumbnail:
        return False
    resp = _post_telegram("sendPhoto", {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": thumbnail,
        "caption": message,
        "parse_mode": "HTML",
    })
    return resp.ok


def _send_text(message: str) -> None:
    resp = _post_telegram("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })
    resp.raise_for_status()


def _deliver(thumbnail: str, message: str) -> None:
    """Send a Telegram photo message, falling back to text; no-op if unconfigured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return
    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def _epic_claim_url(game: dict) -> str:
    slug = game.get("product_slug") or game.get("url_slug", "")
    return f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games"


def _epic_thumbnail(game: dict) -> str:
    for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
        for img in game.get("key_images", []):
            if img.get("type") == img_type:
                return img.get("url", "")
    return ""


def send_telegram_epic(game: dict) -> None:
    expires = _format_expiry(game.get("expiry"))
    price_line = f"💰 Original Price: {_escape(game['original_price'])}\n" if game.get("original_price") else ""
    desc = _truncate_desc(game.get("description", ""))
    message = (
        f"🎮 <b>New Free Game on Epic Games!</b>\n\n"
        f"<b>{_escape(game['title'])}</b>\n"
        + (f"{desc}\n\n" if desc else "")
        + price_line
        + expires
        + f"<a href='{_epic_claim_url(game)}'>Claim Now</a>"
    )
    _deliver(_epic_thumbnail(game), message)


def send_telegram_no_new_games() -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    message = (
        "ℹ️ <b>No New Free Games Today</b>\n\n"
        "Checked Epic Games, Amazon Luna, itch.io, STOVE Store, Steam, and Xbox (PC).\n"
        "There are no new currently available free games."
    )
    try:
        _send_text(message)
        print("  ℹ️ Sent Telegram notification: No new free games found today.")
    except Exception as e:
        print(f"  ⚠️ Failed to send 'no new games' notification: {e}", file=sys.stderr)


def send_telegram_luna(game: dict) -> None:
    desc = _truncate_desc(game.get("description", ""))
    expires_str = _format_expiry(game.get("expiry"), include_time=False)
    message = (
        f"🎮 <b>New Free Game on Amazon Luna!</b>\n\n"
        f"<b>{_escape(game['title'])}</b>\n"
        + (f"{desc}\n\n" if desc else "")
        + f"🆓 Free with Prime\n"
        + expires_str
        + f"<a href='{game.get('claim_url', '')}'>Claim Now</a>"
    )
    _deliver(game.get("thumbnail", ""), message)


def send_telegram_itch(game: dict) -> None:
    desc = _truncate_desc(game.get("description", ""))
    author = game.get("author", "")
    author_str = f"by {_escape(author)}\n" if author else ""
    message = (
        f"🎮 <b>New Free Game on itch.io! (100% Off)</b>\n\n"
        f"<b>{_escape(game['title'])}</b>\n"
        f"{author_str}"
        + (f"{desc}\n\n" if desc else "")
        + f"💰 100% Discount (Free)\n\n"
        + f"<a href='{game.get('claim_url', '')}'>Claim Now</a>"
    )
    _deliver(game.get("thumbnail", ""), message)


def send_telegram_stove(game: dict) -> None:
    desc = _truncate_desc(game.get("description", ""))
    expires_str = _format_expiry(game.get("expiry"))
    price_line = f"💰 Original Price: {_escape(game.get('original_price', ''))}\n" if game.get("original_price") else ""
    message = (
        f"🎮 <b>New Free Game on STOVE Store! (100% Off)</b>\n\n"
        f"<b>{_escape(game['title'])}</b>\n"
        + (f"{desc}\n\n" if desc else "")
        + price_line
        + expires_str
        + f"<a href='{game.get('claim_url', '')}'>Claim Now</a>"
    )
    _deliver(game.get("thumbnail", ""), message)


def _generic_free_game(game: dict, store_label: str, heading: str) -> None:
    desc = _truncate_desc(game.get("description", ""))
    expires_str = _format_expiry(game.get("expiry"))
    price_line = f"💰 Original Price: {_escape(game.get('original_price', ''))}\n" if game.get("original_price") else "💰 100% Discount (Free)\n"
    message = (
        f"🎮 <b>{heading}</b>\n\n"
        f"<b>{_escape(game['title'])}</b>\n"
        + (f"{desc}\n\n" if desc else "")
        + price_line
        + expires_str
        + f"<a href='{game.get('claim_url', '')}'>Claim Now</a>"
    )
    _deliver(game.get("thumbnail", ""), message)


def send_telegram_steam(game: dict) -> None:
    _generic_free_game(game, "Steam", "New Free Game on Steam! (100% Off)")


def send_telegram_xbox(game: dict) -> None:
    _generic_free_game(game, "Xbox", "New Free PC Game on Xbox! (100% Off)")


def send_telegram_deal(deal: dict, store_name: str) -> None:
    desc = _truncate_desc(deal.get("description", ""))
    pct = _escape(deal.get("discount_pct", "90%+"))
    original = _escape(deal.get("original_price", ""))
    discounted = _escape(deal.get("discounted_price", ""))

    price_line = (
        f"💰 Original Price: <s>{original}</s>\n"
        f"🏷️ Deal Price: <b>{discounted}</b> ({pct} Off)\n\n"
    ) if original else f"🏷️ Deal Price: <b>{discounted}</b> ({pct} Off)\n\n"

    message = (
        f"🔥 <b>Steep Deal on {_escape(store_name)}! ({pct} Off)</b>\n\n"
        f"<b>{_escape(deal['title'])}</b>\n"
        + (f"{desc}\n\n" if desc else "")
        + price_line
        + f"<a href='{deal.get('claim_url', '')}'>Get Deal Now</a>"
    )
    _deliver(deal.get("thumbnail", ""), message)


def send_telegram_key_drop(drop: dict, platform_name: str) -> None:
    desc = drop.get("description", "")
    message = (
        f"🎁 <b>New Game Key Drop on {_escape(platform_name)}!</b>\n\n"
        f"<b>{_escape(drop['title'])}</b>\n"
        f"{desc}\n\n"
        f"🆓 <b>100% Free Key Drop</b> (Limited Quantity)\n\n"
        f"<a href='{drop.get('claim_url', '')}'>Claim Key Now</a>"
    )
    _deliver(drop.get("thumbnail", ""), message)


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

# kind: "game" (tracked_games), "deal" (tracked_deals), "key_drop" (tracked_key_drops)
# send_fn signature: game -> (item); deal/key_drop -> (item, store_name)
SOURCES: list[dict[str, Any]] = [
    {"name": "Epic Games",  "platform": "epic",   "kind": "game",     "fetch": get_epic_free_games,      "send": send_telegram_epic},
    {"name": "Amazon Luna", "platform": "luna",   "kind": "game",     "fetch": get_luna_free_games,      "send": send_telegram_luna},
    {"name": "itch.io",     "platform": "itch",   "kind": "game",     "fetch": get_itch_free_games,      "send": send_telegram_itch},
    {"name": "STOVE Store", "platform": "stove",  "kind": "game",     "fetch": get_stove_free_games,     "send": send_telegram_stove},
    {"name": "Steam",       "platform": "steam",  "kind": "game",     "fetch": get_steam_free_games,     "send": send_telegram_steam},
    {"name": "Xbox (PC)",   "platform": "xbox",   "kind": "game",     "fetch": get_xbox_free_games,      "send": send_telegram_xbox},
    {"name": "Steam",       "platform": "steam",  "kind": "deal",     "fetch": get_steam_discount_deals, "send": send_telegram_deal},
    {"name": "Epic Games",  "platform": "epic",   "kind": "deal",     "fetch": get_epic_discount_deals,  "send": send_telegram_deal},
    {"name": "Xbox (PC)",   "platform": "xbox",   "kind": "deal",     "fetch": get_xbox_discount_deals,  "send": send_telegram_deal},
    {"name": "Lenovo Legion", "platform": "lenovo", "kind": "key_drop", "fetch": get_lenovo_key_drops,   "send": send_telegram_key_drop},
]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _send_alert(message: str) -> None:
    if DRY_RUN:
        print(f"  [dry-run] Would send alert: {message}", file=sys.stderr)
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        _send_text(f"⚠️ <b>Bot Alert</b>\n\n{message}")
    except Exception:
        pass


def _load_known_ids(kind: str, platform: str) -> set[str]:
    if kind == "game":
        return db.load_known_game_ids(platform)
    if kind == "deal":
        return db.load_known_deal_ids(platform)
    return db.load_known_key_drop_ids(platform)


def _save_items(kind: str, platform: str, items: list[dict]) -> None:
    if kind == "game":
        db.save_games(platform, items)
    elif kind == "deal":
        db.save_deals(platform, items)
    else:
        db.save_key_drops(platform, items)


def check_source(name: str, kind: str, fetch_fn, send_fn, platform: str, dry_run: bool = False) -> tuple[int, bool]:
    """Check one source; returns (notifications_sent, ok)."""
    label = {"game": "free games", "deal": "90%+ discount deals", "key_drop": "Key Drops"}[kind]
    print(f"\n[{datetime.now().isoformat()}] Checking {name} {label}…")

    known_ids = _load_known_ids(kind, platform)
    print(f"  [DB] Known {name} {label} count: {len(known_ids)}")

    try:
        current = fetch_fn()
    except Exception as e:
        print(f"  ❌ Failed to fetch {name} {label}: {e}", file=sys.stderr)
        _send_alert(f"Failed to fetch {name} {label}:\n<code>{e}</code>")
        return 0, False

    if current is None:
        print(f"  ❌ {name} {label} fetch returned no data (possible API change)", file=sys.stderr)
        _send_alert(
            f"{name} {label} fetch returned no data.\n"
            "The API endpoint may have changed or is unreachable."
        )
        return 0, False

    if kind == "deal":
        print(f"  Current 90%+ deals: {[d['title'] + ' (' + d['discount_pct'] + ')' for d in current]}")
    else:
        print(f"  Current items: {[g['title'] for g in current]}")

    current_ids = {g["id"] for g in current}
    new_ids = current_ids - known_ids
    notified = 0
    if new_ids:
        print(f"  New items detected: {new_ids}")
        for item in current:
            if item["id"] in new_ids:
                print(f"  Notifying about: {item['title']}")
                if dry_run:
                    print("  [dry-run] Skipped notification.")
                    notified += 1
                    continue
                try:
                    if kind == "game":
                        send_fn(item)
                    else:
                        send_fn(item, name)
                    print(f"  ✅ Notification sent for {item['title']}")
                    notified += 1
                except Exception as e:
                    print(f"  ❌ Failed to send notification: {e}", file=sys.stderr)
    else:
        print("  No new items found.")

    if dry_run:
        print("  [dry-run] Skipped saving items to database.")
    else:
        _save_items(kind, platform, current)
        print(f"  [DB] Saved active {name} {label} to database.")

    return notified, True


def run_sources(selected: list[dict[str, Any]] | None = None, dry_run: bool = False) -> int:
    """Run all (or selected) source checks concurrently; returns total notifications sent, or -1 if all sources failed."""
    sources = selected if selected is not None else SOURCES
    total = 0
    ok_count = 0

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {
            pool.submit(
                check_source,
                s["name"], s["kind"], s["fetch"], s["send"], s["platform"], dry_run,
            ): s
            for s in sources
        }
        for fut, s in futures.items():
            try:
                notified, ok = fut.result()
                total += notified
                if ok:
                    ok_count += 1
            except Exception as e:
                print(f"  ❌ Unexpected error checking {s['name']} ({s['kind']}): {e}", file=sys.stderr)

    if ok_count == 0:
        print(f"❌ All {len(sources)} source checks failed.", file=sys.stderr)
        return -1

    return total


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Check stores for free games & steep deals, notify via Telegram.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and diff but skip Telegram notifications and DB writes.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="PLATFORM",
        help="Only check the given platform (repeatable): epic, luna, itch, stove, steam, xbox, lenovo.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List available platforms and exit.",
    )
    args = parser.parse_args(argv)

    global DRY_RUN
    DRY_RUN = args.dry_run

    if args.list_sources:
        for s in SOURCES:
            print(f"{s['platform']:8} {s['kind']:8} {s['name']}")
        return 0

    selected = None
    if args.source:
        wanted = {p.lower() for p in args.source}
        known = {s["platform"] for s in SOURCES}
        unknown = wanted - known
        if unknown:
            print(f"❌ Unknown platform(s): {', '.join(sorted(unknown))}. "
                  f"Available: {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        selected = [s for s in SOURCES if s["platform"] in wanted]

    db.init_db()

    if not args.dry_run and not db.is_db_enabled():
        err_msg = "NeonDB is not configured or DATABASE_URL environment variable is missing."
        print(f"❌ {err_msg}", file=sys.stderr)
        _send_alert(f"<b>NeonDB Not Configured</b>\n\n{err_msg}\nPlease set <code>DATABASE_URL</code> in environment / GitHub Secrets.")
        return 1

    total = run_sources(selected, dry_run=args.dry_run)

    db.close_connection()

    if total < 0:
        return 1

    if total == 0:
        if DRY_RUN:
            print("  [dry-run] Would send 'No new free games today' notification.")
        else:
            send_telegram_no_new_games()

    return 0


if __name__ == "__main__":
    sys.exit(main())



