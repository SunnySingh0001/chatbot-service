"""Evaluation script for implicit topic-switch cases comparing Variant C, Variant F, and llm_then_heuristic."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.rag.condense import (
    _rewrite_heuristic,
    condense_query_detailed,
    is_code_stub,
    is_topic_switch,
)
from app.rag.retriever import retrieve
from scripts.rag_eval import _check_candidate_hits, _is_chunk_match
from tests.topic_switch_implicit import IMPLICIT_TOPIC_SWITCH_CASES

CACHE_FILE = Path("eval_results/fair_rewrites_cache.json")


def load_fair_cache() -> dict[str, dict[str, Any]]:
    """Loads cached rewrites from Step 4e fair evaluation if available."""
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def eval_retrieval(
    query_str: str,
    case: dict[str, Any],
    score_threshold: float = 0.80,
) -> tuple[bool, bool, bool, int | None]:
    """Evaluates retrieval against target sources and phrase."""
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
    )
    t1, t3, r5, _ = _check_candidate_hits(chunks, sources, phrase, any_of)
    rank = None
    for idx, chunk in enumerate(chunks, 1):
        if _is_chunk_match(chunk, sources, phrase, any_of):
            rank = idx
            break
    return t1, t3, r5, rank


def run_evaluation() -> None:
    """Runs evaluation across all 10 implicit topic-switch cases for C, F, and llm_then_heuristic."""
    print("=" * 125)
    print("IMPLICIT TOPIC-SWITCH EVALUATION (N=10) WITHOUT EXPLICIT TRANSITION MARKERS")
    print("=" * 125)

    cache = load_fair_cache()

    results: list[dict[str, Any]] = []

    for c in IMPLICIT_TOPIC_SWITCH_CASES:
        cid = c["id"]
        q = c["question"]
        hist = c["history"]

        # 1. Topic switch status
        ts_flag = is_topic_switch(q)
        code_stub_flag = is_code_stub(q)

        # 2. Variant C
        rw_c = _rewrite_heuristic(q, hist, variant="c")
        t1_c, t3_c, r5_c, rk_c = eval_retrieval(rw_c, c)

        # 3. Variant F
        rw_f = _rewrite_heuristic(q, hist, variant="f")
        t1_f, t3_f, r5_f, rk_f = eval_retrieval(rw_f, c)

        # 4. llm_then_heuristic
        with patch.object(settings, "query_rewrite", True):
            rw_llm, status_llm, _ = condense_query_detailed(q, hist)
        t1_llm, t3_llm, r5_llm, rk_llm = eval_retrieval(rw_llm, c)

        # Detect if old topic words were appended
        old_topic_words_added = rw_c != q

        results.append({
            "id": cid,
            "question": q,
            "is_topic_switch": ts_flag,
            "is_code_stub": code_stub_flag,
            "old_topic_added": old_topic_words_added,
            "rw_c": rw_c,
            "rk_c": rk_c,
            "rw_f": rw_f,
            "rk_f": rk_f,
            "rw_llm": rw_llm,
            "status_llm": status_llm,
            "rk_llm": rk_llm,
            "t1_c": t1_c,
            "t3_c": t3_c,
            "t1_f": t1_f,
            "t3_f": t3_f,
            "t1_llm": t1_llm,
            "t3_llm": t3_llm,
        })

    print(f"\n{'ID':<12} | {'Question':<26} | {'is_topic_switch':<15} | {'Variant C Query':<45} | {'Rank C':<7} | {'Variant F Query':<45} | {'Rank F':<7}")
    print("-" * 165)
    for r in results:
        rk_c_str = str(r["rk_c"]) if r["rk_c"] is not None else "miss"
        rk_f_str = str(r["rk_f"]) if r["rk_f"] is not None else "miss"
        print(f"{r['id']:<12} | {r['question']:<26} | {str(r['is_topic_switch']):<15} | {r['rw_c'][:43]:<45} | {rk_c_str:<7} | {r['rw_f'][:43]:<45} | {rk_f_str:<7}")

    print("\n" + "=" * 125)
    print("LLM_THEN_HEURISTIC COMPARISON")
    print("=" * 125)
    print(f"{'ID':<12} | {'Question':<26} | {'Rewrite Status':<22} | {'Rewritten Search Query':<50} | {'Rank LLM'}")
    print("-" * 125)
    for r in results:
        rk_llm_str = str(r["rk_llm"]) if r["rk_llm"] is not None else "miss"
        print(f"{r['id']:<12} | {r['question']:<26} | {r['status_llm']:<22} | {r['rw_llm'][:48]:<50} | {rk_llm_str}")

    # Summary table
    n = len(results)
    t1_c_total = sum(1 for r in results if r["t1_c"])
    t3_c_total = sum(1 for r in results if r["t3_c"])

    t1_f_total = sum(1 for r in results if r["t1_f"])
    t3_f_total = sum(1 for r in results if r["t3_f"])

    t1_llm_total = sum(1 for r in results if r["t1_llm"])
    t3_llm_total = sum(1 for r in results if r["t3_llm"])

    print("\n" + "=" * 65)
    print("SUMMARY RESULTS (N=10 Implicit Topic Switches)")
    print("=" * 65)
    print(f"{'Method':<24} | {'Top-1':<15} | {'Top-3':<15}")
    print("-" * 65)
    print(f"{'Variant C':<24} | {t1_c_total:>2}/{n} ({t1_c_total/n*100:>5.1f}%) | {t3_c_total:>2}/{n} ({t3_c_total/n*100:>5.1f}%)")
    print(f"{'Variant F':<24} | {t1_f_total:>2}/{n} ({t1_f_total/n*100:>5.1f}%) | {t3_f_total:>2}/{n} ({t3_f_total/n*100:>5.1f}%)")
    print(f"{'llm_then_heuristic':<24} | {t1_llm_total:>2}/{n} ({t1_llm_total/n*100:>5.1f}%) | {t3_llm_total:>2}/{n} ({t3_llm_total/n*100:>5.1f}%)")

    # Plain report of which switches the heuristic wrongly enriched
    print("\n" + "=" * 125)
    print("ANALYSIS: WHICH SWITCHES THE HEURISTIC WRONGLY ENRICHED WITH OLD TOPIC TERMS")
    print("=" * 125)
    wrongly_enriched = [r for r in results if r["old_topic_added"]]
    print(f"Total cases where heuristic appended old topic words: {len(wrongly_enriched)} / {n}\n")
    for r in wrongly_enriched:
        print(f"- Case [{r['id']}]: \"{r['question']}\"")
        print(f"  * is_topic_switch: {r['is_topic_switch']}")
        print(f"  * Enriched query: \"{r['rw_c']}\"")
        print(f"  * Cause: The question did not match current topic switch rules because either:")
        if "wifi" in r["question"].lower():
            print("    -> 'wifi' is written as an unhyphenated single word, whereas the corpus has 'wi-fi', and 'wifi' is <5 characters so edit distance matching was not permitted.")
        elif "laptop kharab" in r["question"].lower():
            print("    -> 'kharab' is a colloquial Hindi verb/adjective absent from the English policy vocabulary; only 1 vocabulary term ('laptop') remained (<2 required).")
        elif "wfh policy kya hai" in r["question"].lower():
            print("    -> Length is 4 words ('wfh policy kya hai?'), exceeding the strict cap of 3 words (len(words) > 3).")
        elif "naya software" in r["question"].lower():
            print("    -> Length is 4 words ('naya software install karna'), exceeding the strict cap of 3 words (len(words) > 3).")
        print()


if __name__ == "__main__":
    run_evaluation()
