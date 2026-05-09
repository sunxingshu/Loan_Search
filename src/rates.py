"""Rate fetching from FRED API + lender estimation model."""

import json
from datetime import date, timedelta
from pathlib import Path

import requests

HISTORY_FILE = Path("data/rates_history.json")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# 5/1 ARM historically trades ~0.75–1.0% below 30yr fixed.
# Used as fallback when FRED MORTGAGE5US data is unavailable.
ARM_SPREAD_FROM_30YR = 0.875


def _fetch_fred_series(series_id: str, api_key: str) -> float | None:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 2,
        "observation_start": (date.today() - timedelta(days=21)).isoformat(),
    }
    try:
        resp = requests.get(FRED_URL, params=params, timeout=10)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        for o in obs:
            if o["value"] != ".":
                return float(o["value"])
    except Exception:
        pass
    return None


def fetch_rates(fred_api_key: str) -> dict:
    """Fetch current 30yr fixed and 5/1 ARM rates from FRED.

    Falls back to last known rates from history if FRED is unreachable.
    """
    rate_30yr = _fetch_fred_series("MORTGAGE30US", fred_api_key)
    rate_5_1 = _fetch_fred_series("MORTGAGE5US", fred_api_key)

    if rate_5_1 is None and rate_30yr is not None:
        rate_5_1 = round(rate_30yr - ARM_SPREAD_FROM_30YR, 3)
        arm_source = "derived"
    elif rate_5_1 is not None:
        arm_source = "fred"
    else:
        arm_source = "unavailable"

    # If FRED completely unavailable, fall back to last known rates from history
    if rate_30yr is None:
        history = load_rate_history()
        last = next((h for h in reversed(history) if h.get("rate_30yr_fixed")), None)
        if last:
            rate_30yr = last["rate_30yr_fixed"]
            rate_5_1 = last.get("rate_5_1_arm") or round(rate_30yr - ARM_SPREAD_FROM_30YR, 3)
            arm_source = "last_known"
            print(f"WARNING: FRED unavailable — using last known rates from {last['date']}")

    return {
        "date": date.today().isoformat(),
        "rate_30yr_fixed": rate_30yr,
        "rate_5_1_arm": rate_5_1,
        "arm_source": arm_source,
    }


def load_rate_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with open(HISTORY_FILE) as f:
        return json.load(f)


def save_rate_history(history: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def get_4week_trend(history: list[dict], field: str = "rate_5_1_arm") -> list[float | None]:
    """Return the last 4 weekly readings for sparkline display."""
    readings = [
        h.get(field) for h in history
        if h.get(field) is not None
    ]
    return readings[-4:] if len(readings) >= 4 else readings


def compute_lender_rates(arm_benchmark: float, config: dict) -> list[dict]:
    """
    Estimate each lender's rate using their market spread and relationship discount.
    Rates are estimates — not live quotes.
    """
    relationship_assets = config["borrower"]["relationship_assets_current"]
    results = []

    for lender in config["lenders"]:
        spread = lender.get("market_spread", 0.0)
        base_rate = round(arm_benchmark + spread, 3)

        discount = lender.get("relationship_discount", 0.0) if relationship_assets >= 500_000 else 0.0
        your_rate = round(base_rate - discount, 3)

        results.append({
            "name": lender["name"],
            "base_rate": base_rate,
            "relationship_discount": discount,
            "your_rate": your_rate,
            "notes": lender.get("notes", ""),
        })

    return sorted(results, key=lambda x: x["your_rate"])
