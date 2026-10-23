"""Cost ESTIMATE for a proposed live evaluation. Nothing here calls a model, a search provider or the network.

Inputs: (1) token counts the provider RETURNED in earlier stored runs (model_calls, opened read-only), (2) a saved copy of the Mesh model
catalog with per-million-token prices. Output: expected and worst-case request counts and a CALCULATED cost. A calculated cost is not a
provider-reported charge; the two have differed in earlier runs, so treat this as an order of magnitude.
"""

import json
import sqlite3
from pathlib import Path


def measured_usage(db_files: list[Path]) -> dict:
    """{(agent, model): {calls, avg_prompt, avg_completion}} over stored calls that returned both token counts."""
    acc: dict[tuple[str, str], list[int]] = {}
    for f in db_files:
        conn = sqlite3.connect(f"file:{Path(f).as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT agent, model, prompt_tokens, completion_tokens FROM model_calls WHERE ok = 1 AND prompt_tokens IS NOT NULL"
                                " AND completion_tokens IS NOT NULL AND agent != ''").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        for agent, model, p, c in rows:
            a = acc.setdefault((agent, model), [0, 0, 0])
            a[0] += 1
            a[1] += p
            a[2] += c
    return {k: {"calls": v[0], "avg_prompt": v[1] / v[0], "avg_completion": v[2] / v[0]} for k, v in acc.items()}


def load_prices(catalog_path: Path) -> dict[str, dict]:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    return {m["id"]: {"prompt_usd_per_1m": m["pricing"]["prompt_usd_per_1m"], "completion_usd_per_1m": m["pricing"]["completion_usd_per_1m"]}
            for m in catalog if m.get("pricing") and m["pricing"].get("prompt_usd_per_1m") is not None}


def call_cost(usage: dict, prices: dict, model: str) -> float | None:
    if model not in prices:
        return None
    return (usage["avg_prompt"] * float(prices[model]["prompt_usd_per_1m"]) + usage["avg_completion"] * float(prices[model]["completion_usd_per_1m"])) / 1_000_000


def estimate(measured: dict, prices: dict, calls_by_agent: dict[str, int]) -> dict:
    """Calculated cost of `calls_by_agent` using, per agent, the measured average tokens of its most-used model. Agents with no measurement
    or no price are listed under `unpriced` and excluded from the total (the total is then a lower bound)."""
    per_agent, unpriced, total = {}, [], 0.0
    for agent, n in calls_by_agent.items():
        candidates = sorted(((v["calls"], m, v) for (a, m), v in measured.items() if a == agent), reverse=True)
        if not candidates:
            unpriced.append(f"{agent}: no measured usage")
            continue
        _, model, usage = candidates[0]
        cost = call_cost(usage, prices, model)
        if cost is None:
            unpriced.append(f"{agent}: no price for {model}")
            continue
        per_agent[agent] = {"model": model, "calls": n, "avg_prompt_tokens": round(usage["avg_prompt"]), "avg_completion_tokens": round(usage["avg_completion"]),
                            "usd_per_call": cost, "usd": cost * n}
        total += cost * n
    return {"per_agent": per_agent, "calculated_usd": total, "unpriced": unpriced, "is_lower_bound": bool(unpriced)}
