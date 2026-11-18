"""Evaluation harness measuring chunk-level accuracy, RBAC security, thresholds, and latency."""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from tokenizers import Tokenizer

from app.config import settings
from app.rag.retriever import RetrievedChunk, get_collection_name, retrieve

EVAL_FILE = Path("tests/rag_eval.yaml")
RESULTS_DIR = Path("eval_results")
TOKENIZER_PATH = Path("models/multilingual-e5-small/tokenizer.json")


@dataclass
class CaseResult:
    """Evaluation output metrics for an individual test case."""

    case_id: str
    category: str
    question: str
    role: str
    expected_sources: list[str]
    expected_phrase: str
    retrieved_sources: list[str]
    top_score: float | None
    latency_ms: float
    top_1_hit: bool
    top_3_hit: bool
    recall_5_hit: bool
    recall_8_hit: bool
    correct_no_context: bool
    access_leak: bool
    any_of: list[dict[str, str]] | None = None
    top_snippet: str = ""
    top_3_snippets: list[str] | None = None


def _load_eval_cases() -> list[dict[str, Any]]:  # type: ignore[misc]
    """Loads benchmark test cases from tests/rag_eval.yaml."""
    if not EVAL_FILE.exists():
        raise FileNotFoundError(f"Evaluation benchmark file not found: {EVAL_FILE}")
    with EVAL_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def _strip_prefix(text: str) -> str:
    """Strips leading markdown header or breadcrumb prefix from chunk text."""
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
    stripped = re.sub(r"^(?:#{1,6}\s+[^\n]+\n*)+", "", stripped).strip()
    return stripped


def _normalize_eval_text(text: str) -> str:
    """Normalizes markdown syntax, dashes, whitespace, and case for robust phrase matching."""
    if not text:
        return ""
    cleaned = re.sub(r"[\*_`]+", "", text)
    cleaned = re.sub(r"[\u2010-\u2015\u2212]", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


def _is_chunk_match(
    chunk: RetrievedChunk,
    sources: list[str],
    phrase: str,
    any_of: list[dict[str, str]] | None = None,
) -> bool:
    """Checks whether an individual chunk matches any acceptable (source, phrase) definition."""
    clean = _normalize_eval_text(_strip_prefix(chunk.text))
    if any_of:
        for opt in any_of:
            opt_src = opt.get("source")
            opt_phrase = _normalize_eval_text(opt.get("phrase", ""))
            src_ok = (not opt_src) or (chunk.source == opt_src)
            phrase_ok = (not opt_phrase) or (opt_phrase in clean)
            if src_ok and phrase_ok:
                return True
        return False

    src_ok = (not sources) or (chunk.source in sources)
    norm_phrase = _normalize_eval_text(phrase)
    phrase_ok = (not norm_phrase) or (norm_phrase in clean)
    return bool(src_ok and phrase_ok)


def _check_candidate_hits(
    chunks: list[RetrievedChunk],
    sources: list[str],
    phrase: str,
    any_of: list[dict[str, str]] | None = None,
) -> tuple[bool, bool, bool, bool]:
    """Evaluates top-1, top-3, recall@5, and recall@8 against acceptable candidate definitions."""
    if not chunks:
        return False, False, False, False

    top_1 = _is_chunk_match(chunks[0], sources, phrase, any_of)
    top_3 = any(_is_chunk_match(c, sources, phrase, any_of) for c in chunks[:3])
    top_5 = any(_is_chunk_match(c, sources, phrase, any_of) for c in chunks[:5])
    top_8 = any(_is_chunk_match(c, sources, phrase, any_of) for c in chunks[:8])
    return top_1, top_3, top_5, top_8


def _evaluate_single_case(
    case: dict[str, Any],  # type: ignore[misc]
    threshold: float | None = None,
    mode: str | None = None,
    table_max_rows: int | None = None,
    hybrid_search: bool = False,
) -> CaseResult:
    """Executes retrieval for a test case and records candidate accuracy, security, and latency."""
    case_id: str = str(case["id"])
    category: str = str(case.get("category", "single_turn"))
    question: str = str(case["question"])
    role: str = str(case.get("role", "employee"))
    expected_sources: list[str] = case.get("expected_sources", [])
    expected_phrase: str = str(case.get("expected_phrase", "")).strip().lower()
    forbidden_sources: list[str] = case.get("forbidden_sources", [])
    any_of: list[dict[str, str]] | None = case.get("any_of")

    t0 = time.perf_counter()
    chunks: list[RetrievedChunk] = retrieve(
        question,
        role=role,
        score_threshold=threshold,
        mode=mode,
        table_max_rows=table_max_rows,
        top_k=8,
        hybrid_search=hybrid_search,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    retrieved_sources = list(dict.fromkeys(c.source for c in chunks))
    top_score = (
        round(chunks[0].score, 4)
        if (chunks and chunks[0].score is not None)
        else None
    )
    leak = any(src in forbidden_sources for src in retrieved_sources)

    top_1_hit, top_3_hit, top_5_hit, top_8_hit = False, False, False, False
    correct_no_context = False

    if category in ("single_turn", "multi_turn", "ambiguous"):
        top_1_hit, top_3_hit, top_5_hit, top_8_hit = _check_candidate_hits(
            chunks, expected_sources, expected_phrase, any_of=any_of
        )
    elif category in ("hard_negative", "easy_negative"):
        correct_no_context = len(chunks) == 0
    elif category == "access":
        correct_no_context = not leak

    top_snippet = ""
    top_3_snippets: list[str] = []
    for c in chunks[:3]:
        clean_text = _strip_prefix(c.text).replace("\n", " ")
        score_str = f"{c.score:.4f}" if c.score is not None else "N/A"
        top_3_snippets.append(f"[{c.source} ({score_str})] {clean_text[:90]}")
    if top_3_snippets:
        top_snippet = top_3_snippets[0]

    return CaseResult(
        case_id=case_id,
        category=category,
        question=question,
        role=role,
        expected_sources=expected_sources,
        expected_phrase=expected_phrase,
        retrieved_sources=retrieved_sources,
        top_score=top_score,
        latency_ms=latency_ms,
        top_1_hit=top_1_hit,
        top_3_hit=top_3_hit,
        recall_5_hit=top_5_hit,
        recall_8_hit=top_8_hit,
        correct_no_context=correct_no_context,
        access_leak=leak,
        any_of=any_of,
        top_snippet=top_snippet,
        top_3_snippets=top_3_snippets,
    )


def _compute_summary_metrics(
    results: list[CaseResult],
    cold_start_ms: float = 0.0,
    threshold: float | None = None,
    mode: str | None = None,
    table_max_rows: int = 0,
    hybrid_search: bool = False,
) -> dict[str, Any]:  # type: ignore[misc]
    """Aggregates individual case results into overall evaluation metrics with latency percentiles."""
    positives = [r for r in results if r.category in ("single_turn", "multi_turn", "ambiguous")]
    negatives = [r for r in results if r.category in ("hard_negative", "easy_negative")]
    warm_latencies = [r.latency_ms for r in results]

    def _calc_pct(num: int, denom: int) -> float:
        """Calculates percentage rounded to two decimals."""
        return round((num / denom * 100.0), 2) if denom else 0.0

    p50_lat = round(float(np.percentile(warm_latencies, 50)), 2) if warm_latencies else 0.0
    p95_lat = round(float(np.percentile(warm_latencies, 95)), 2) if warm_latencies else 0.0
    avg_lat = round(float(np.mean(warm_latencies)), 2) if warm_latencies else 0.0

    return {
        "mode": mode or settings.chunking_mode,
        "table_max_rows": table_max_rows,
        "hybrid_search": hybrid_search,
        "threshold": threshold if threshold is not None else settings.rag_score_threshold,
        "total_cases": len(results),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "top_1_accuracy_pct": _calc_pct(sum(1 for r in positives if r.top_1_hit), len(positives)),
        "top_3_recall_pct": _calc_pct(sum(1 for r in positives if r.top_3_hit), len(positives)),
        "recall_5_pct": _calc_pct(sum(1 for r in positives if r.recall_5_hit), len(positives)),
        "recall_8_pct": _calc_pct(sum(1 for r in positives if r.recall_8_hit), len(positives)),
        "correct_no_context_rate_pct": _calc_pct(
            sum(1 for r in negatives if r.correct_no_context), len(negatives)
        ),
        "access_leaks": sum(1 for r in results if r.access_leak),
        "cold_start_latency_ms": round(cold_start_ms, 2),
        "avg_warm_latency_ms": avg_lat,
        "p50_warm_latency_ms": p50_lat,
        "p95_warm_latency_ms": p95_lat,
    }


def _check_split_integrity(mode: str, table_max_rows: int = 0) -> dict[str, bool]:
    """Evaluates whether critical semantic structures are preserved intact within chunks."""
    import chromadb  # noqa: PLC0415

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    col = client.get_collection(name=get_collection_name(mode, table_max_rows))
    docs = col.get(where={"source": "leave_and_notice_policy.md"})["documents"]

    cond_phrases = [
        "If an employee is currently serving an active initial probation period",
        "mandatory standard separation notice period is 60 calendar days",
        "Exception 1",
        "30 calendar days",
        "Exception 2",
        "14 calendar days",
    ]
    cond_intact = any(all(p in d for p in cond_phrases) for d in docs)

    proc_phrases = [
        "1. Resignation Submission",
        "6. Financial Settlement Clearance",
    ]
    proc_intact = any(all(p in d for p in proc_phrases) for d in docs)

    if table_max_rows == 0:
        table_phrases = [
            "| Leave Type | Annual Entitlement |",
            "Bereavement Leave (BL)",
            "Study Leave (STL)",
        ]
        table_intact = any(all(p in d for p in table_phrases) for d in docs)
    else:
        table_chunks = [d for d in docs if "Bereavement Leave" in d or "Study Leave" in d]
        table_intact = bool(table_chunks) and all(
            "| Leave Type | Annual Entitlement |" in d for d in table_chunks
        )

    return {
        "conditional_rule_strict": cond_intact,
        "table_integrity": table_intact,
        "procedure_steps_1_to_6": proc_intact,
    }


def _get_chunk_token_stats(
    mode: str, table_max_rows: int = 0
) -> dict[str, Any]:  # type: ignore[misc]
    """Calculates chunk counts and token distribution statistics using real e5 tokenizer."""
    import chromadb  # noqa: PLC0415

    tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    col = client.get_collection(name=get_collection_name(mode, table_max_rows))
    docs = col.get()["documents"]

    token_lens = [len(tokenizer.encode(d).ids) for d in docs]
    over_400 = sum(1 for length in token_lens if length > 400)
    over_512 = sum(1 for length in token_lens if length > 512)

    return {
        "chunk_count": len(docs),
        "min_tokens": min(token_lens) if token_lens else 0,
        "median_tokens": float(np.median(token_lens)) if token_lens else 0.0,
        "p95_tokens": float(np.percentile(token_lens, 95)) if token_lens else 0.0,
        "max_tokens": max(token_lens) if token_lens else 0,
        "chunks_over_400": over_400,
        "chunks_over_512": over_512,
    }


def _check_per_mode_phrase_coverage(
    mode: str, table_max_rows: int = 0
) -> list[tuple[str, str]]:
    """Checks whether each expected phrase exists in at least one stored chunk for the mode."""
    import chromadb  # noqa: PLC0415

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    col = client.get_collection(name=get_collection_name(mode, table_max_rows))
    data = col.get(include=["documents", "metadatas"])

    docs_by_source: dict[str, list[str]] = {}
    for doc, meta in zip(data["documents"], data["metadatas"]):
        src = str(meta.get("source", ""))
        docs_by_source.setdefault(src, []).append(_strip_prefix(doc).lower())

    cases = _load_eval_cases()
    missing: list[tuple[str, str]] = []

    for c in cases:
        if c.get("category") not in ("single_turn", "multi_turn", "ambiguous"):
            continue
        any_of = c.get("any_of")
        if any_of:
            for opt in any_of:
                src = opt.get("source", "")
                phrase = opt.get("phrase", "").strip().lower()
                doc_list = docs_by_source.get(src, [])
                if not any(phrase in d for d in doc_list):
                    missing.append((mode, phrase))
        else:
            phrase = str(c.get("expected_phrase", "")).strip().lower()
            srcs = c.get("expected_sources", [])
            doc_list = []
            for s in srcs:
                doc_list.extend(docs_by_source.get(s, []))
            if not doc_list:
                doc_list = [d for sub in docs_by_source.values() for d in sub]
            if not any(phrase in d for d in doc_list):
                missing.append((mode, phrase))

    return missing


def _save_evaluation_results(
    metrics: dict[str, Any],  # type: ignore[misc]
    results: list[CaseResult],
    config_name: str,
) -> Path:
    """Saves benchmark results and metrics to eval_results/<timestamp>_<config_name>.json."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = RESULTS_DIR / f"{timestamp}_{config_name}.json"

    payload = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "config_name": config_name,
        "flags": {
            "chunking_mode": metrics.get("mode"),
            "table_max_rows_per_chunk": metrics.get("table_max_rows", 0),
            "hybrid_search": metrics.get("hybrid_search", False),
            "hybrid_candidates": settings.hybrid_candidates,
            "reranker": settings.reranker,
            "query_rewrite": settings.query_rewrite,
            "grader": settings.grader,
            "orchestrator": settings.orchestrator,
        },
        "metrics": metrics,
        "cases": [asdict(r) for r in results],
    }

    with out_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    return out_file


def _print_hybrid_comparison_table(
    off_metrics: dict[str, Any],  # type: ignore[misc]
    on_metrics: dict[str, Any],  # type: ignore[misc]
    off_results: list[CaseResult],
    on_results: list[CaseResult],
    chunk_count: int,
) -> None:
    """Prints side-by-side comparison table between HYBRID_SEARCH=off and HYBRID_SEARCH=on."""
    col_w = 26
    hdr = f"{'Metric / Category':<34} | {'Hierarchical t4 (Hybrid OFF)':<{col_w}} | {'Hierarchical t4 (Hybrid ON)':<{col_w}}"

    print("\n" + "═" * len(hdr))
    print("         HYBRID SEARCH BENCHMARK COMPARISON TABLE (Mode: Hierarchical t4, Threshold: 0.77)")
    print("═" * len(hdr))
    print(hdr)
    print("─" * len(hdr))

    print(f"{'Chunk Count':<34} | {chunk_count:>{col_w}} | {chunk_count:>{col_w}}")
    print(
        f"{'Top-1 Accuracy (Positives)':<34} | "
        f"{off_metrics['top_1_accuracy_pct']:.2f}%".rjust(col_w)
        + " | "
        + f"{on_metrics['top_1_accuracy_pct']:.2f}%".rjust(col_w)
    )
    print(
        f"{'Top-3 Recall (Positives)':<34} | "
        f"{off_metrics['top_3_recall_pct']:.2f}%".rjust(col_w)
        + " | "
        + f"{on_metrics['top_3_recall_pct']:.2f}%".rjust(col_w)
    )
    print(
        f"{'Recall@5 (Positives)':<34} | "
        f"{off_metrics['recall_5_pct']:.2f}%".rjust(col_w)
        + " | "
        + f"{on_metrics['recall_5_pct']:.2f}%".rjust(col_w)
    )
    print(
        f"{'Recall@8 (Positives)':<34} | "
        f"{off_metrics['recall_8_pct']:.2f}%".rjust(col_w)
        + " | "
        + f"{on_metrics['recall_8_pct']:.2f}%".rjust(col_w)
    )
    print("─" * len(hdr))

    # Categories
    categories = [
        ("single_turn", "Single-Turn (T1 / T3 / R5 / R8)"),
        ("multi_turn", "Multi-Turn (T1 / T3 / R5 / R8)"),
        ("ambiguous", "Ambiguous (T1 / T3 / R5 / R8)"),
    ]
    for cat, label in categories:
        s_off = [r for r in off_results if r.category == cat]
        s_on = [r for r in on_results if r.category == cat]
        t1_off, t3_off = sum(1 for r in s_off if r.top_1_hit), sum(1 for r in s_off if r.top_3_hit)
        r5_off, r8_off = sum(1 for r in s_off if r.recall_5_hit), sum(1 for r in s_off if r.recall_8_hit)
        t1_on, t3_on = sum(1 for r in s_on if r.top_1_hit), sum(1 for r in s_on if r.top_3_hit)
        r5_on, r8_on = sum(1 for r in s_on if r.recall_5_hit), sum(1 for r in s_on if r.recall_8_hit)
        off_str = f"{t1_off/len(s_off)*100:.0f}% / {t3_off/len(s_off)*100:.0f}% / {r5_off/len(s_off)*100:.0f}% / {r8_off/len(s_off)*100:.0f}%"
        on_str = f"{t1_on/len(s_on)*100:.0f}% / {t3_on/len(s_on)*100:.0f}% / {r5_on/len(s_on)*100:.0f}% / {r8_on/len(s_on)*100:.0f}%"
        print(f"{label:<34} | {off_str:>{col_w}} | {on_str:>{col_w}}")

    print("─" * len(hdr))
    print(
        f"{'Correct No-Context (Negatives)':<34} | "
        f"{off_metrics['correct_no_context_rate_pct']:.2f}%".rjust(col_w)
        + " | "
        + f"{on_metrics['correct_no_context_rate_pct']:.2f}%".rjust(col_w)
    )
    print(
        f"{'Access Leaks (Must be 0)':<34} | "
        f"{off_metrics['access_leaks']}".rjust(col_w)
        + " | "
        + f"{on_metrics['access_leaks']}".rjust(col_w)
    )
    print(
        f"{'Avg Warm Query Latency':<34} | "
        f"{off_metrics['avg_warm_latency_ms']:.2f} ms".rjust(col_w)
        + " | "
        + f"{on_metrics['avg_warm_latency_ms']:.2f} ms".rjust(col_w)
    )
    print("═" * len(hdr) + "\n")


def _print_spotlight_cases(
    off_results: list[CaseResult], on_results: list[CaseResult]
) -> None:
    """Prints before and after retrieval ranks for key section and code queries."""
    off_map = {r.case_id: r for r in off_results}
    on_map = {r.case_id: r for r in on_results}

    print("--- Spotlight Queries: Hybrid OFF vs Hybrid ON ---")
    spotlight_ids = ["single_18", "ambig_04", "single_16", "single_17"]
    for cid in spotlight_ids:
        r_off = off_map.get(cid)
        r_on = on_map.get(cid)
        if not r_off or not r_on:
            continue
        print(f"[{cid}] Question: {r_off.question!r}")
        print(f"  Hybrid OFF: Top-1 Hit={r_off.top_1_hit}, Top-3 Hit={r_off.top_3_hit}")
        print(f"    Snippet: {r_off.top_snippet}")
        print(f"  Hybrid ON : Top-1 Hit={r_on.top_1_hit}, Top-3 Hit={r_on.top_3_hit}")
        print(f"    Snippet: {r_on.top_snippet}")
        print()


def _print_failing_single_turn(on_results: list[CaseResult]) -> None:
    """Lists failing single-turn queries under Hybrid ON showing question and retrieved chunk."""
    failing = [r for r in on_results if r.category == "single_turn" and not r.top_1_hit]
    print(f"--- Failing Single-Turn Cases for Hybrid ON ({len(failing)}/34) ---")
    for r in failing:
        print(f"[{r.case_id}] {r.question}")
        if r.any_of:
            opts = [f"{o.get('source')}: {o.get('phrase')}" for o in r.any_of]
            print(f"    Acceptable: {opts}")
        else:
            print(f"    Expected  : {r.expected_phrase!r}")
        print(f"    Top Chunk : {r.top_snippet}\n")


def run_config_eval(
    mode: str,
    table_max_rows: int,
    threshold: float,
    hybrid_search: bool = False,
) -> tuple[dict[str, Any], list[CaseResult], dict[str, Any], dict[str, bool]]:  # type: ignore[misc]
    """Runs evaluation benchmark for an individual mode and configuration."""
    cases = _load_eval_cases()

    # Cold start check
    t0 = time.perf_counter()
    _ = retrieve(
        "ping init",
        role="employee",
        mode=mode,
        table_max_rows=table_max_rows,
        top_k=8,
        hybrid_search=hybrid_search,
    )
    cold_start_ms = (time.perf_counter() - t0) * 1000.0

    results = [
        _evaluate_single_case(
            c,
            threshold=threshold,
            mode=mode,
            table_max_rows=table_max_rows,
            hybrid_search=hybrid_search,
        )
        for c in cases
    ]
    metrics = _compute_summary_metrics(
        results,
        cold_start_ms=cold_start_ms,
        threshold=threshold,
        mode=mode,
        table_max_rows=table_max_rows,
        hybrid_search=hybrid_search,
    )
    token_stats = _get_chunk_token_stats(mode, table_max_rows)
    integrity = _check_split_integrity(mode, table_max_rows)

    row_tag = f"_t{table_max_rows}" if table_max_rows > 0 else ""
    hyb_tag = "_hybrid" if hybrid_search else ""
    config_name = f"{mode}{row_tag}{hyb_tag}"
    out_file = _save_evaluation_results(metrics, results, config_name)
    print(f"Results for {config_name} saved to: {out_file.as_posix()}")

    return metrics, results, token_stats, integrity


def run_evaluation(
    threshold: float = 0.77, target_mode: str = "hierarchical_t4", hybrid: str = "compare"
) -> None:
    """Runs evaluation benchmark across modes and prints comparison table."""
    mode = "hierarchical" if "hierarchical" in target_mode else "section"
    table_rows = 4 if "t4" in target_mode else 0

    if hybrid == "compare":
        print(f"\n--- Running Hybrid Comparison on {target_mode} (Threshold: {threshold}) ---")
        off_m, off_r, tok_stats, _ = run_config_eval(
            mode, table_rows, threshold, hybrid_search=False
        )
        on_m, on_r, _, _ = run_config_eval(
            mode, table_rows, threshold, hybrid_search=True
        )
        _print_hybrid_comparison_table(
            off_m, on_m, off_r, on_r, chunk_count=tok_stats["chunk_count"]
        )
        _print_spotlight_cases(off_r, on_r)
        _print_failing_single_turn(on_r)
        return

    hyb_flag = hybrid == "on"
    run_config_eval(mode, table_rows, threshold, hybrid_search=hyb_flag)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG retrieval benchmark evaluation.")
    parser.add_argument(
        "--threshold", type=float, default=0.77, help="Score threshold (default: 0.77)"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="hierarchical_t4",
        choices=["section", "hierarchical", "hierarchical_t4", "all"],
        help="Chunking mode to evaluate (default: hierarchical_t4)",
    )
    parser.add_argument(
        "--hybrid",
        type=str,
        default="compare",
        choices=["off", "on", "compare"],
        help="Evaluate hybrid search (off, on, or compare)",
    )
    args = parser.parse_args()
    run_evaluation(threshold=args.threshold, target_mode=args.mode, hybrid=args.hybrid)
