"""Evaluation script running the golden dataset against the RAG retrieval pipeline."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from app.rag.retriever import RetrievedChunk, retrieve

GOLDEN_FILE = Path("tests/golden_set.yaml")


def _load_golden_set() -> list[dict[str, Any]]:  # type: ignore[misc]
    """Loads evaluation test cases from YAML definition."""
    if not GOLDEN_FILE.exists():
        print(f"Error: Golden set file '{GOLDEN_FILE}' not found.", file=sys.stderr)
        sys.exit(1)
    with GOLDEN_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def _evaluate_case(case: dict[str, Any]) -> tuple[bool, list[RetrievedChunk], str]:  # type: ignore[misc]
    """Evaluates a single golden case; returns (passed, retrieved_chunks, error_reason)."""
    question: str = case["question"]
    role: str = case["role"]
    expected_sources: list[str] = case.get("expected_sources", [])
    must_not: list[str] = case.get("must_not_retrieve", [])
    case_type: str = case.get("type", "")

    chunks = retrieve(question, role=role)
    retrieved_sources = list(dict.fromkeys(c.source for c in chunks))

    for forbidden in must_not:
        if forbidden in retrieved_sources:
            return False, chunks, f"Retrieved forbidden source '{forbidden}'"

    for expected in expected_sources:
        if expected not in retrieved_sources:
            return False, chunks, f"Missing expected source '{expected}' in top-k"

    if case_type in ("not_in_docs", "off_topic") and len(chunks) > 0:
        return False, chunks, f"Expected 0 chunks for {case_type}, got {len(chunks)}"

    return True, chunks, ""


def _print_results_table(
    cases: list[dict[str, Any]],  # type: ignore[misc]
    results: list[tuple[bool, list[RetrievedChunk], str]],
) -> None:
    """Prints formatted summary table of golden evaluation results."""
    print("\n" + "═" * 80)
    print("GOLDEN DATASET RETRIEVAL EVALUATION TABLE")
    print("═" * 80)
    print(f"{'ID':<4} | {'Type':<12} | {'Role':<8} | {'Status':<6} | {'Top Score':<9} | {'Sources'}")
    print("─" * 80)

    for case, (passed, chunks, _) in zip(cases, results):
        status_str = "PASS" if passed else "FAIL"
        top_score = f"{chunks[0].score:.4f}" if chunks else "N/A"
        sources_str = ", ".join(dict.fromkeys(c.source for c in chunks)) or "None"
        print(
            f"{case['id']:<4} | {case['type']:<12} | {case['role']:<8} | "
            f"{status_str:<6} | {top_score:<9} | {sources_str}"
        )
    print("─" * 80)


def _print_failures(
    cases: list[dict[str, Any]],  # type: ignore[misc]
    results: list[tuple[bool, list[RetrievedChunk], str]],
) -> int:
    """Prints detailed diagnostics for failed cases and returns total failure count."""
    failures = [(c, r) for c, r in zip(cases, results) if not r[0]]
    if not failures:
        print("\nAll evaluation cases PASSED successfully! (30/30)\n")
        return 0

    print(f"\n⚠️  {len(failures)} CASE(S) FAILED:\n" + "─" * 80)
    for case, (_, chunks, reason) in failures:
        print(f"\n[Q{case['id']}] {case['question']}")
        print(f"  Role     : {case['role']}")
        print(f"  Expected : {case.get('expected_sources', [])}")
        print(f"  Reason   : {reason}")
        print("  Retrieved Chunks:")
        for idx, chunk in enumerate(chunks):
            print(f"    [{idx}] score={chunk.score:.4f} source={chunk.source}")
    print("─" * 80 + "\n")
    return len(failures)


def main() -> None:
    """Runs golden set evaluation and exits with appropriate return code."""
    cases = _load_golden_set()
    results = [_evaluate_case(case) for case in cases]
    _print_results_table(cases, results)
    fail_count = _print_failures(cases, results)
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
