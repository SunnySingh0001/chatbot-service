"""Evaluation harness measuring chunk-level accuracy, RBAC security, thresholds, and latency."""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from app.config import settings
from app.rag.retriever import RetrievedChunk, retrieve

EVAL_FILE = Path("tests/rag_eval.yaml")
RESULTS_DIR = Path("eval_results")


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
    correct_no_context: bool
    access_leak: bool


def _load_eval_cases() -> list[dict[str, Any]]:  # type: ignore[misc]
    """Loads benchmark test cases from tests/rag_eval.yaml."""
    if not EVAL_FILE.exists():
        raise FileNotFoundError(f"Evaluation benchmark file not found: {EVAL_FILE}")
    with EVAL_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def _check_phrase_hit(chunks: list[RetrievedChunk], sources: list[str], phrase: str) -> tuple[bool, bool]:
    """Evaluates top-1 and top-3 accuracy asserting exact phrase and source matches."""
    if not chunks:
        return False, False

    c0 = chunks[0]
    c0_src = (not sources) or (c0.source in sources)
    c0_phrase = (not phrase) or (phrase in c0.text.lower())
    top_1 = bool(c0_src and c0_phrase)

    top_3 = False
    for c in chunks[:3]:
        src_ok = (not sources) or (c.source in sources)
        phrase_ok = (not phrase) or (phrase in c.text.lower())
        if src_ok and phrase_ok:
            top_3 = True
            break
    return top_1, top_3


def _evaluate_single_case(case: dict[str, Any], threshold: float | None = None) -> CaseResult:  # type: ignore[misc]
    """Executes retrieval for a test case and records chunk-level accuracy, security, and latency."""
    case_id: str = str(case["id"])
    category: str = str(case.get("category", "single_turn"))
    question: str = str(case["question"])
    role: str = str(case.get("role", "employee"))
    expected_sources: list[str] = case.get("expected_sources", [])
    expected_phrase: str = str(case.get("expected_phrase", "")).strip().lower()
    forbidden_sources: list[str] = case.get("forbidden_sources", [])

    t0 = time.perf_counter()
    chunks: list[RetrievedChunk] = retrieve(question, role=role, score_threshold=threshold)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    retrieved_sources = list(dict.fromkeys(c.source for c in chunks))
    top_score = round(chunks[0].score, 4) if chunks else None
    leak = any(src in forbidden_sources for src in retrieved_sources)

    top_1_hit = False
    top_3_hit = False
    correct_no_context = False

    if category in ("single_turn", "multi_turn", "ambiguous"):
        top_1_hit, top_3_hit = _check_phrase_hit(chunks, expected_sources, expected_phrase)
    elif category in ("hard_negative", "easy_negative"):
        correct_no_context = len(chunks) == 0
    elif category == "access":
        correct_no_context = not leak

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
        correct_no_context=correct_no_context,
        access_leak=leak,
    )


def _compute_summary_metrics(
    results: list[CaseResult], cold_start_ms: float = 0.0, threshold: float | None = None
) -> dict[str, Any]:  # type: ignore[misc]
    """Aggregates individual case results into overall evaluation metrics with latency percentiles."""
    positives = [r for r in results if r.category in ("single_turn", "multi_turn", "ambiguous")]
    negatives = [r for r in results if r.category in ("hard_negative", "easy_negative")]
    warm_latencies = [r.latency_ms for r in results]

    top_1_acc = round(sum(1 for r in positives if r.top_1_hit) / len(positives) * 100.0, 2) if positives else 0.0
    top_3_rec = round(sum(1 for r in positives if r.top_3_hit) / len(positives) * 100.0, 2) if positives else 0.0
    no_ctx_rate = round(sum(1 for r in negatives if r.correct_no_context) / len(negatives) * 100.0, 2) if negatives else 0.0
    leaks = sum(1 for r in results if r.access_leak)

    p50_lat = round(float(np.percentile(warm_latencies, 50)), 2) if warm_latencies else 0.0
    p95_lat = round(float(np.percentile(warm_latencies, 95)), 2) if warm_latencies else 0.0
    avg_lat = round(float(np.mean(warm_latencies)), 2) if warm_latencies else 0.0

    return {
        "threshold": threshold if threshold is not None else settings.rag_score_threshold,
        "total_cases": len(results),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "top_1_accuracy_pct": top_1_acc,
        "top_3_recall_pct": top_3_rec,
        "correct_no_context_rate_pct": no_ctx_rate,
        "access_leaks": leaks,
        "cold_start_latency_ms": round(cold_start_ms, 2),
        "avg_warm_latency_ms": avg_lat,
        "p50_warm_latency_ms": p50_lat,
        "p95_warm_latency_ms": p95_lat,
    }


def _save_evaluation_results(
    metrics: dict[str, Any],  # type: ignore[misc]
    results: list[CaseResult],
    multi_threshold_metrics: list[dict[str, Any]] | None = None,  # type: ignore[misc]
) -> Path:
    """Saves benchmark results and metrics to eval_results/<timestamp>.json."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = RESULTS_DIR / f"{timestamp}.json"

    payload = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "flags": {
            "chunking_mode": settings.chunking_mode,
            "hybrid_search": settings.hybrid_search,
            "reranker": settings.reranker,
            "query_rewrite": settings.query_rewrite,
            "grader": settings.grader,
            "orchestrator": settings.orchestrator,
        },
        "primary_metrics": metrics,
        "multi_threshold_comparison": multi_threshold_metrics or [],
        "cases": [asdict(r) for r in results],
    }

    with out_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    return out_file


def _print_report(metrics: dict[str, Any], results: list[CaseResult], out_file: Path) -> None:  # type: ignore[misc]
    """Prints formatted summary and category breakdown tables to stdout."""
    print("\n" + "═" * 80)
    print("      RAG RETRIEVAL EVALUATION HARNESS — CORRECTED BASELINE (CHUNK TRUTH)     ")
    print("═" * 80)
    print(f"Evaluation Threshold         : {metrics['threshold']}")
    print(f"Total Test Cases             : {metrics['total_cases']}")
    print(f"Top-1 Accuracy (Positives)   : {metrics['top_1_accuracy_pct']}%  [Chunk-level Phrase Hit]")
    print(f"Top-3 Recall (Positives)     : {metrics['top_3_recall_pct']}%  [Chunk-level Phrase Hit]")
    print(f"Correct No-Context (Negs)    : {metrics['correct_no_context_rate_pct']}%")
    print(f"Access Leaks (Must be 0)     : {metrics['access_leaks']}")
    print(f"Cold Start Latency           : {metrics['cold_start_latency_ms']} ms")
    print(f"Warm Query Latency (p50/p95) : {metrics['p50_warm_latency_ms']} ms / {metrics['p95_warm_latency_ms']} ms (avg {metrics['avg_warm_latency_ms']} ms)")
    print("─" * 80)

    # Category breakdown
    categories = sorted({r.category for r in results})
    print(f"{'Category':<16} | {'Count':<6} | {'Top-1 Hit / Correct':<22} | {'Leaks':<6}")
    print("─" * 80)
    for cat in categories:
        cat_items = [r for r in results if r.category == cat]
        if cat in ("single_turn", "multi_turn", "ambiguous"):
            passed = sum(1 for r in cat_items if r.top_1_hit)
            metric_str = f"{passed}/{len(cat_items)} ({passed / len(cat_items) * 100:.1f}%)"
        else:
            passed = sum(1 for r in cat_items if r.correct_no_context)
            metric_str = f"{passed}/{len(cat_items)} ({passed / len(cat_items) * 100:.1f}%)"
        leaks = sum(1 for r in cat_items if r.access_leak)
        print(f"{cat:<16} | {len(cat_items):<6} | {metric_str:<22} | {leaks:<6}")

    print("═" * 80)
    print(f"Results saved to: {out_file.as_posix()}\n")


def _print_threshold_comparison(multi_metrics: list[dict[str, Any]]) -> None:  # type: ignore[misc]
    """Prints a comparative table displaying retrieval metrics across multiple thresholds."""
    print("\n" + "═" * 80)
    print("                 THRESHOLD COMPARATIVE EVALUATION TABLE                       ")
    print("═" * 80)
    header = f"{'Threshold':<16} | {'Top-1 Acc':<12} | {'Top-3 Rec':<12} | {'No-Context':<12} | {'Leaks':<6}"
    print(header)
    print("─" * 80)
    for m in multi_metrics:
        t_label = f"{m['threshold']}" if m['threshold'] > 0 else "None (0.0)"
        row = f"{t_label:<16} | {m['top_1_accuracy_pct']:>9.1f}% | {m['top_3_recall_pct']:>9.1f}% | {m['correct_no_context_rate_pct']:>9.1f}% | {m['access_leaks']:>5}"
        print(row)
    print("═" * 80 + "\n")


def run_evaluation(primary_threshold: float = 0.77) -> dict[str, Any]:  # type: ignore[misc]
    """Runs evaluation benchmark across thresholds and returns primary metrics dictionary."""
    cases = _load_eval_cases()

    # Measure cold start load once
    t_cold = time.perf_counter()
    _ = retrieve("ping initialization cold query", role="employee")
    cold_start_ms = (time.perf_counter() - t_cold) * 1000.0

    # Evaluate at multiple thresholds: current .env, 0.77, and no threshold (0.0)
    test_thresholds = [settings.rag_score_threshold, 0.77, 0.0]
    # Deduplicate while preserving order
    unique_thresholds = list(dict.fromkeys(test_thresholds))

    multi_metrics = []
    primary_results: list[CaseResult] = []
    primary_metrics: dict[str, Any] = {}

    for thresh in unique_thresholds:
        res = [_evaluate_single_case(c, threshold=thresh) for c in cases]
        metrics = _compute_summary_metrics(res, cold_start_ms=cold_start_ms, threshold=thresh)
        multi_metrics.append(metrics)
        if round(thresh, 2) == round(primary_threshold, 2):
            primary_results = res
            primary_metrics = metrics

    if not primary_metrics:
        res = [_evaluate_single_case(c, threshold=primary_threshold) for c in cases]
        primary_metrics = _compute_summary_metrics(res, cold_start_ms=cold_start_ms, threshold=primary_threshold)
        primary_results = res

    out_file = _save_evaluation_results(primary_metrics, primary_results, multi_metrics)
    _print_threshold_comparison(multi_metrics)
    _print_report(primary_metrics, primary_results, out_file)
    return primary_metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAG retrieval benchmark evaluation.")
    parser.add_argument("--threshold", type=float, default=0.77, help="Primary score threshold (default: 0.77)")
    args = parser.parse_args()

    metrics_summary = run_evaluation(primary_threshold=args.threshold)
    sys.exit(0 if metrics_summary["access_leaks"] == 0 else 1)
