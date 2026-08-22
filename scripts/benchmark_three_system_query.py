#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.auth_manager import SystemType
from core.query_engine import QueryEngine


def summarize(values):
    ordered = sorted(round(float(value), 3) for value in values)
    if not ordered:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def run_query(engine, id_card, bypass_cache):
    cache_key = f"query:{id_card}"
    if bypass_cache:
        engine._query_cache.pop(cache_key, None)
    cache_hit = cache_key in engine._query_cache
    started = time.perf_counter()
    result = asyncio.run(engine.query_all(id_card, timeout=60))
    return {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "cache_hit": cache_hit,
        "found": bool(result.name),
        "sources": result.sources,
        "durations_ms": result.query_durations_ms,
        "phase_durations_ms": result.system_phase_durations_ms,
        "retry_counts": result.query_retry_counts,
    }


def run_scenario(engine, scenario, id_card, runs):
    records = []
    internal = engine._crawlers[SystemType.INTERNAL]
    for index in range(runs):
        if scenario == "stale-session":
            internal._logged_in = True
            internal._session_started_at = 0
        record = run_query(
            engine,
            id_card,
            bypass_cache=scenario != "repeat" or index == 0,
        )
        record.update({"scenario": scenario, "run": index + 1})
        records.append(record)
        print(json.dumps(record, ensure_ascii=False))
    return records


def main():
    parser = argparse.ArgumentParser(description="Read-only three-system query benchmark")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--scenarios",
        default="healthy,stale-session,repeat",
        help="Comma-separated: healthy,stale-session,repeat",
    )
    args = parser.parse_args()
    id_card = os.getenv("TEST_ID_CARD", "").strip()
    if not id_card:
        raise SystemExit("TEST_ID_CARD is required")

    engine = QueryEngine()
    try:
        for scenario in [item.strip() for item in args.scenarios.split(",") if item.strip()]:
            records = run_scenario(engine, scenario, id_card, args.runs)
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "summary": summarize([item["wall_seconds"] for item in records]),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        engine.close()


if __name__ == "__main__":
    main()
