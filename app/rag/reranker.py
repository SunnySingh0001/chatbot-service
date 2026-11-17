"""Local cross-encoder rerankers supporting GTE-multilingual (b) and mMiniLMv2 (c).

Model weights and tokenizers are stored locally, verified via SHA256 checksums,
and run via ONNX Runtime without external APIs or runtime network access.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from app.config import settings
from app.rag.retriever import RetrievedChunk

_logger = logging.getLogger("chatbot.reranker")

MODEL_CONFIGS: dict[str, dict[str, str | Path]] = {
    "b": {
        "dir": Path("models/gte-multilingual-reranker-base"),
        "onnx_name": "model_int8.onnx",
        "revision": "ee64367e35a2db0da46bb6497e13a18f8bd585cb",
        "onnx_sha256": "ccf51dba7f8aa9205753761cfaa68c55f741792501463a3bf25d7e5bcdac7c35",
        "tok_sha256": "3ffb37461c391f096759f4a9bbbc329da0f36952f88bab061fcf84940c022e98",
    },
    "c": {
        "dir": Path("models/mmarco-mMiniLMv2-L12-H384-v1"),
        "onnx_name": "model_quint8_avx2.onnx",
        "revision": "1427fd652930e4ba29e8149678df786c240d8825",
        "onnx_sha256": "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
        "tok_sha256": "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
    },
}


def _verify_file_checksum(file_path: Path, expected_sha256: str) -> None:
    """Verifies that a local model file exists and matches its expected SHA256 hash."""
    if not file_path.exists():
        raise FileNotFoundError(f"Required reranker asset not found: {file_path}")
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Checksum mismatch for {file_path}: expected {expected_sha256}, got {actual}"
        )


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Computes standard sigmoid mapping raw cross-encoder logits into probabilities in [0, 1]."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


class CrossEncoderReranker:
    """Model-agnostic singleton cross-encoder reranker using ONNX Runtime."""

    def __init__(self, model_key: str = "b") -> None:
        """Initializes tokenizer and ONNX inference session after validating checksums."""
        cfg = MODEL_CONFIGS.get(model_key.lower())
        if not cfg:
            raise ValueError(f"Unknown reranker model {model_key!r}; must be 'b' or 'c'.")

        model_dir = Path(cfg["dir"])
        onnx_path = model_dir / str(cfg["onnx_name"])
        tok_path = model_dir / "tokenizer.json"
        _verify_file_checksum(onnx_path, str(cfg["onnx_sha256"]))
        _verify_file_checksum(tok_path, str(cfg["tok_sha256"]))

        self.model_key = model_key.lower()
        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_padding(pad_token="<pad>", pad_id=1)
        self._tokenizer.enable_truncation(max_length=512)

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if settings.rerank_intra_op_threads > 0:
            opts.intra_op_num_threads = settings.rerank_intra_op_threads
        if settings.rerank_inter_op_threads > 0:
            opts.inter_op_num_threads = settings.rerank_inter_op_threads
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = [inp.name for inp in self._session.get_inputs()]

    def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        """Encodes (query, passage) pairs, runs inference, and returns sigmoid probabilities."""
        if not passages:
            return []
        max_tok = settings.rerank_max_tokens
        self._tokenizer.enable_truncation(max_length=max_tok)
        self._tokenizer.enable_padding(pad_token="<pad>", pad_id=1)

        indexed = sorted(enumerate(passages), key=lambda item: len(item[1]))
        sorted_pairs = [(query, p) for _, p in indexed]
        encoded = self._tokenizer.encode_batch(sorted_pairs)

        feed = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
        }
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)

        raw_logits = self._session.run(None, feed)[0].reshape(-1)
        sorted_probs = _sigmoid(raw_logits)

        probs = [0.0] * len(passages)
        for (orig_idx, _), prob in zip(indexed, sorted_probs):
            probs[orig_idx] = round(float(prob), 4)
        return probs


_RERANKER_CACHE: dict[str, CrossEncoderReranker] = {}


def clear_reranker_cache() -> None:
    """Clears the cached reranker singleton instances."""
    _RERANKER_CACHE.clear()


def get_reranker(model_key: str | None = None) -> CrossEncoderReranker:
    """Returns cached singleton cross-encoder reranker instance for the requested model."""
    chosen = (model_key or settings.reranker_model).lower()
    cache_key = f"{chosen}_{settings.rerank_intra_op_threads}_{settings.rerank_inter_op_threads}"
    if cache_key not in _RERANKER_CACHE:
        _RERANKER_CACHE[cache_key] = CrossEncoderReranker(model_key=chosen)
    return _RERANKER_CACHE[cache_key]
    if chosen not in _RERANKER_CACHE:
        _RERANKER_CACHE[chosen] = CrossEncoderReranker(model_key=chosen)
    return _RERANKER_CACHE[chosen]


def rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    model_key: str | None = None,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Reranks candidate chunks with cross-encoder and returns top-k sorted by rerank_score."""
    if not chunks:
        return []
    reranker = get_reranker(model_key)
    passages = [c.text for c in chunks]
    scores = reranker.score_pairs(query, passages)

    scored_chunks = [
        RetrievedChunk(
            text=c.text,
            source=c.source,
            department=c.department,
            access_level=c.access_level,
            score=c.score,
            chunk_index=c.chunk_index,
            rerank_score=score,
        )
        for c, score in zip(chunks, scores)
    ]
    scored_chunks.sort(key=lambda item: (item.rerank_score or 0.0), reverse=True)
    limit = top_k if top_k is not None else settings.rerank_top_k
    return scored_chunks[:limit]
