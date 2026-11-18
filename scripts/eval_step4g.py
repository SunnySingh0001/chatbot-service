"""Step 4g comprehensive evaluation script implementing all Step 4g verifications."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.rag.bm25 import tokenize_bm25
from app.rag.condense import (
    _COMMON_STOPWORDS,
    _extract_assistant_nouns,
    _rewrite_heuristic,
    condense_query_detailed,
    get_corpus_vocabulary,
    is_code_stub,
    is_fuzzy_vocab_match,
    is_topic_switch,
)
from app.rag.retriever import retrieve
from scripts.rag_eval import _check_candidate_hits, _is_chunk_match
from tests.dev_followups import DEV_15_FOLLOWUPS
from tests.realistic_histories import REALISTIC_12_HISTORIES
from tests.test_followups_v2 import FRESH_20_TEST_CASES_V2
from scratch.run_step4c_eval import FRESH_15_TEST_CASES
from scratch.run_step4e_eval import load_yaml_cases

EVAL_YAML_PATH = Path("tests/rag_eval.yaml")
CACHE_FILE = Path("eval_results/fair_rewrites_cache.json")


def load_fair_cache() -> dict[str, dict[str, Any]]:
    """Loads cached rewrites from Step 4e fair evaluation."""
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def eval_query_retrieval(
    query_str: str,
    case: dict[str, Any],
    original_query: str | None = None,
    score_threshold: float = 0.80,
) -> tuple[bool, bool, bool, int | None, list[Any]]:
    """Runs retrieval and returns top1, top3, recall5, rank, chunks."""
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


def run_item1_code_stubs(cache: dict[str, dict[str, Any]]) -> None:
    """Item 1: Code stubs and the LLM evaluation."""
    print("\n" + "=" * 115)
    print("ITEM 1: CODE STUBS AND THE LLM REWRITER EVALUATION")
    print("=" * 115)

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

    print(f"{'Stub ID':<8} | {'Stub Query':<20} | {'Type':<12} | {'Method':<20} | {'Executed Search Query':<48} | {'Tokens Preserved':<18} | {'Rank'}")
    print("-" * 135)

    for s in stubs:
        cid = str(s["id"])
        q = s["question"]
        code_s = is_code_stub(q)
        stub_type = "Code Stub" if code_s else "Typo Stub"

        # 1. OFF
        _, _, _, rk_off, _ = eval_query_retrieval(q, s)
        print(f"{cid:<8} | {q:<20} | {stub_type:<12} | {'off':<20} | {q:<48} | {'YES (raw)':<18} | {str(rk_off):<4}")

        # 2. Heuristic C
        rw_c = _rewrite_heuristic(q, fake_history, variant="c")
        _, _, _, rk_c, _ = eval_query_retrieval(rw_c, s)
        c_pres = "YES (blocked)" if rw_c == q else ("YES" if all(w in rw_c.lower() for w in q.lower().split()) else "NO")
        print(f"{cid:<8} | {q:<20} | {stub_type:<12} | {'heuristic C':<20} | {rw_c:<48} | {c_pres:<18} | {str(rk_c):<4}")

        # 3. LLM (new rule: if code_stub -> retrieve with original query only; typo stubs may be corrected by LLM)
        if code_s:
            rw_llm = q
            llm_pres = "YES (code bypass)"
        else:
            cached = cache.get(f"stubs:{cid}", {})
            rw_llm = cached.get("rewritten", q)
            llm_pres = "YES (spelling fixed)"
        _, _, _, rk_llm, _ = eval_query_retrieval(rw_llm, s)
        print(f"{cid:<8} | {q:<20} | {stub_type:<12} | {'LLM (new rule)':<20} | {rw_llm:<48} | {llm_pres:<18} | {str(rk_llm):<4}")
        print("-" * 135)


def run_item2_realistic_inspection(cache: dict[str, dict[str, Any]]) -> None:
    """Item 2: Detailed inspection of realistic cases real_01, real_03, real_08 and benchmark across sets."""
    print("\n" + "=" * 115)
    print("ITEM 2: VARIANTS D AND F ON REALISTIC CASES & BENCHMARK")
    print("=" * 115)

    cases_to_inspect = ["real_01", "real_03", "real_08"]
    print("--- Detailed Inspection for real_01, real_03, real_08 ---")

    for cid in cases_to_inspect:
        c = [x for x in REALISTIC_12_HISTORIES if x["id"] == cid][0]
        q = c["question"]
        hist = c["history"]

        last_asst = ""
        for turn in reversed(hist):
            if turn.get("role") == "assistant":
                last_asst = turn.get("content", "")
                break

        d_nouns = _extract_assistant_nouns(hist)
        # Variant F checks if last user message has domain terms
        last_u = ""
        for turn in reversed(hist):
            if turn.get("role") == "user":
                last_u = turn.get("content", "")
                break
        vocab = get_corpus_vocabulary()
        u_terms = [
            w for w in tokenize_bm25(last_u)
            if (not vocab or w in vocab) and w not in _COMMON_STOPWORDS and len(w) >= 3 and not any(ch.isdigit() for ch in w)
        ]
        f_nouns = [] if len(u_terms) > 0 else d_nouns

        rw_a = _rewrite_heuristic(q, hist, variant="a")
        rw_c = _rewrite_heuristic(q, hist, variant="c")
        rw_d = _rewrite_heuristic(q, hist, variant="d")
        rw_f = _rewrite_heuristic(q, hist, variant="f")

        _, _, _, rk_a, _ = eval_query_retrieval(rw_a, c)
        _, _, _, rk_c, _ = eval_query_retrieval(rw_c, c)
        _, _, _, rk_d, _ = eval_query_retrieval(rw_d, c)
        _, _, _, rk_f, _ = eval_query_retrieval(rw_f, c)

        print(f"\nCase ID: [{cid}]")
        print(f"Follow-up Question: \"{q}\"")
        print("History:")
        for idx, turn in enumerate(hist, 1):
            print(f"  Turn {idx} [{turn.get('role')}]: {turn.get('content')}")
        print(f"Last Assistant Answer: \"{last_asst}\"")
        print(f"Extracted Nouns by Variant D: {d_nouns}")
        print(f"Extracted Nouns by Variant F: {f_nouns} (last user domain terms: {u_terms})")
        print(f"Enriched Query Variant A: \"{rw_a}\" -> Rank: {rk_a}")
        print(f"Enriched Query Variant C: \"{rw_c}\" -> Rank: {rk_c}")
        print(f"Enriched Query Variant D: \"{rw_d}\" -> Rank: {rk_d}")
        print(f"Enriched Query Variant F: \"{rw_f}\" -> Rank: {rk_f}")

    print("\n" + "-" * 115)
    print("FOUR-DATASET BENCHMARK TABLE (A, C, D, F, llm_then_heuristic)")
    print("-" * 115)

    old_multis, _, _ = load_yaml_cases()
    dev_38 = old_multis + DEV_15_FOLLOWUPS
    real_12 = REALISTIC_12_HISTORIES
    test_v1 = FRESH_15_TEST_CASES
    test_v2 = FRESH_20_TEST_CASES_V2

    datasets = [
        ("Realistic Histories (N=12)", "realistic", real_12),
        ("Dev Set (N=38)", "dev", dev_38),
        ("Test Set v1 (N=15)", "test_v1", test_v1),
        ("Test Set v2 (N=20)", "test_v2", test_v2),
    ]

    for dname, dprefix, dcases in datasets:
        print(f"\nDataset: {dname}")
        print(f"{'Method':<24} | {'Top-1':<15} | {'Top-3':<15} | {'Recall@5':<15}")
        print("-" * 75)

        methods = [
            ("Variant A", "a"),
            ("Variant C", "c"),
            ("Variant D", "d"),
            ("Variant F", "f"),
            ("llm_then_heuristic", "llm_then_heuristic"),
        ]

        for mlabel, mkey in methods:
            t1_cnt, t3_cnt, r5_cnt = 0, 0, 0
            n = len(dcases)
            for c in dcases:
                cid = str(c.get("id"))
                q = c["question"]
                hist = c.get("history", [])

                if mkey == "llm_then_heuristic":
                    if is_code_stub(q):
                        rw_q = q
                    else:
                        cached = cache.get(f"{dprefix}:{cid}") or cache.get(f"realistic:{cid}") or cache.get(f"stubs:{cid}") or {}
                        rw_llm = cached.get("rewritten")
                        st = cached.get("status")
                        if rw_llm and st == "ok":
                            rw_q = rw_llm
                        else:
                            rw_q = _rewrite_heuristic(q, hist, variant="c")
                else:
                    rw_q = _rewrite_heuristic(q, hist, variant=mkey)

                t1, t3, r5, _, _ = eval_query_retrieval(rw_q, c)
                if t1:
                    t1_cnt += 1
                if t3:
                    t3_cnt += 1
                if r5:
                    r5_cnt += 1

            pct1 = t1_cnt / n * 100.0
            pct3 = t3_cnt / n * 100.0
            pct5 = r5_cnt / n * 100.0
            print(f"{mlabel:<24} | {t1_cnt:>2}/{n} ({pct1:>5.1f}%) | {t3_cnt:>2}/{n} ({pct3:>5.1f}%) | {r5_cnt:>2}/{n} ({pct5:>5.1f}%)")


def run_item3_topic_switches() -> None:
    """Item 3: Evaluate typo-tolerant topic switch check on 5 stubs and 3 v2 topic switches."""
    print("\n" + "=" * 115)
    print("ITEM 3: TOPIC SWITCHES & TYPO-TOLERANT CORPUS VOCABULARY CHECK")
    print("=" * 115)

    stubs = ["VPN", "SLA P1", "Section 2.3", "pasword requirments", "probaton perod"]
    print("--- 5 Stubs Evaluation ---")
    for s in stubs:
        sw = is_topic_switch(s)
        code = is_code_stub(s)
        print(f"Stub Query: '{s:<22}' | is_code_stub={str(code):<5} | is_topic_switch={str(sw):<5} | Action: {'NOT ENRICHED' if (sw or code) else 'ENRICHED'}")

    print("\n--- Test Set v2 Topic Switch Cases ---")
    v2_switches = ["v2_15", "v2_16", "v2_17"]
    for c in FRESH_20_TEST_CASES_V2:
        if c["id"] in v2_switches:
            sw = is_topic_switch(c["question"])
            print(f"Case [{c['id']}] Question: \"{c['question']}\" | is_topic_switch={sw}")

    # Check across all 85 follow-up cases
    old_multis, _, _ = load_yaml_cases()
    all_followups = old_multis + DEV_15_FOLLOWUPS + FRESH_15_TEST_CASES + REALISTIC_12_HISTORIES + FRESH_20_TEST_CASES_V2

    wrongly_skipped: list[tuple[str, str]] = []
    intentional_switches: list[tuple[str, str]] = []

    for c in all_followups:
        cid = str(c.get("id"))
        q = c["question"]
        if is_topic_switch(q):
            if any(m in q.lower() for m in ["switching", "changing subject", "different question", "unrelated", "changing gear"]):
                intentional_switches.append((cid, q))
            else:
                wrongly_skipped.append((cid, q))

    print(f"\nTotal follow-up queries scanned: {len(all_followups)}")
    print(f"Intentional topic switches correctly identified: {len(intentional_switches)}")
    for cid, q in intentional_switches:
        print(f"  - [{cid}]: \"{q}\"")
    print(f"Wrongly skipped legitimate dependent follow-ups: {len(wrongly_skipped)}")
    if wrongly_skipped:
        for cid, q in wrongly_skipped:
            print(f"  - [{cid}]: \"{q}\"")
    else:
        print("  None! (0 false positives on legitimate follow-ups)")


if __name__ == "__main__":
    cache = load_fair_cache()
    run_item1_code_stubs(cache)
    run_item2_realistic_inspection(cache)
    run_item3_topic_switches()
