"""Mortgage broker discovery — Google Places API with monthly refresh and static fallback.

Contact enrichment pipeline:
  1. Google Places Text Search  → name, address, rating, place_id
  2. Google Places Details      → phone, website (one call per broker)
  3. Hunter.io Domain Search    → email address from website domain (optional)
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

BROKERS_FILE = Path("data/brokers.json")
PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
HUNTER_URL = "https://api.hunter.io/v2/domain-search"

# Curated fallback — used when Google Places API key is absent.
# Phone/email/website filled in from public sources; update if stale.
STATIC_FALLBACK = [
    {
        "name": "RPM Mortgage",
        "address": "Fremont, CA",
        "phone": "(925) 478-8630",
        "website": "https://www.rpmmortgage.com",
        "email": "contact@rpmmortgage.com",
        "rating": 4.9,
        "reviews": 340,
        "specialty": "ARM, Bay Area refinance",
        "google_maps": "https://maps.google.com/?q=RPM+Mortgage+Fremont+CA",
        "contact_source": "static",
    },
    {
        "name": "Bay Equity Home Loans",
        "address": "Newark / Fremont, CA",
        "phone": "(415) 370-1711",
        "website": "https://www.bayequityhomeloans.com",
        "email": "info@bayequityhomeloans.com",
        "rating": 4.8,
        "reviews": 210,
        "specialty": "Purchase & refi, Bay Area",
        "google_maps": "https://maps.google.com/?q=Bay+Equity+Home+Loans+Fremont+CA",
        "contact_source": "static",
    },
    {
        "name": "CrossCountry Mortgage",
        "address": "San Jose, CA",
        "phone": "(877) 351-3400",
        "website": "https://www.crosscountrymortgage.com",
        "email": "info@crosscountrymortgage.com",
        "rating": 4.8,
        "reviews": 185,
        "specialty": "Conforming, jumbo, ARM",
        "google_maps": "https://maps.google.com/?q=CrossCountry+Mortgage+San+Jose+CA",
        "contact_source": "static",
    },
    {
        "name": "Guaranteed Rate",
        "address": "Oakland / Fremont, CA",
        "phone": "(773) 290-0505",
        "website": "https://www.rate.com",
        "email": "customerservice@rate.com",
        "rating": 4.7,
        "reviews": 230,
        "specialty": "Digital-first, competitive rates",
        "google_maps": "https://maps.google.com/?q=Guaranteed+Rate+Oakland+CA",
        "contact_source": "static",
    },
    {
        "name": "LoanDepot",
        "address": "Fremont, CA",
        "phone": "(888) 337-6888",
        "website": "https://www.loandepot.com",
        "email": "customercare@loandepot.com",
        "rating": 4.7,
        "reviews": 160,
        "specialty": "CA-headquartered, refinance specialists",
        "google_maps": "https://maps.google.com/?q=LoanDepot+Fremont+CA",
        "contact_source": "static",
    },
]


def _needs_refresh(data: dict, refresh_days: int) -> bool:
    last = data.get("last_updated")
    if not last:
        return True
    last_date = datetime.strptime(last, "%Y-%m-%d").date()
    return (date.today() - last_date).days >= refresh_days


def _fetch_place_details(place_id: str, api_key: str) -> dict:
    """Fetch phone and website for a single place from Google Places Details API."""
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website",
        "key": api_key,
    }
    try:
        resp = requests.get(PLACES_DETAILS_URL, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        return {
            "phone": result.get("formatted_phone_number", ""),
            "website": result.get("website", ""),
        }
    except Exception:
        return {"phone": "", "website": ""}


def _fetch_email_from_hunter(domain: str, api_key: str) -> str:
    """Look up a contact email for a domain via Hunter.io (free: 25/month)."""
    if not domain or not api_key:
        return ""
    try:
        resp = requests.get(
            HUNTER_URL,
            params={"domain": domain, "api_key": api_key, "limit": 3},
            timeout=10,
        )
        resp.raise_for_status()
        emails = resp.json().get("data", {}).get("emails", [])
        # Prefer generic contact emails over personal ones
        for priority in ("contact", "info", "hello", "loans", "mortgage"):
            for e in emails:
                val = e.get("value", "").lower()
                if priority in val:
                    return e["value"]
        # Fall back to first email found
        return emails[0]["value"] if emails else ""
    except Exception:
        return ""


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    host = parsed.netloc or parsed.path
    return host.lstrip("www.")


def _fetch_from_google(api_key: str, hunter_key: str, config: dict) -> list[dict]:
    location = config["brokers"]["search_location"]
    min_rating = config["brokers"]["min_rating"]
    min_reviews = config["brokers"]["min_reviews"]
    max_results = config["brokers"]["max_results"]

    # Step 1: text search
    try:
        resp = requests.get(
            PLACES_SEARCH_URL,
            params={"query": f"mortgage broker {location}", "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        places = resp.json().get("results", [])
    except Exception:
        return []

    candidates = [
        p for p in places
        if p.get("rating", 0) >= min_rating
        and p.get("user_ratings_total", 0) >= min_reviews
    ]
    candidates.sort(key=lambda x: (-x.get("rating", 0), -x.get("user_ratings_total", 0)))
    candidates = candidates[:max_results]

    brokers = []
    for p in candidates:
        place_id = p.get("place_id", "")

        # Step 2: fetch phone + website
        details = _fetch_place_details(place_id, api_key) if place_id else {}
        phone = details.get("phone", "")
        website = details.get("website", "")

        # Step 3: fetch email via Hunter.io
        domain = _domain_from_url(website)
        email = _fetch_email_from_hunter(domain, hunter_key) if domain else ""

        brokers.append({
            "name": p.get("name", ""),
            "address": p.get("formatted_address", ""),
            "phone": phone,
            "website": website,
            "email": email,
            "rating": p.get("rating", 0),
            "reviews": p.get("user_ratings_total", 0),
            "specialty": "Mortgage broker",
            "google_maps": f"https://maps.google.com/?place_id={place_id}",
            "contact_source": "google_places" + ("+hunter" if email else ""),
        })

    return brokers


def get_brokers(config: dict, google_api_key: str = "", hunter_api_key: str = "") -> dict:
    """Return broker list, refreshing from Google Places + Hunter.io if stale."""
    refresh_days = config["brokers"]["refresh_days"]
    existing = {}

    if BROKERS_FILE.exists():
        with open(BROKERS_FILE) as f:
            existing = json.load(f)

    if not _needs_refresh(existing, refresh_days):
        return existing

    brokers = []
    source = "static_fallback"

    if google_api_key:
        brokers = _fetch_from_google(google_api_key, hunter_api_key, config)
        if brokers:
            source = "google_places"

    if not brokers:
        brokers = STATIC_FALLBACK[: config["brokers"]["max_results"]]
        source = "static_fallback"

    data = {
        "last_updated": date.today().isoformat(),
        "source": source,
        "brokers": brokers,
    }
    BROKERS_FILE.parent.mkdir(exist_ok=True)
    with open(BROKERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return data
