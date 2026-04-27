"""Mortgage broker discovery — Google Places API with monthly refresh and static fallback."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BROKERS_FILE = Path("data/brokers.json")

# Curated fallback list for Alameda County when Google Places API key is absent.
# Update manually if ratings change significantly.
STATIC_FALLBACK = [
    {
        "name": "RPM Mortgage",
        "contact": "Fremont, CA",
        "rating": 4.9,
        "reviews": 340,
        "specialty": "ARM, Bay Area refinance",
        "google_maps": "https://maps.google.com/?q=RPM+Mortgage+Fremont+CA",
    },
    {
        "name": "Bay Equity Home Loans",
        "contact": "Newark / Fremont, CA",
        "rating": 4.8,
        "reviews": 210,
        "specialty": "Purchase & refi, Bay Area",
        "google_maps": "https://maps.google.com/?q=Bay+Equity+Home+Loans+Fremont+CA",
    },
    {
        "name": "CrossCountry Mortgage",
        "contact": "San Jose, CA",
        "rating": 4.8,
        "reviews": 185,
        "specialty": "Conforming, jumbo, ARM",
        "google_maps": "https://maps.google.com/?q=CrossCountry+Mortgage+San+Jose+CA",
    },
    {
        "name": "Guaranteed Rate",
        "contact": "Oakland / Fremont, CA",
        "rating": 4.7,
        "reviews": 230,
        "specialty": "Digital-first, competitive rates",
        "google_maps": "https://maps.google.com/?q=Guaranteed+Rate+Oakland+CA",
    },
    {
        "name": "LoanDepot",
        "contact": "Fremont, CA",
        "rating": 4.7,
        "reviews": 160,
        "specialty": "CA-headquartered, refinance specialists",
        "google_maps": "https://maps.google.com/?q=LoanDepot+Fremont+CA",
    },
]


def _needs_refresh(data: dict, refresh_days: int) -> bool:
    last = data.get("last_updated")
    if not last:
        return True
    last_date = datetime.strptime(last, "%Y-%m-%d").date()
    return (date.today() - last_date).days >= refresh_days


def _fetch_from_google(api_key: str, config: dict) -> list[dict]:
    location = config["brokers"]["search_location"]
    radius = config["brokers"]["search_radius_meters"]
    min_rating = config["brokers"]["min_rating"]
    min_reviews = config["brokers"]["min_reviews"]
    max_results = config["brokers"]["max_results"]

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": f"mortgage broker {location}",
        "radius": radius,
        "key": api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        places = resp.json().get("results", [])
    except Exception:
        return []

    brokers = []
    for p in places:
        rating = p.get("rating", 0)
        reviews = p.get("user_ratings_total", 0)
        if rating < min_rating or reviews < min_reviews:
            continue
        brokers.append({
            "name": p.get("name", ""),
            "contact": p.get("formatted_address", ""),
            "rating": rating,
            "reviews": reviews,
            "specialty": "Mortgage broker",
            "google_maps": f"https://maps.google.com/?place_id={p.get('place_id', '')}",
        })

    brokers.sort(key=lambda x: (-x["rating"], -x["reviews"]))
    return brokers[:max_results]


def get_brokers(config: dict, google_api_key: str = "") -> dict:
    """Return broker list, refreshing from Google Places if stale or key provided."""
    refresh_days = config["brokers"]["refresh_days"]
    existing = {}

    if BROKERS_FILE.exists():
        with open(BROKERS_FILE) as f:
            existing = json.load(f)

    if not _needs_refresh(existing, refresh_days):
        return existing

    # Attempt live refresh
    brokers = []
    source = "static_fallback"
    if google_api_key:
        brokers = _fetch_from_google(google_api_key, config)
        if brokers:
            source = "google_places"

    if not brokers:
        brokers = STATIC_FALLBACK[: config["brokers"]["max_results"]]

    data = {
        "last_updated": date.today().isoformat(),
        "source": source,
        "brokers": brokers,
    }
    BROKERS_FILE.parent.mkdir(exist_ok=True)
    with open(BROKERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return data
