#!/usr/bin/env python3
"""Check Epic Games Store, Amazon Luna, itch.io, STOVE Store, Steam, and Xbox PC free games & deals, notify via Telegram."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
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
    "?query&start=0&count=50&specials=1&maxprice=free&infinite=1"
)
STEAM_DEALS_SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=100&specials=1&infinite=1"
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
# itch.io
# ---------------------------------------------------------------------------

def get_itch_free_games(max_pages: int = 5) -> list[dict] | None:
    headers = {"User-Agent": ITCH_USER_AGENT}
    free_games = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"{ITCH_ON_SALE_URL}&page={page}"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
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

def get_stove_free_games(max_pages: int = 5) -> list[dict] | None:
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
            resp = requests.get(STOVE_API_URL, headers=headers, params=params, timeout=30)
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
    headers = {"User-Agent": STEAM_USER_AGENT}
    try:
        resp = requests.get(STEAM_SEARCH_URL, headers=headers, timeout=30)
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

            description = ""
            try:
                details_resp = requests.get(
                    f"https://store.steampowered.com/api/appdetails?appids={appid}",
                    headers=headers,
                    timeout=10,
                )
                if details_resp.ok:
                    details_data = details_resp.json().get(str(appid), {}).get("data", {})
                    description = details_data.get("short_description", "")
            except Exception:
                pass

            free_games.append({
                "id": f"steam_{appid}",
                "title": title,
                "description": description,
                "claim_url": claim_url,
                "thumbnail": thumbnail,
                "original_price": original_price,
                "expiry": None,
            })
        except Exception as e:
            print(f"  ⚠️ Skipping malformed Steam game entry: {e}", file=sys.stderr)
            continue

    return free_games


def get_steam_discount_deals() -> list[dict] | None:
    headers = {"User-Agent": STEAM_USER_AGENT}
    try:
        resp = requests.get(STEAM_DEALS_SEARCH_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️ Failed to fetch Steam Deals Search API: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Invalid JSON from Steam Deals Search API: {e}", file=sys.stderr)
        return None

    content = data.get("results_html", "")
    if not content:
        return []

    soup = BeautifulSoup(content, "html.parser")
    rows = soup.find_all("a", class_="search_result_row")
    deals = []
    seen_ids = set()

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

            # Strictly 90% to 99% off (excluding 100% free games to avoid duplicate notification)
            if not (90 <= pct < 100):
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

            description = ""
            try:
                details_resp = requests.get(
                    f"https://store.steampowered.com/api/appdetails?appids={appid}",
                    headers=headers,
                    timeout=10,
                )
                if details_resp.ok:
                    details_data = details_resp.json().get(str(appid), {}).get("data", {})
                    description = details_data.get("short_description", "")
            except Exception:
                pass

            month_prefix = datetime.now(timezone.utc).strftime("%Y_%m")
            deals.append({
                "id": f"steam_deal_{month_prefix}_{appid}",
                "title": title,
                "description": description,
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

    return deals


def get_epic_discount_deals(max_pages: int = 5) -> list[dict] | None:
    query = """
    query searchStoreQuery($allowCountries: String, $category: String, $count: Int, $country: String!, $locale: String, $sortBy: String, $sortDir: String, $start: Int, $onSale: Boolean) {
      Catalog {
        searchStore(allowCountries: $allowCountries, category: $category, count: $count, country: $country, locale: $locale, sortBy: $sortBy, sortDir: $sortDir, start: $start, onSale: $onSale) {
          paging {
            total
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
          }
        }
      }
    }
    """
    headers = {
        "User-Agent": STEAM_USER_AGENT,
        "Content-Type": "application/json"
    }

    deals = []
    seen_ids = set()
    start = 0
    count = 100

    for page in range(max_pages):
        variables = {
            "allowCountries": "IN",
            "category": "games/edition/base",
            "count": count,
            "country": "IN",
            "locale": "en-US",
            "sortBy": "releaseDate",
            "sortDir": "DESC",
            "start": start,
            "onSale": True
        }

        try:
            resp = requests.post(
                EPIC_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  ⚠️ Failed to fetch Epic GraphQL API for deals (start {start}): {e}", file=sys.stderr)
            break
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Invalid JSON from Epic GraphQL API for deals (start {start}): {e}", file=sys.stderr)
            break

        try:
            store_data = data["data"]["Catalog"]["searchStore"]
            elements = store_data.get("elements", [])
            total_store = store_data.get("paging", {}).get("total", 0)
        except (KeyError, TypeError) as e:
            print(f"  ⚠️ Unexpected Epic GraphQL API response structure for deals: {e}", file=sys.stderr)
            break

        if not elements:
            break

        for el in elements:
            try:
                game_id = el.get("id")
                if not game_id or game_id in seen_ids:
                    continue

                price = el.get("price", {}) or {}
                total = price.get("totalPrice", {}) or {}
                orig = total.get("originalPrice", 0)
                disc = total.get("discountPrice", 0)

                if orig <= 0 or disc >= orig:
                    continue

                pct = int(round((1 - disc / orig) * 100))
                # Strictly 90% to 99% off (excluding 100% free games to avoid duplicate notification)
                if not (90 <= pct < 100):
                    continue

                seen_ids.add(game_id)
                title = el.get("title", "Unknown Game")
                description = el.get("description", "")
                fmt = total.get("fmtPrice", {}) or {}
                original_price = fmt.get("originalPrice", "")
                discounted_price = fmt.get("discountPrice", "")

                slug = el.get("productSlug") or el.get("urlSlug", "")
                claim_url = f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/"

                images = el.get("keyImages", [])
                thumbnail = ""
                for img_type in ("Thumbnail", "OfferImageTall", "OfferImageWide"):
                    for img in images:
                        if img.get("type") == img_type:
                            thumbnail = img["url"]
                            break
                    if thumbnail:
                        break

                month_prefix = datetime.now(timezone.utc).strftime("%Y_%m")
                deals.append({
                    "id": f"epic_deal_{month_prefix}_{game_id}",
                    "title": title,
                    "description": description,
                    "claim_url": claim_url,
                    "thumbnail": thumbnail,
                    "original_price": original_price,
                    "discounted_price": discounted_price,
                    "discount_pct": f"{pct}%",
                    "expiry": None,
                })
            except Exception as e:
                print(f"  ⚠️ Skipping malformed Epic deal entry: {e}", file=sys.stderr)
                continue

        start += count
        if start >= total_store:
            break

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
    if _xbox_cache["fetched"]:
        return True

    headers = {"User-Agent": XBOX_USER_AGENT}
    try:
        resp = requests.get(XBOX_SALES_URL, headers=headers, timeout=30)
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
    chunks = [all_pids[i:i + 30] for i in range(0, len(all_pids), 30)]

    free_games = []
    discount_deals = []
    seen_free = set()
    seen_deals = set()
    month_prefix = datetime.now(timezone.utc).strftime("%Y_%m")

    for chunk in chunks:
        params = {
            "bigIds": ",".join(chunk),
            "market": "IN",
            "languages": "en-in",
            "MS-CV": "DUMMY",
        }
        try:
            cat_resp = requests.get(XBOX_CATALOG_URL, params=params, headers=headers, timeout=30)
            cat_resp.raise_for_status()
            products = cat_resp.json().get("Products", []) or []
        except Exception as e:
            print(f"  ⚠️ Failed to fetch Xbox DisplayCatalog for chunk: {e}", file=sys.stderr)
            continue

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
                            if 90 <= pct < 100:
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


def send_telegram_itch(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game.get("description", "")
    if desc:
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

    author = game.get("author", "")
    author_str = f"by {author}\n" if author else ""
    claim_url = game.get("claim_url", "")
    thumbnail = game.get("thumbnail", "")

    message = (
        f"🎮 <b>New Free Game on itch.io! (100% Off)</b>\n\n"
        f"<b>{title}</b>\n"
        f"{author_str}"
        f"{desc}\n\n"
        f"💰 100% Discount (Free)\n\n"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_stove(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game.get("description", "")
    if desc:
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

    original = game.get("original_price", "")
    claim_url = game.get("claim_url", "")
    thumbnail = game.get("thumbnail", "")
    expiry = game.get("expiry")

    expires_str = ""
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            expires_str = f"⏳ Free until: {expiry_dt.strftime('%B %d, %Y at %I:%M %p UTC')}\n\n"
        except (ValueError, TypeError):
            pass

    message = (
        f"🎮 <b>New Free Game on STOVE Store! (100% Off)</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"💰 Original Price: {original}\n"
        f"{expires_str}"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_steam(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game.get("description", "")
    if desc:
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

    original = game.get("original_price", "")
    claim_url = game.get("claim_url", "")
    thumbnail = game.get("thumbnail", "")
    expiry = game.get("expiry")

    expires_str = ""
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            expires_str = f"⏳ Free until: {expiry_dt.strftime('%B %d, %Y at %I:%M %p UTC')}\n\n"
        except (ValueError, TypeError):
            pass

    price_str = f"💰 Original Price: {original}\n" if original else "💰 100% Discount (Free)\n"
    desc_str = f"{desc}\n\n" if desc else ""

    message = (
        f"🎮 <b>New Free Game on Steam! (100% Off)</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc_str}"
        f"{price_str}"
        f"{expires_str}"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_xbox(game: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = game["title"]
    desc = game.get("description", "")
    if desc:
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

    original = game.get("original_price", "")
    claim_url = game.get("claim_url", "")
    thumbnail = game.get("thumbnail", "")
    expiry = game.get("expiry")

    expires_str = ""
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            expires_str = f"⏳ Free until: {expiry_dt.strftime('%B %d, %Y at %I:%M %p UTC')}\n\n"
        except (ValueError, TypeError):
            pass

    price_str = f"💰 Original Price: {original}\n" if original else "💰 100% Discount (Free)\n"
    desc_str = f"{desc}\n\n" if desc else ""

    message = (
        f"🎮 <b>New Free PC Game on Xbox! (100% Off)</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc_str}"
        f"{price_str}"
        f"{expires_str}"
        f"<a href='{claim_url}'>Claim Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_deal(deal: dict, store_name: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = deal["title"]
    desc = deal.get("description", "")
    if desc:
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

    original = deal.get("original_price", "")
    discounted = deal.get("discounted_price", "")
    pct = deal.get("discount_pct", "90%+")
    claim_url = deal.get("claim_url", "")
    thumbnail = deal.get("thumbnail", "")

    price_line = (
        f"💰 Original Price: <s>{original}</s>\n"
        f"🏷️ Deal Price: <b>{discounted}</b> ({pct} Off)\n\n"
    ) if original else f"🏷️ Deal Price: <b>{discounted}</b> ({pct} Off)\n\n"
    desc_str = f"{desc}\n\n" if desc else ""

    message = (
        f"🔥 <b>Steep Deal on {store_name}! ({pct} Off)</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc_str}"
        f"{price_line}"
        f"<a href='{claim_url}'>Get Deal Now</a>"
    )

    if _send_photo(thumbnail, message):
        return
    _send_text(message)


def send_telegram_key_drop(drop: dict, platform_name: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return

    title = drop["title"]
    desc = drop.get("description", "")
    claim_url = drop.get("claim_url", "")
    thumbnail = drop.get("thumbnail", "")

    message = (
        f"🎁 <b>New Game Key Drop on {platform_name}!</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"🆓 <b>100% Free Key Drop</b> (Limited Quantity)\n\n"
        f"<a href='{claim_url}'>Claim Key Now</a>"
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
    print(f"  [DB tracked_games] Known {name} games count: {len(known_ids)}")

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
    print(f"  [DB tracked_games] Saved active {name} games to database.")

    return notified


def check_deal_source(name: str, fetch_fn, send_fn, platform: str) -> int:
    print(f"\n[{datetime.now().isoformat()}] Checking {name} 90%+ discount deals…")

    known_ids = db.load_known_deal_ids(platform)
    print(f"  [DB tracked_deals] Known {name} deal count: {len(known_ids)}")

    try:
        current = fetch_fn()
    except Exception as e:
        print(f"  ❌ Failed to fetch {name} deals: {e}", file=sys.stderr)
        _send_alert(f"Failed to fetch {name} deals:\n<code>{e}</code>")
        return 0

    if current is None:
        print(f"  ❌ {name} deals fetch returned no data", file=sys.stderr)
        return 0

    current_ids = {d["id"] for d in current}
    print(f"  Current 90%+ deals: {[d['title'] + ' (' + d['discount_pct'] + ')' for d in current]}")

    new_ids = current_ids - known_ids
    notified = 0
    if new_ids:
        print(f"  New 90%+ deals detected: {new_ids}")
        for deal in current:
            if deal["id"] in new_ids:
                print(f"  Notifying about deal: {deal['title']} ({deal['discount_pct']})")
                try:
                    send_fn(deal, name)
                    print(f"  ✅ Notification sent for deal: {deal['title']}")
                    notified += 1
                except Exception as e:
                    print(f"  ❌ Failed to send deal notification: {e}", file=sys.stderr)
    else:
        print("  No new 90%+ deals found.")

    db.save_deals(platform, current)
    print(f"  [DB tracked_deals] Saved active {name} deals to database.")

    return notified


def check_key_drop_source(name: str, fetch_fn, send_fn, platform: str) -> int:
    print(f"\n[{datetime.now().isoformat()}] Checking {name} Key Drops…")

    known_ids = db.load_known_key_drop_ids(platform)
    print(f"  [DB tracked_key_drops] Known {name} key drop count: {len(known_ids)}")

    try:
        current = fetch_fn()
    except Exception as e:
        print(f"  ❌ Failed to fetch {name} key drops: {e}", file=sys.stderr)
        _send_alert(f"Failed to fetch {name} key drops:\n<code>{e}</code>")
        return 0

    if current is None:
        print(f"  ❌ {name} key drops fetch returned no data", file=sys.stderr)
        return 0

    current_ids = {d["id"] for d in current}
    print(f"  Current key drops: {[d['title'] for d in current]}")

    new_ids = current_ids - known_ids
    notified = 0
    if new_ids:
        print(f"  New key drops detected: {new_ids}")
        for drop in current:
            if drop["id"] in new_ids:
                print(f"  Notifying about key drop: {drop['title']}")
                try:
                    send_fn(drop, name)
                    print(f"  ✅ Notification sent for key drop: {drop['title']}")
                    notified += 1
                except Exception as e:
                    print(f"  ❌ Failed to send key drop notification: {e}", file=sys.stderr)
    else:
        print("  No new key drops found.")

    db.save_key_drops(platform, current)
    print(f"  [DB tracked_key_drops] Saved active {name} key drops to database.")

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
    # Free games (100% off)
    total += check_source("Epic Games", get_epic_free_games, send_telegram_epic, "epic")
    total += check_source("Amazon Luna", get_luna_free_games, send_telegram_luna, "luna")
    total += check_source("itch.io", get_itch_free_games, send_telegram_itch, "itch")
    total += check_source("STOVE Store", get_stove_free_games, send_telegram_stove, "stove")
    total += check_source("Steam", get_steam_free_games, send_telegram_steam, "steam")
    total += check_source("Xbox (PC)", get_xbox_free_games, send_telegram_xbox, "xbox")

    # 90%+ Discount Deals (separate tracked_deals DB table)
    total += check_deal_source("Steam", get_steam_discount_deals, send_telegram_deal, "steam")
    total += check_deal_source("Epic Games", get_epic_discount_deals, send_telegram_deal, "epic")
    total += check_deal_source("Xbox (PC)", get_xbox_discount_deals, send_telegram_deal, "xbox")

    # Key Drops (separate tracked_key_drops DB table)
    total += check_key_drop_source("Lenovo Legion", get_lenovo_key_drops, send_telegram_key_drop, "lenovo")

    if total == 0:
        send_telegram_no_new_games()

    return 0




if __name__ == "__main__":
    sys.exit(main())



