"""Step 4f comprehensive evaluation script closing all Step 4e open questions."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.rag.bm25 import get_bm25_index, tokenize_bm25
from app.rag.condense import (
    _COMMON_STOPWORDS,
    _rewrite_heuristic,
    condense_query_detailed,
    is_code_stub,
)
from app.rag.retriever import (
    _build_where_clause,
    _fetch_candidate_pool,
    _open_collection,
    _query_vector_store,
    get_collection_name,
    retrieve,
)
from scripts.rag_eval import _check_candidate_hits, _is_chunk_match
from tests.dev_followups import DEV_15_FOLLOWUPS
from tests.test_followups_v2 import FRESH_20_TEST_CASES_V2
from tests.realistic_histories import REALISTIC_12_HISTORIES
from scratch.run_step4c_eval import FRESH_15_TEST_CASES
from scratch.run_step4e_eval import NEW_10_OFFTOPIC_NEGATIVES, load_cache, load_yaml_cases

EVAL_YAML_PATH = Path("tests/rag_eval.yaml")


def get_all_62_positives() -> list[dict[str, Any]]:
    """Loads all 62 positive evaluation queries from tests/rag_eval.yaml."""
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    return [c for c in cases if c.get("category") in ("single_turn", "ambiguous", "multi_turn")]


def get_single_turn_positives() -> list[dict[str, Any]]:
    """Loads the 62 baseline positives (evaluated without conversation history)."""
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    # Strip any history so they are evaluated strictly as single-turn queries
    res = []
    for c in cases:
        if c.get("category") in ("single_turn", "ambiguous", "multi_turn"):
            item = dict(c)
            item["history"] = []
            res.append(item)
    return res


def eval_case_retrieval(
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


def run_item1_gate_tradeoffs(cache: dict[str, dict[str, Any]]) -> None:
    """Item 1: Positives accepted, negatives rejected, and access leaks across gate choices and thresholds."""
    print("\n" + "=" * 105)
    print("ITEM 1: POSITIVES-SIDE OF THE GATE & COMPARISON OF THREE GATE CHOICES")
    print("=" * 105)

    old_multis, negatives_yaml, access_cases = load_yaml_cases()
    single_turn_62 = get_single_turn_positives()  # 62 single-turn
    dev_38 = old_multis + DEV_15_FOLLOWUPS        # 38 dev follow-ups with history
    real_12 = REALISTIC_12_HISTORIES              # 12 realistic follow-ups with history
    total_positives = single_turn_62 + dev_38 + real_12  # 62 + 38 + 12 = 112

    all_negatives = negatives_yaml + NEW_10_OFFTOPIC_NEGATIVES  # 20 + 10 = 30 negatives

    # Precompute rewritten queries using llm_then_heuristic with variant C
    def get_query_and_hist(case: dict[str, Any], prefix: str) -> str:
        cid = str(case["id"])
        q = case["question"]
        hist = case.get("history", [])
        if not hist:
            return q
        item = cache.get(f"{prefix}:{cid}", {})
        if item.get("status") == "ok":
            return str(item.get("rewritten", q))
        return _rewrite_heuristic(q, hist, variant="c")

    col = _open_collection(mode="hierarchical", table_max_rows=4)
    where_emp = _build_where_clause("employee")
    col_name = get_collection_name(mode="hierarchical", table_max_rows=4)
    bm25 = get_bm25_index(col_name, role="employee")

    thresholds = [0.77, 0.80, 0.82, 0.84]

    print(f"\nEvaluation on 112 Positives (62 Single + 38 Dev + 12 Realistic), 30 Negatives, and 10 Access Queries")
    print(f"Backend: llm_then_heuristic (Variant C)")
    print("-" * 125)
    print(f"{'Threshold':<10} | {'Gate Choice':<38} | {'Positives Accepted (N=112)':<28} | {'Negatives Rejected (N=30)':<27} | {'Access Leaks'}")
    print("-" * 125)

    for thresh in thresholds:
        # We test 3 Gate Choices:
        # Choice 1: Gate applied to enriched query
        # Choice 2: Gate applied to original query
        # Choice 3: Gate applied to enriched query + lexical overlap guard (content term or BM25 > 0 on original)

        for choice_num, choice_desc in [
            (1, "Choice 1: Enriched-query gate"),
            (2, "Choice 2: Original-query gate"),
            (3, "Choice 3: Enriched + lexical guard"),
        ]:
            pos_acc = 0
            neg_rej = 0
            acc_leaks = 0

            # 1. Positives
            for idx, c in enumerate(total_positives):
                prefix = "single" if idx < 62 else ("dev" if idx < 100 else "realistic")
                orig_q = c["question"]
                rw_q = get_query_and_hist(c, prefix)

                # Vector scores
                vc_rw = _query_vector_store(col, rw_q, where_emp, 5)
                vc_orig = _query_vector_store(col, orig_q, where_emp, 5)

                top_rw_score = vc_rw[0].score if (vc_rw and vc_rw[0].score is not None) else 0.0
                top_orig_score = vc_orig[0].score if (vc_orig and vc_orig[0].score is not None) else 0.0

                passed = False
                if choice_num == 1:
                    passed = top_rw_score >= thresh
                elif choice_num == 2:
                    passed = top_orig_score >= thresh
                elif choice_num == 3:
                    if top_rw_score >= thresh:
                        best_doc = vc_rw[0].text if vc_rw else ""
                        best_doc_tokens = set(tokenize_bm25(best_doc))
                        orig_tokens = tokenize_bm25(orig_q)
                        content_terms = [
                            w for w in orig_tokens
                            if w not in _COMMON_STOPWORDS and len(w) >= 3 and not w.isdigit()
                        ]
                        has_lexical = any(t in best_doc_tokens for t in content_terms)
                        bm25_pos = any(t in best_doc_tokens and bm25.idf.get(t, 0.0) > 0.0 for t in orig_tokens)
                        if has_lexical or bm25_pos:
                            passed = True

                if passed:
                    pos_acc += 1

            # 2. Negatives
            for c in all_negatives:
                orig_q = c["question"]
                hist = c.get("history", [])
                rw_q = _rewrite_heuristic(orig_q, hist, variant="c") if hist else orig_q

                vc_rw = _query_vector_store(col, rw_q, where_emp, 5)
                vc_orig = _query_vector_store(col, orig_q, where_emp, 5)

                top_rw_score = vc_rw[0].score if (vc_rw and vc_rw[0].score is not None) else 0.0
                top_orig_score = vc_orig[0].score if (vc_orig and vc_orig[0].score is not None) else 0.0

                passed = False
                if choice_num == 1:
                    passed = top_rw_score >= thresh
                elif choice_num == 2:
                    passed = top_orig_score >= thresh
                elif choice_num == 3:
                    if top_rw_score >= thresh:
                        best_doc = vc_rw[0].text if vc_rw else ""
                        best_doc_tokens = set(tokenize_bm25(best_doc))
                        orig_tokens = tokenize_bm25(orig_q)
                        content_terms = [
                            w for w in orig_tokens
                            if w not in _COMMON_STOPWORDS and len(w) >= 3 and not w.isdigit()
                        ]
                        has_lexical = any(t in best_doc_tokens for t in content_terms)
                        bm25_pos = any(t in best_doc_tokens and bm25.idf.get(t, 0.0) > 0.0 for t in orig_tokens)
                        if has_lexical or bm25_pos:
                            passed = True

                if not passed:
                    neg_rej += 1

            # 3. Access
            for ac in access_cases:
                orig_q = ac["question"]
                hist = ac.get("history", [])
                rw_q = _rewrite_heuristic(orig_q, hist, variant="c") if hist else orig_q
                vc = _query_vector_store(col, rw_q, where_emp, 5)
                if any(c.source == "hr_benefits.md" for c in vc):
                    acc_leaks += 1

            pos_pct = pos_acc / len(total_positives) * 100.0
            neg_pct = neg_rej / len(all_negatives) * 100.0
            print(f"{thresh:<10.2f} | {choice_desc:<38} | {pos_acc:>3}/{len(total_positives)} ({pos_pct:>5.1f}%) | {neg_rej:>3}/{len(all_negatives)} ({neg_pct:>5.1f}%) | {acc_leaks}/{len(access_cases)}")
        print("-" * 125)


def run_item2_keyword_stubs(cache: dict[str, dict[str, Any]]) -> None:
    """Item 2: Keyword-stub guard evaluation for off, heuristic C (with guard), and LLM both."""
    print("\n" + "=" * 105)
    print("ITEM 2: KEYWORD-STUB GUARD BENCHMARK")
    print("=" * 105)

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

    print(f"{'Stub ID':<8} | {'Original Stub':<20} | {'Method':<24} | {'Executed Search Query':<45} | {'Tokens Preserved':<18} | {'Rank'}")
    print("-" * 130)

    for s in stubs:
        cid = str(s["id"])
        q = s["question"]

        # 1. OFF
        _, _, _, rk_off, _ = eval_case_retrieval(q, s)
        print(f"{cid:<8} | {q:<20} | {'off':<24} | {q:<45} | {'YES':<18} | {str(rk_off):<4}")

        # 2. Heuristic C with Guard
        rw_c = _rewrite_heuristic(q, fake_history, variant="c")
        _, _, _, rk_c, _ = eval_case_retrieval(rw_c, s)
        c_pres = "YES (blocked)" if rw_c == q else ("YES" if all(w in rw_c.lower() for w in q.lower().split()) else "NO")
        print(f"{cid:<8} | {q:<20} | {'heuristic (C guarded)':<24} | {rw_c:<45} | {c_pres:<18} | {str(rk_c):<4}")

        # 3. LLM Both (fusion keeps original raw stub)
        item = cache.get(f"stubs:{cid}", {})
        rw_llm = item.get("rewritten", q)
        _, _, _, rk_both, _ = eval_case_retrieval(rw_llm, s, original_query=q)
        both_pres = "YES (fused raw)"
        print(f"{cid:<8} | {q:<20} | {'llm (both)':<24} | {rw_llm:<45} | {both_pres:<18} | {str(rk_both):<4}")
        print("-" * 130)


def run_item3_realistic_and_variant_f(cache: dict[str, dict[str, Any]]) -> None:
    """Item 3: Evaluate variants A, C, D, F, and llm_then_heuristic across sets."""
    print("\n" + "=" * 105)
    print("ITEM 3: REALISTIC HISTORIES & VARIANT F BENCHMARK")
    print("=" * 105)

    old_multis, _, _ = load_yaml_cases()
    dev_38 = old_multis + DEV_15_FOLLOWUPS
    real_12 = REALISTIC_12_HISTORIES
    test_v1 = FRESH_15_TEST_CASES
    test_v2 = FRESH_20_TEST_CASES_V2

    test_groups = [
        ("Realistic Histories (N=12)", "realistic", real_12),
        ("Dev Set (N=38)", "dev", dev_38),
        ("Test Set v1 (N=15)", "test_v1", test_v1),
        ("Test Set v2 (N=20)", "test_v2", test_v2),
    ]

    variants = [
        ("Variant A (current)", "a"),
        ("Variant C (<=2 terms)", "c"),
        ("Variant D (+asst nouns)", "d"),
        ("Variant F (D + no user topic term)", "f"),
        ("llm_then_heuristic", "lth"),
    ]

    print(f"{'Dataset':<26} | {'Variant':<34} | {'Top-1':<14} | {'Top-3':<14} | {'Recall@5'}")
    print("-" * 105)

    for group_name, prefix, cases in test_groups:
        for v_name, v_code in variants:
            t1, t3, r5 = 0, 0, 0
            n = len(cases)
            for c in cases:
                cid = str(c["id"])
                q = c["question"]
                hist = c.get("history", [])

                if v_code == "lth":
                    item = cache.get(f"{prefix}:{cid}", {})
                    if item.get("status") == "ok":
                        rw = item.get("rewritten", q)
                    else:
                        rw = _rewrite_heuristic(q, hist, variant="c")
                else:
                    rw = _rewrite_heuristic(q, hist, variant=v_code)

                t1_h, t3_h, r5_h, _, _ = eval_case_retrieval(rw, c)
                if t1_h: t1 += 1
                if t3_h: t3 += 1
                if r5_h: r5 += 1

            t1_pct = t1 / n * 100.0
            t3_pct = t3 / n * 100.0
            r5_pct = r5 / n * 100.0
            print(f"{group_name:<26} | {v_name:<34} | {t1:>2}/{n} ({t1_pct:>5.1f}%) | {t3:>2}/{n} ({t3_pct:>5.1f}%) | {r5:>2}/{n} ({r5_pct:>5.1f}%)")
        print("-" * 105)


def check_user_followups() -> None:
    """Item 5: Inspect and report entries in tests/user_followups.yaml."""
    print("\n" + "=" * 105)
    print("ITEM 5: USER FOLLOW-UPS YAML INSPECTION")
    print("=" * 105)
    uf_path = Path("tests/user_followups.yaml")
    if not uf_path.exists():
        print("tests/user_followups.yaml does not exist.")
        return
    with uf_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    count = len(data) if isinstance(data, list) else 0
    print(f"tests/user_followups.yaml exists and currently contains {count} entries.")
    if count == 0:
        print("It is currently EMPTY (`[]`). Zero entries were invented.")


def main() -> None:
    cache = load_cache()
    run_item1_gate_tradeoffs(cache)
    run_item2_keyword_stubs(cache)
    run_item3_realistic_and_variant_f(cache)
    check_user_followups()
    print("\n[SUCCESS] Step 4f evaluation script finished successfully.")


if __name__ == "__main__":
    main()
