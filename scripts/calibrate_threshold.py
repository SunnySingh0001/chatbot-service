"""Calibration script analyzing reranker score distributions and threshold separation."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import numpy as np
import yaml

from app.rag.reranker import get_reranker
from app.rag.retriever import retrieve

EVAL_YAML_PATH = Path("tests/rag_eval.yaml")


def _load_eval_cases() -> list[dict[str, Any]]:  # type: ignore[misc]
    """Loads all test cases from tests/rag_eval.yaml."""
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return data.get("cases", [])  # type: ignore[no-any-return]


def _calc_stats(scores: list[float]) -> dict[str, float]:
    """Calculates min, p05, median, and max for a score list."""
    if not scores:
        return {"min": 0.0, "p05": 0.0, "median": 0.0, "max": 0.0}
    arr = np.array(scores)
    return {
        "min": round(float(np.min(arr)), 4),
        "p05": round(float(np.percentile(arr, 5)), 4),
        "median": round(float(np.median(arr)), 4),
        "max": round(float(np.max(arr)), 4),
    }


def _score_single_case(case: dict[str, Any], reranker: Any) -> tuple[str, float]:  # type: ignore[misc]
    """Fetches hybrid candidates for a case and calculates its highest reranker score."""
    q = case["question"]
    role = case.get("role", "employee")
    chunks = retrieve(
        q,
        role=role,
        score_threshold=0.0,
        mode="hierarchical",
        table_max_rows=4,
        top_k=8,
        hybrid_search=True,
        reranker=False,
    )
    if not chunks:
        return case.get("category", ""), 0.0
    scores = reranker.score_pairs(q, [c.text for c in chunks])
    return case.get("category", ""), max(scores) if scores else 0.0


def _score_all_cases(
    cases: list[dict[str, Any]],  # type: ignore[misc]
    model_key: str,
) -> dict[str, list[float]]:
    """Runs hybrid retrieval and scores top candidates using the specified reranker."""
    reranker = get_reranker(model_key)
    grouped: dict[str, list[float]] = {
        "positives": [],
        "easy_negatives": [],
        "hard_negatives": [],
    }
    for case in cases:
        cat, top_score = _score_single_case(case, reranker)
        if cat in ("single_turn", "multi_turn", "ambiguous"):
            grouped["positives"].append(top_score)
        elif cat == "easy_negative":
            grouped["easy_negatives"].append(top_score)
        elif cat == "hard_negative":
            grouped["hard_negatives"].append(top_score)
    return grouped


def _evaluate_thresholds(
    pos: list[float], easy_neg: list[float], hard_neg: list[float]
) -> tuple[float, list[dict[str, Any]]]:  # type: ignore[misc]
    """Sweeps candidate thresholds and tracks error counts across positives and negatives."""
    all_neg = easy_neg + hard_neg
    candidate_thresholds = [round(t, 2) for t in np.linspace(0.05, 0.95, 19)]
    rows: list[dict[str, Any]] = []  # type: ignore[misc]
    best_thresh = 0.50
    min_errors = 9999

    for thresh in candidate_thresholds:
        fn_pos = sum(1 for s in pos if s < thresh)
        fp_easy = sum(1 for s in easy_neg if s >= thresh)
        fp_hard = sum(1 for s in hard_neg if s >= thresh)
        total_errors = fn_pos + fp_easy + fp_hard

        rows.append(
            {
                "threshold": thresh,
                "fn_positives": fn_pos,
                "fp_easy": fp_easy,
                "fp_hard": fp_hard,
                "total_errors": total_errors,
            }
        )
        if total_errors < min_errors:
            min_errors = total_errors
            best_thresh = thresh

    return best_thresh, rows


def calibrate_model(model_key: str, model_name: str) -> None:
    """Executes calibration analysis for a reranker model and prints distribution table."""
    cases = _load_eval_cases()
    grouped = _score_all_cases(cases, model_key)

    pos_stats = _calc_stats(grouped["positives"])
    easy_stats = _calc_stats(grouped["easy_negatives"])
    hard_stats = _calc_stats(grouped["hard_negatives"])

    print(f"\n=======================================================")
    print(f"CALIBRATION: {model_name} (RERANKER_MODEL={model_key})")
    print(f"=======================================================")
    print("Score Distributions (Sigmoid Probabilities in [0, 1]):")
    print(f"  Positives      (N={len(grouped['positives'])}): min={pos_stats['min']:.4f}, p05={pos_stats['p05']:.4f}, med={pos_stats['median']:.4f}, max={pos_stats['max']:.4f}")
    print(f"  Easy Negatives (N={len(grouped['easy_negatives'])}): min={easy_stats['min']:.4f}, p05={easy_stats['p05']:.4f}, med={easy_stats['median']:.4f}, max={easy_stats['max']:.4f}")
    print(f"  Hard Negatives (N={len(grouped['hard_negatives'])}): min={hard_stats['min']:.4f}, p05={hard_stats['p05']:.4f}, med={hard_stats['median']:.4f}, max={hard_stats['max']:.4f}")

    best_thresh, sweep_rows = _evaluate_thresholds(
        grouped["positives"], grouped["easy_negatives"], grouped["hard_negatives"]
    )
    print(f"\nBest Threshold (minimizing total errors): {best_thresh:.2f}")
    print("\nThreshold Sweep:")
    print("Thresh | False Neg Pos (miss) | False Pos Easy | False Pos Hard | Total Errors")
    print("-------|----------------------|----------------|----------------|-------------")
    for r in sweep_rows:
        flag = " *" if r["threshold"] == best_thresh else ""
        print(f" {r['threshold']:.2f}  | {r['fn_positives']:20d} | {r['fp_easy']:14d} | {r['fp_hard']:14d} | {r['total_errors']:11d}{flag}")


def main() -> None:
    """Runs calibration across both Option B (GTE) and Option C (mMiniLMv2)."""
    calibrate_model("b", "Option B (gte-multilingual-reranker-base)")
    calibrate_model("c", "Option C (mmarco-mMiniLMv2-L12-H384-v1)")


if __name__ == "__main__":
    main()
