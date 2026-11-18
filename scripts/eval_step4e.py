"""Step 4e comprehensive evaluation script covering Items 1 to 5."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.rag.condense import (
    _rewrite_heuristic,
    condense_query_detailed,
    is_self_contained,
)
from app.rag.retriever import retrieve
from scripts.rag_eval import _check_candidate_hits, _is_chunk_match
from tests.dev_followups import DEV_15_FOLLOWUPS
from tests.test_followups_v2 import FRESH_20_TEST_CASES_V2
from tests.realistic_histories import REALISTIC_12_HISTORIES
from scratch.run_step4c_eval import FRESH_15_TEST_CASES

CACHE_FILE = Path("eval_results/fair_rewrites_cache.json")
EVAL_YAML_PATH = Path("tests/rag_eval.yaml")


# 10 NEW Off-Topic Follow-up Questions following a policy conversation
NEW_10_OFFTOPIC_NEGATIVES: list[dict[str, Any]] = [
    {
        "id": "off_01",
        "question": "Can I bring my pet dog to office on work-from-office days?",
        "history": [
            {"role": "user", "content": "What is the company work from home policy?"},
            {"role": "assistant", "content": "Employees may work from home up to 2 days per week with manager approval."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_02",
        "question": "Is there a gym or fitness centre in the office basement?",
        "history": [
            {"role": "user", "content": "What are the core working hours at Acme Corp?"},
            {"role": "assistant", "content": "Core working hours are 10 AM to 4 PM IST."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_03",
        "question": "Can we order food delivery from Swiggy directly to our desk?",
        "history": [
            {"role": "user", "content": "How do I check my cafeteria meal card balance?"},
            {"role": "assistant", "content": "You can check your cafeteria balance on the employee portal."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_04",
        "question": "Is there a designated smoking zone on the office campus?",
        "history": [
            {"role": "user", "content": "What does the employee code of conduct say about harassment?"},
            {"role": "assistant", "content": "Harassment and discrimination are strictly prohibited under the code of conduct."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_05",
        "question": "Is there an office shuttle bus service from the nearest metro station?",
        "history": [
            {"role": "user", "content": "What are the daily working hours?"},
            {"role": "assistant", "content": "Standard office working hours are between 9 AM and 6 PM IST."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_06",
        "question": "Can I charge my electric scooter at the basement parking lot?",
        "history": [
            {"role": "user", "content": "How do I get a basement parking permit?"},
            {"role": "assistant", "content": "You need to obtain an RFID parking sticker from the security desk."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_07",
        "question": "Are we participating in the annual corporate cricket tournament this year?",
        "history": [
            {"role": "user", "content": "How many days of paid leave do full-time staff get?"},
            {"role": "assistant", "content": "Full-time employees receive 15 days of annual paid leave."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_08",
        "question": "Is there any special leave for attending school parent-teacher meetings?",
        "history": [
            {"role": "user", "content": "How many sick days are available per year?"},
            {"role": "assistant", "content": "Employees are entitled to 7 sick leave days annually."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_09",
        "question": "Does the office recreational game room have a table tennis table?",
        "history": [
            {"role": "user", "content": "Where is the IT walk-in helpdesk located?"},
            {"role": "assistant", "content": "The IT desk is located on Floor 2, Block B."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
    {
        "id": "off_10",
        "question": "Are sleeping pods available on floor 3 for taking power naps?",
        "history": [
            {"role": "user", "content": "Can I stay late in the office to finish project deliverables?"},
            {"role": "assistant", "content": "Employees must coordinate late stays with facility management."},
        ],
        "category": "negative_with_history",
        "role": "employee",
    },
]


def load_yaml_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Loads multi-turn, negative, and access cases from YAML."""
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    multis = [c for c in cases if c.get("category") == "multi_turn"]
    negs = [c for c in cases if c.get("category") in ("easy_negative", "hard_negative")]
    access = [c for c in cases if c.get("category") == "access"]
    return multis, negs, access


def load_cache() -> dict[str, dict[str, Any]]:
    """Loads fair run cache."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    """Saves fair run cache."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def run_fair_llm_rewrites(
    set_name: str,
    cases: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Runs LLM rewriter spaced >= 3 seconds apart with retry on 429."""
    settings.query_rewrite = True
    settings.rewrite_backend = "llm"
    settings.rewrite_model = "qwen/qwen3.8-27b"
    settings.rewrite_timeout_s = 5.0

    print(f"\n--- Fair LLM Run for {set_name} (N={len(cases)}) ---", flush=True)
    for idx, c in enumerate(cases, 1):
        cid = str(c["id"])
        key = f"{set_name}:{cid}"
        q = c["question"]
        hist = c.get("history", [])

        if key in cache:
            continue

        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                rw, status, dt = condense_query_detailed(q, hist)
                cache[key] = {
                    "id": cid,
                    "orig": q,
                    "rewritten": rw,
                    "status": status,
                    "latency_ms": dt,
                }
                print(f"  [{idx}/{len(cases)}] {cid}: status={status} ({dt:.1f}ms)", flush=True)
                save_cache(cache)
                # Enforce spacing >= 3s between calls
                time.sleep(3.1)
                break
            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str or "rate" in exc_str.lower():
                    print(f"  [429 Rate Limit] Backing off 10s for {cid}...", flush=True)
                    time.sleep(10.0)
                else:
                    cache[key] = {
                        "id": cid,
                        "orig": q,
                        "rewritten": q,
                        "status": "error",
                        "latency_ms": 0.0,
                    }
                    save_cache(cache)
                    break
    return cache


def eval_retrieval(
    query_str: str,
    case: dict[str, Any],
    original_query: str | None = None,
    score_threshold: float = 0.77,
) -> tuple[bool, bool, bool, int | None, list[Any]]:
    """Evaluates retrieval quality against case targets."""
    role = case.get("role", "employee")
    sources = case.get("expected_sources", [])
    phrase = str(case.get("expected_phrase", "")).strip()
    any_of = case.get("any_of")

    chunks = retrieve(
        query_str,
        role=role,
        score_threshold=score_threshold,
        mode="hierarchical",
        table_max_rows=4,
        top_k=5,
        hybrid_search=True,
        reranker=False,
        original_query=original_query,
    )
    t1, t3, r5, _ = _check_candidate_hits(chunks, sources, phrase, any_of)
    rank = None
    for idx, chunk in enumerate(chunks, 1):
        if _is_chunk_match(chunk, sources, phrase, any_of):
            rank = idx
            break
    return t1, t3, r5, rank, chunks


def benchmark_set_with_map(
    cases: list[dict[str, Any]],
    rewritten_map: dict[str, str],
    latencies: list[float],
    fallback_count: int,
    rewrite_mode: str = "replace",
) -> dict[str, Any]:
    """Evaluates retrieval across a dataset using a precomputed rewrite map."""
    t1, t3, r5 = 0, 0, 0
    ranks: list[int | None] = []
    n = len(cases)

    for c in cases:
        cid = str(c["id"])
        orig_q = c["question"]
        rw_q = rewritten_map.get(cid, orig_q)
        eval_q = rw_q
        orig_param = orig_q if rewrite_mode == "both" else None

        t1_h, t3_h, r5_h, rk, _ = eval_retrieval(eval_q, c, original_query=orig_param)
        if t1_h: t1 += 1
        if t3_h: t3 += 1
        if r5_h: r5 += 1
        ranks.append(rk)

    p50 = float(np.median(latencies)) if latencies else 0.0
    p95 = float(np.percentile(latencies, 95)) if latencies else 0.0
    fallback_share = (fallback_count / n * 100.0) if n > 0 else 0.0

    return {
        "n": n,
        "t1": t1,
        "t3": t3,
        "r5": r5,
        "t1_pct": t1 / n * 100.0,
        "t3_pct": t3 / n * 100.0,
        "r5_pct": r5 / n * 100.0,
        "fallback_share": fallback_share,
        "p50": p50,
        "p95": p95,
    }


def evaluate_heuristics_on_set(
    cases: list[dict[str, Any]],
    variant: str = "a",
    rewrite_mode: str = "replace",
) -> tuple[dict[str, str], list[float], int]:
    """Computes heuristic rewrites, latencies, and fallback count for a case list."""
    rw_map: dict[str, str] = {}
    latencies: list[float] = []
    fallbacks = 0
    for c in cases:
        cid = str(c["id"])
        q = c["question"]
        hist = c.get("history", [])
        t0 = time.perf_counter()
        rw = _rewrite_heuristic(q, hist, variant=variant)
        dt = (time.perf_counter() - t0) * 1000.0
        latencies.append(dt)
        rw_map[cid] = rw
        if rw == q:
            fallbacks += 1
    return rw_map, latencies, fallbacks


def run_item1_fair_llm(
    dev_cases: list[dict[str, Any]],
    test_v1: list[dict[str, Any]],
    test_v2: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> None:
    """Item 1: Fair LLM run, status verification, and comparative benchmark across sets."""
    print("\n" + "=" * 90)
    print("ITEM 1: FAIR LLM RUN & COMPARATIVE BENCHMARK (DEV, TEST V1, TEST V2)")
    print("=" * 90)

    # 1. Populate fair LLM rewrites
    cache = run_fair_llm_rewrites("dev", dev_cases, cache)
    cache = run_fair_llm_rewrites("test_v1", test_v1, cache)
    cache = run_fair_llm_rewrites("test_v2", test_v2, cache)

    # 2. Check rewrite_status distribution per set
    sets = [("Dev (N=38)", "dev", dev_cases), ("Test v1 (N=15)", "test_v1", test_v1), ("Test v2 (N=20)", "test_v2", test_v2)]
    print("\nRewrite Status Breakdown per Set (Fair LLM):")
    for label, prefix, c_list in sets:
        st_counts: dict[str, int] = {}
        for c in c_list:
            cid = str(c["id"])
            item = cache.get(f"{prefix}:{cid}", {})
            st = item.get("status", "unknown")
            st_counts[st] = st_counts.get(st, 0) + 1
        non_skipped = sum(v for k, v in st_counts.items() if not k.startswith("skipped_"))
        ok_count = st_counts.get("ok", 0)
        ok_rate = (ok_count / non_skipped * 100.0) if non_skipped > 0 else 100.0
        print(f"  {label:<18}: total={len(c_list)}, {st_counts} -> Non-skipped OK Rate: {ok_count}/{non_skipped} ({ok_rate:.1f}%)")

    # 3. Benchmark llm, heuristic, and llm_then_heuristic on all three sets
    print("\nRetrieval Quality Comparison Table (Dev, Test v1, Test v2):")
    print(f"{'Set':<14} | {'Backend':<20} | {'Top-1':<12} | {'Top-3':<12} | {'Recall@5':<12} | {'Fallback %':<10} | {'p50 (ms)':<9} | {'p95 (ms)':<9}")
    print("-" * 115)

    for label, prefix, c_list in sets:
        # LLM map & latencies
        llm_map: dict[str, str] = {}
        llm_lat: list[float] = []
        llm_fallbacks = 0
        for c in c_list:
            cid = str(c["id"])
            item = cache.get(f"{prefix}:{cid}", {})
            rw = item.get("rewritten", c["question"])
            llm_map[cid] = rw
            llm_lat.append(item.get("latency_ms", 0.0))
            if item.get("status") in ("skipped_self_contained", "skipped_no_history", "timeout", "rejected", "error"):
                llm_fallbacks += 1

        res_llm = benchmark_set_with_map(c_list, llm_map, llm_lat, llm_fallbacks)
        print(f"{label:<14} | {'llm':<20} | {res_llm['t1']}/{res_llm['n']} ({res_llm['t1_pct']:.1f}%) | {res_llm['t3']}/{res_llm['n']} ({res_llm['t3_pct']:.1f}%) | {res_llm['r5']}/{res_llm['n']} ({res_llm['r5_pct']:.1f}%) | {res_llm['fallback_share']:>8.1f}% | {res_llm['p50']:>7.1f}ms | {res_llm['p95']:>7.1f}ms")

        # Heuristic map
        heu_map, heu_lat, heu_fb = evaluate_heuristics_on_set(c_list, variant="a")
        res_heu = benchmark_set_with_map(c_list, heu_map, heu_lat, heu_fb)
        print(f"{label:<14} | {'heuristic (A)':<20} | {res_heu['t1']}/{res_heu['n']} ({res_heu['t1_pct']:.1f}%) | {res_heu['t3']}/{res_heu['n']} ({res_heu['t3_pct']:.1f}%) | {res_heu['r5']}/{res_heu['n']} ({res_heu['r5_pct']:.1f}%) | {res_heu['fallback_share']:>8.1f}% | {res_heu['p50']:>7.1f}ms | {res_heu['p95']:>7.1f}ms")

        # LLM then heuristic map
        lth_map: dict[str, str] = {}
        lth_lat: list[float] = []
        lth_fb = 0
        for c in c_list:
            cid = str(c["id"])
            item = cache.get(f"{prefix}:{cid}", {})
            st = item.get("status")
            if st == "ok":
                rw = item.get("rewritten", c["question"])
                lth_lat.append(item.get("latency_ms", 0.0))
            else:
                rw = _rewrite_heuristic(c["question"], c.get("history", []), variant="a")
                lth_lat.append(item.get("latency_ms", 0.0) + 0.1)
                lth_fb += 1
            lth_map[cid] = rw
        res_lth = benchmark_set_with_map(c_list, lth_map, lth_lat, lth_fb)
        print(f"{label:<14} | {'llm_then_heuristic':<20} | {res_lth['t1']}/{res_lth['n']} ({res_lth['t1_pct']:.1f}%) | {res_lth['t3']}/{res_lth['n']} ({res_lth['t3_pct']:.1f}%) | {res_lth['r5']}/{res_lth['n']} ({res_lth['r5_pct']:.1f}%) | {res_lth['fallback_share']:>8.1f}% | {res_lth['p50']:>7.1f}ms | {res_lth['p95']:>7.1f}ms")
        print("-" * 115)


def run_item2_realistic_histories(
    realistic_cases: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> None:
    """Item 2: Benchmark realistic histories across off, llm, heuristic, and llm_then_heuristic."""
    print("\n" + "=" * 90)
    print("ITEM 2: REALISTIC HISTORIES (12 CASES WITH DISTANT / ASST-ONLY TOPICS)")
    print("=" * 90)

    cache = run_fair_llm_rewrites("realistic", realistic_cases, cache)

    # 1. off
    off_map = {str(c["id"]): c["question"] for c in realistic_cases}
    res_off = benchmark_set_with_map(realistic_cases, off_map, [0.0]*len(realistic_cases), 0)

    # 2. llm
    llm_map: dict[str, str] = {}
    llm_lat: list[float] = []
    llm_fb = 0
    for c in realistic_cases:
        cid = str(c["id"])
        item = cache.get(f"realistic:{cid}", {})
        llm_map[cid] = item.get("rewritten", c["question"])
        llm_lat.append(item.get("latency_ms", 0.0))
        if item.get("status") != "ok": llm_fb += 1
    res_llm = benchmark_set_with_map(realistic_cases, llm_map, llm_lat, llm_fb)

    # 3. heuristic (A)
    heu_map, heu_lat, heu_fb = evaluate_heuristics_on_set(realistic_cases, variant="a")
    res_heu = benchmark_set_with_map(realistic_cases, heu_map, heu_lat, heu_fb)

    # 4. llm_then_heuristic
    lth_map = {}
    lth_lat = []
    lth_fb = 0
    for c in realistic_cases:
        cid = str(c["id"])
        item = cache.get(f"realistic:{cid}", {})
        if item.get("status") == "ok":
            rw = item.get("rewritten", c["question"])
            lth_lat.append(item.get("latency_ms", 0.0))
        else:
            rw = heu_map[cid]
            lth_lat.append(item.get("latency_ms", 0.0) + 0.1)
            lth_fb += 1
        lth_map[cid] = rw
    res_lth = benchmark_set_with_map(realistic_cases, lth_map, lth_lat, lth_fb)

    print(f"\nRealistic Histories Retrieval Results (N={len(realistic_cases)}):")
    print(f"  QUERY_REWRITE=off        : Top-1 = {res_off['t1']}/{len(realistic_cases)} ({res_off['t1_pct']:.1f}%), Top-3 = {res_off['t3']}/{len(realistic_cases)} ({res_off['t3_pct']:.1f}%), Recall@5 = {res_off['r5']}/{len(realistic_cases)} ({res_off['r5_pct']:.1f}%)")
    print(f"  llm (fair run)           : Top-1 = {res_llm['t1']}/{len(realistic_cases)} ({res_llm['t1_pct']:.1f}%), Top-3 = {res_llm['t3']}/{len(realistic_cases)} ({res_llm['t3_pct']:.1f}%), Recall@5 = {res_llm['r5']}/{len(realistic_cases)} ({res_llm['r5_pct']:.1f}%)")
    print(f"  heuristic (Variant A)    : Top-1 = {res_heu['t1']}/{len(realistic_cases)} ({res_heu['t1_pct']:.1f}%), Top-3 = {res_heu['t3']}/{len(realistic_cases)} ({res_heu['t3_pct']:.1f}%), Recall@5 = {res_heu['r5']}/{len(realistic_cases)} ({res_heu['r5_pct']:.1f}%)")
    print(f"  llm_then_heuristic       : Top-1 = {res_lth['t1']}/{len(realistic_cases)} ({res_lth['t1_pct']:.1f}%), Top-3 = {res_lth['t3']}/{len(realistic_cases)} ({res_lth['t3_pct']:.1f}%), Recall@5 = {res_lth['r5']}/{len(realistic_cases)} ({res_lth['r5_pct']:.1f}%)")

    print("\nCase-by-Case Breakdown on Realistic Histories:")
    for c in realistic_cases:
        cid = str(c["id"])
        orig_q = c["question"]
        rw_llm = llm_map.get(cid, orig_q)
        rw_heu = heu_map.get(cid, orig_q)
        _, _, r5_off, rk_off, _ = eval_retrieval(orig_q, c)
        _, _, r5_llm, rk_llm, _ = eval_retrieval(rw_llm, c)
        _, _, r5_heu, rk_heu, _ = eval_retrieval(rw_heu, c)
        print(f"  [{cid}] ranks: off={rk_off} | llm={rk_llm} | heu={rk_heu}")
        print(f"         q: '{orig_q[:40]}'")
        print(f"         llm: '{rw_llm[:50]}'")
        print(f"         heu: '{rw_heu[:50]}'")


def run_item3_heuristic_variants(
    dev_cases: list[dict[str, Any]],
    test_v1: list[dict[str, Any]],
    test_v2: list[dict[str, Any]],
) -> None:
    """Item 3: Evaluate heuristic variants A, B, C, D, E on Dev + Negatives, then confirm on v1/v2."""
    print("\n" + "=" * 90)
    print("ITEM 3: HEURISTIC VARIANTS TUNING (A, B, C, D, E)")
    print("=" * 90)

    variants = [
        ("A: ref OR <5 content terms", "a", "replace"),
        ("B: ref marker only", "b", "replace"),
        ("C: ref OR <=2 content terms", "c", "replace"),
        ("D: C + last assistant nouns", "d", "replace"),
        ("E: C in REWRITE_MODE=both", "c", "both"),
    ]

    print("\n--- Tuning on Dev Set (N=38) ---")
    print(f"{'Variant Description':<32} | {'Top-1':<12} | {'Top-3':<12} | {'Recall@5':<12} | {'Fallback %':<10}")
    print("-" * 85)
    for desc, var_code, mode in variants:
        rw_map, lat, fb = evaluate_heuristics_on_set(dev_cases, variant=var_code, rewrite_mode=mode)
        res = benchmark_set_with_map(dev_cases, rw_map, lat, fb, rewrite_mode=mode)
        print(f"{desc:<32} | {res['t1']}/{res['n']} ({res['t1_pct']:.1f}%) | {res['t3']}/{res['n']} ({res['t3_pct']:.1f}%) | {res['r5']}/{res['n']} ({res['r5_pct']:.1f}%) | {res['fallback_share']:>8.1f}%")

    print("\n--- Confirmation on Test v1 (N=15) ---")
    print(f"{'Variant Description':<32} | {'Top-1':<12} | {'Top-3':<12} | {'Recall@5':<12} | {'Fallback %':<10}")
    print("-" * 85)
    for desc, var_code, mode in variants:
        rw_map, lat, fb = evaluate_heuristics_on_set(test_v1, variant=var_code, rewrite_mode=mode)
        res = benchmark_set_with_map(test_v1, rw_map, lat, fb, rewrite_mode=mode)
        print(f"{desc:<32} | {res['t1']}/{res['n']} ({res['t1_pct']:.1f}%) | {res['t3']}/{res['n']} ({res['t3_pct']:.1f}%) | {res['r5']}/{res['n']} ({res['r5_pct']:.1f}%) | {res['fallback_share']:>8.1f}%")

    print("\n--- Confirmation on Test v2 (N=20) ---")
    print(f"{'Variant Description':<32} | {'Top-1':<12} | {'Top-3':<12} | {'Recall@5':<12} | {'Fallback %':<10}")
    print("-" * 85)
    for desc, var_code, mode in variants:
        rw_map, lat, fb = evaluate_heuristics_on_set(test_v2, variant=var_code, rewrite_mode=mode)
        res = benchmark_set_with_map(test_v2, rw_map, lat, fb, rewrite_mode=mode)
        print(f"{desc:<32} | {res['t1']}/{res['n']} ({res['t1_pct']:.1f}%) | {res['t3']}/{res['n']} ({res['t3_pct']:.1f}%) | {res['r5']}/{res['n']} ({res['r5_pct']:.1f}%) | {res['fallback_share']:>8.1f}%")


def run_item4_negatives_and_gates(
    negatives_yaml: list[dict[str, Any]],
    access_cases: list[dict[str, Any]],
    new_offtopic: list[dict[str, Any]],
) -> None:
    """Item 4: Negatives with history & Gate Choice 1 vs Gate Choice 2."""
    print("\n" + "=" * 90)
    print("ITEM 4: NEGATIVES WITH HISTORY & GATE EVALUATION (N=40)")
    print("=" * 90)

    all_negatives = negatives_yaml + new_offtopic  # 20 + 10 = 30 negatives
    # 10 access questions
    all_eval_negs = all_negatives + access_cases  # total 40

    variants = [
        ("OFF", "off", "replace"),
        ("Variant A (current)", "a", "replace"),
        ("Variant B (ref only)", "b", "replace"),
        ("Variant C (<=2 words)", "c", "replace"),
        ("Variant D (+asst nouns)", "d", "replace"),
        ("Variant E (C in both)", "c", "both"),
    ]

    print("\nGate Choice 1: Gate applied to Enriched Query Score")
    print("Gate Choice 2: Gate applied to Original Query Score")
    print(f"{'Variant':<25} | {'Gate Choice 1 Rejected':<24} | {'Gate Choice 2 Rejected':<24} | {'Access Leaks (N=10)':<18}")
    print("-" * 100)

    for v_name, v_code, mode in variants:
        g1_rejected = 0
        g2_rejected = 0
        access_leaks = 0

        for c in all_negatives:
            q = c["question"]
            hist = c.get("history", [])
            rw = q if v_code == "off" else _rewrite_heuristic(q, hist, variant=v_code)

            # Gate Choice 1: evaluate on rewritten query
            orig_p1 = q if mode == "both" else None
            _, _, _, _, chunks_g1 = eval_retrieval(rw, c, original_query=orig_p1, score_threshold=0.77)
            if len(chunks_g1) == 0:
                g1_rejected += 1

            # Gate Choice 2: gate checked on original query
            _, _, _, _, chunks_orig = eval_retrieval(q, c, score_threshold=0.77)
            if len(chunks_orig) == 0:
                g2_rejected += 1
            else:
                # If original passed, check enriched
                if len(chunks_g1) == 0:
                    g2_rejected += 1

        # Check access cases
        for ac in access_cases:
            q = ac["question"]
            hist = ac.get("history", [])
            rw = q if v_code == "off" else _rewrite_heuristic(q, hist, variant=v_code)
            orig_p = q if mode == "both" else None
            _, _, _, _, chs = eval_retrieval(rw, ac, original_query=orig_p, score_threshold=0.77)
            if any(ch.source == "hr_benefits.md" for ch in chs):
                access_leaks += 1

        total_negs = len(all_negatives)
        print(f"{v_name:<25} | {g1_rejected}/{total_negs} ({g1_rejected/total_negs*100:.1f}%) | {g2_rejected}/{total_negs} ({g2_rejected/total_negs*100:.1f}%) | {access_leaks}/{len(access_cases)}")


def run_item5_keyword_stubs(cache: dict[str, dict[str, Any]]) -> None:
    """Item 5: Keyword stubs evaluation across off, C, E, and LLM replace/both."""
    print("\n" + "=" * 90)
    print("ITEM 5: KEYWORD STUBS WITH FAKE HISTORY")
    print("=" * 90)

    stubs = [
        {"id": "stub_01", "question": "VPN", "expected_sources": ["it_policies.md"], "expected_phrase": "globalprotect"},
        {"id": "stub_02", "question": "SLA P1", "expected_sources": ["it_policies.md"], "expected_phrase": "2 hours sla"},
        {"id": "stub_03", "question": "Section 2.3", "expected_sources": ["employee_handbook.md"], "expected_phrase": "10 am"},
        {"id": "stub_04", "question": "pasword requirments", "expected_sources": ["it_policies.md"], "expected_phrase": "12 characters"},
        {"id": "stub_05", "question": "probaton perod", "expected_sources": ["employee_handbook.md", "leave_and_notice_policy.md"], "expected_phrase": "90"},
    ]
    fake_history = [
        {"role": "user", "content": "How many days of paid leave can I carry forward into next year?"},
        {"role": "assistant", "content": "You can carry forward up to a maximum of 10 days of paid leave."},
    ]
    for s in stubs:
        s["history"] = fake_history
        s["role"] = "employee"

    # Pre-populate LLM cache for stubs
    cache = run_fair_llm_rewrites("stubs", stubs, cache)

    print(f"{'Stub ID':<8} | {'Original Stub':<20} | {'Method':<16} | {'Rewritten Query':<40} | {'Tokens Preserved':<16} | {'Rank':<6}")
    print("-" * 120)

    for s in stubs:
        cid = str(s["id"])
        q = s["question"]

        # OFF
        _, _, _, rk_off, _ = eval_retrieval(q, s)
        print(f"{cid:<8} | {q:<20} | {'off':<16} | {q:<40} | {'YES':<16} | {str(rk_off):<6}")

        # Heuristic Variant C
        rw_c = _rewrite_heuristic(q, fake_history, variant="c")
        _, _, _, rk_c, _ = eval_retrieval(rw_c, s)
        c_pres = "YES" if all(w in rw_c.lower() for w in q.lower().split()) else "NO"
        print(f"{cid:<8} | {q:<20} | {'heuristic (C)':<16} | {rw_c:<40} | {c_pres:<16} | {str(rk_c):<6}")

        # Heuristic Variant E (both)
        _, _, _, rk_e, _ = eval_retrieval(rw_c, s, original_query=q)
        print(f"{cid:<8} | {q:<20} | {'heuristic (E both)':<16} | {rw_c:<40} | {'YES (fused)':<16} | {str(rk_e):<6}")

        # LLM replace
        llm_item = cache.get(f"stubs:{cid}", {})
        rw_llm = llm_item.get("rewritten", q)
        _, _, _, rk_llm_rep, _ = eval_retrieval(rw_llm, s)
        llm_pres = "YES" if all(w in rw_llm.lower() for w in q.lower().split()) else "NO (drifted)"
        print(f"{cid:<8} | {q:<20} | {'llm (replace)':<16} | {rw_llm:<40} | {llm_pres:<16} | {str(rk_llm_rep):<6}")

        # LLM both
        _, _, _, rk_llm_both, _ = eval_retrieval(rw_llm, s, original_query=q)
        print(f"{cid:<8} | {q:<20} | {'llm (both)':<16} | {rw_llm:<40} | {'YES (fused)':<16} | {str(rk_llm_both):<6}")
        print("-" * 120)


def main() -> None:
    old_multis, negatives, access_cases = load_yaml_cases()
    dev_cases = old_multis + DEV_15_FOLLOWUPS
    test_v1 = FRESH_15_TEST_CASES
    test_v2 = FRESH_20_TEST_CASES_V2
    cache = load_cache()

    # Item 1
    run_item1_fair_llm(dev_cases, test_v1, test_v2, cache)

    # Item 2
    run_item2_realistic_histories(REALISTIC_12_HISTORIES, cache)

    # Item 3
    run_item3_heuristic_variants(dev_cases, test_v1, test_v2)

    # Item 4
    run_item4_negatives_and_gates(negatives, access_cases, NEW_10_OFFTOPIC_NEGATIVES)

    # Item 5
    run_item5_keyword_stubs(cache)

    print("\n[SUCCESS] Step 4e evaluations completed successfully!")


if __name__ == "__main__":
    main()

