"""Step 4 evaluation comparing retrieval quality with QUERY_REWRITE off vs on."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import yaml

from app.config import settings
from app.rag.condense import condense_query
from app.rag.retriever import retrieve
from scripts.rag_eval import _check_candidate_hits

EVAL_YAML_PATH = Path("tests/rag_eval.yaml")
USER_FOLLOWUPS_PATH = Path("tests/user_followups.yaml")
CACHE_FILE = Path("eval_results/rewritten_queries.json")


def load_user_followups() -> list[dict[str, Any]]:
    """Loads custom user follow-up questions from tests/user_followups.yaml if present."""
    if not USER_FOLLOWUPS_PATH.exists():
        return []
    try:
        with USER_FOLLOWUPS_PATH.open("r", encoding="utf-8") as f:
            cases = yaml.safe_load(f) or []
            return [c for c in cases if isinstance(c, dict) and "question" in c]
    except Exception:
        return []


def load_multiturn_cases() -> list[dict[str, Any]]:
    """Loads all 23 multi-turn benchmark cases from tests/rag_eval.yaml."""
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    return [c for c in cases if c.get("category") == "multi_turn"]


def load_rewrite_cache() -> dict[str, str]:
    """Loads cached query rewrites to prevent redundant LLM invocations."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_rewrite_cache(cache: dict[str, str]) -> None:
    """Persists rewritten query mapping to local JSON cache."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def rewrite_all_cases(
    cases: list[dict[str, Any]],
    cache: dict[str, str],
) -> tuple[dict[str, str], int, int]:
    """Rewrites queries using live LLM or cache with strict 60-call safety cap."""
    settings.query_rewrite = True
    llm_calls = 0
    approx_tokens = 0

    for c in cases:
        cid = str(c["id"])
        q = c["question"]
        hist = c.get("history", [])

        if cid in cache:
            continue

        if llm_calls >= 60:
            cache[cid] = q
            continue

        rewritten = condense_query(q, history=hist)
        llm_calls += 1
        # Approx token calculation: ~150 prompt tokens + length of output
        approx_tokens += 150 + len(rewritten.split())
        cache[cid] = rewritten

    save_rewrite_cache(cache)
    return cache, llm_calls, approx_tokens


def evaluate_query(
    query_str: str,
    case: dict[str, Any],
) -> tuple[bool, bool, bool, int | None]:
    """Runs retrieval for a query string and calculates rank and hit indicators."""
    role = case.get("role", "employee")
    sources = case.get("expected_sources", [])
    phrase = str(case.get("expected_phrase", "")).strip().lower()
    any_of = case.get("any_of")

    chunks = retrieve(
        query_str,
        role=role,
        score_threshold=0.77,
        mode="hierarchical",
        table_max_rows=4,
        top_k=5,
        hybrid_search=True,
        reranker=False,
    )
    t1, t3, r5, _ = _check_candidate_hits(chunks, sources, phrase, any_of)

    from scripts.rag_eval import _is_chunk_match
    rank = None
    for idx, chunk in enumerate(chunks, 1):
        if _is_chunk_match(chunk, sources, phrase, any_of):
            rank = idx
            break
    return t1, t3, r5, rank


def run_comparison(
    cases: list[dict[str, Any]],
    rewritten_map: dict[str, str],
) -> None:
    """Compares OFF vs ON retrieval quality on multi-turn and Hinglish follow-ups."""
    off_t1, off_t3, off_r5 = 0, 0, 0
    on_t1, on_t3, on_r5 = 0, 0, 0
    worse_cases: list[dict[str, Any]] = []

    print("\n=======================================================", flush=True)
    print("STEP 4: QUERY REWRITING COMPARISON (23 Multi-Turn Cases)", flush=True)
    print("=======================================================", flush=True)

    for c in cases:
        cid = str(c["id"])
        orig_q = c["question"]
        rewritten_q = rewritten_map.get(cid, orig_q)

        t1_off, t3_off, r5_off, rank_off = evaluate_query(orig_q, c)
        t1_on, t3_on, r5_on, rank_on = evaluate_query(rewritten_q, c)

        if t1_off: off_t1 += 1
        if t3_off: off_t3 += 1
        if r5_off: off_r5 += 1

        if t1_on: on_t1 += 1
        if t3_on: on_t3 += 1
        if r5_on: on_r5 += 1

        # Check if rank got worse (rank_off was better than rank_on)
        is_worse = False
        if rank_off is not None:
            if rank_on is None or rank_on > rank_off:
                is_worse = True
        if is_worse:
            worse_cases.append({
                "id": cid,
                "orig": orig_q,
                "rewritten": rewritten_q,
                "rank_off": rank_off,
                "rank_on": rank_on,
            })

    n = len(cases)
    print(f"Overall Multi-Turn (N={n}):", flush=True)
    print(f"  QUERY_REWRITE=off : Top-1 = {off_t1}/{n} ({off_t1/n*100.1:.1f}%), Top-3 = {off_t3}/{n} ({off_t3/n*100.1:.1f}%), Recall@5 = {off_r5}/{n} ({off_r5/n*100.1:.1f}%)", flush=True)
    print(f"  QUERY_REWRITE=on  : Top-1 = {on_t1}/{n} ({on_t1/n*100.1:.1f}%), Top-3 = {on_t3}/{n} ({on_t3/n*100.1:.1f}%), Recall@5 = {on_r5}/{n} ({on_r5/n*100.1:.1f}%)", flush=True)

    # Hinglish subset (multi_16 through multi_20)
    hinglish_cases = [c for c in cases if str(c.get("id")) in ("multi_16", "multi_17", "multi_18", "multi_19", "multi_20")]
    h_n = len(hinglish_cases)
    if h_n > 0:
        h_off_t1, h_off_t3, h_off_r5 = 0, 0, 0
        h_on_t1, h_on_t3, h_on_r5 = 0, 0, 0
        for c in hinglish_cases:
            cid = str(c["id"])
            t1_off, t3_off, r5_off, _ = evaluate_query(c["question"], c)
            t1_on, t3_on, r5_on, _ = evaluate_query(rewritten_map.get(cid, c["question"]), c)
            if t1_off: h_off_t1 += 1
            if t3_off: h_off_t3 += 1
            if h_off_r5: h_off_r5 += 1
            if t1_on: h_on_t1 += 1
            if t3_on: h_on_t3 += 1
            if h_on_r5: h_on_r5 += 1
        print(f"\nHinglish Follow-ups Subset (N={h_n}):", flush=True)
        print(f"  QUERY_REWRITE=off : Top-1 = {h_off_t1}/{h_n} ({h_off_t1/h_n*100.1:.1f}%), Top-3 = {h_off_t3}/{h_n} ({h_off_t3/h_n*100.1:.1f}%)", flush=True)
        print(f"  QUERY_REWRITE=on  : Top-1 = {h_on_t1}/{h_n} ({h_on_t1/h_n*100.1:.1f}%), Top-3 = {h_on_t3}/{h_n} ({h_on_t3/h_n*100.1:.1f}%)", flush=True)

    print(f"\nCases That Got Worse: {len(worse_cases)}", flush=True)
    for w in worse_cases:
        print(f"  - Case {w['id']}: '{w['orig']}' -> '{w['rewritten']}' (Rank off: {w['rank_off']} -> Rank on: {w['rank_on']})", flush=True)


def main() -> None:
    cases = load_multiturn_cases()
    cache = load_rewrite_cache()

    print(f"Loaded {len(cases)} multi-turn cases. Rewriting queries with LLM provider={settings.llm_provider}...", flush=True)
    rewritten_map, calls_made, tokens_used = rewrite_all_cases(cases, cache)
    print(f"LLM Calls made: {calls_made}, Approx tokens used: {tokens_used}", flush=True)

    print("\n-------------------------------------------------------", flush=True)
    print("ORIGINAL FOLLOW-UP vs REWRITTEN QUERY:", flush=True)
    print("-------------------------------------------------------", flush=True)
    for c in cases:
        cid = str(c["id"])
        print(f"[{cid}] Original: '{c['question']}'\n       Rewritten: '{rewritten_map[cid]}'", flush=True)

    run_comparison(cases, rewritten_map)

    user_cases = load_user_followups()
    if user_cases:
        print("\n=======================================================", flush=True)
        print(f"USER FOLLOW-UPS GROUP ({len(user_cases)} Cases from tests/user_followups.yaml)", flush=True)
        print("=======================================================", flush=True)
        user_cache, _, _ = rewrite_all_cases(user_cases, cache)
        run_comparison(user_cases, user_cache)


if __name__ == "__main__":
    main()
