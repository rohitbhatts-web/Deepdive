"""READ-ONLY check of the remaining Mesh balance and Tavily search allowance. Changes nothing and makes no inference or search.

Mesh: GET /balance (available, balance and reserved USD). Tavily: GET /usage (credits used and plan limit). Only numbers are printed; no key, header
or free-text response field is ever printed. If an endpoint is missing or answers unexpectedly the figure is reported as Unavailable.

    uv run python scripts/check_allowance.py
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.mesh_client import get_mesh_client


def money(value) -> str:
    try:
        return f"${float(value):,.4f}"
    except (TypeError, ValueError):
        return "Unavailable"


def mesh() -> None:
    try:
        data = get_mesh_client().get("/balance", cast_to=object)
    except Exception as exc:  # noqa: BLE001
        print(f"Mesh balance: Unavailable ({type(exc).__name__}, HTTP {getattr(exc, 'status_code', 'n/a')})")
        return
    print("Mesh balance (read-only GET /balance):")
    for key in ("available_usd", "balance_usd", "reserved_usd"):
        print(f"  {key}: {money(data.get(key))}")
    print(f"  updated_at: {data.get('updated_at') if isinstance(data.get('updated_at'), str) else 'Unavailable'}")


def tavily() -> None:
    key = get_settings().tavily_api_key
    if not key:
        print("Tavily usage: Unavailable (no key configured)")
        return
    try:
        r = httpx.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {key}"}, timeout=20, trust_env=False)
    except httpx.HTTPError as exc:
        print(f"Tavily usage: Unavailable ({type(exc).__name__})")
        return
    if r.status_code != 200:
        print(f"Tavily usage: Unavailable (HTTP {r.status_code})")
        return
    body = r.json()
    print("Tavily usage (read-only GET /usage):")

    def num(d, k):
        v = d.get(k) if isinstance(d, dict) else None
        return v if isinstance(v, (int, float)) else "Unavailable"

    account = body.get("account", {})
    print(f"  key usage: {num(body.get('key', {}), 'usage')} of limit {num(body.get('key', {}), 'limit')}")
    print(f"  account plan usage: {num(account, 'plan_usage')} of plan limit {num(account, 'plan_limit')}")
    print(f"  search usage: {num(account, 'search_usage')}; pay-as-you-go usage: {num(account, 'paygo_usage')} of limit {num(account, 'paygo_limit')}")


if __name__ == "__main__":
    mesh()
    tavily()
