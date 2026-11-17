"""Local embedding functions supporting multilingual-e5-small and legacy MiniLM.

Model weights and tokenizers are stored locally and validated via SHA256 checksums.
Prefixes ('passage: ' for docs, 'query: ' for questions) are applied strictly.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from tokenizers import Tokenizer

from app.config import settings

MODEL_DIR = Path("models/multilingual-e5-small")
MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
ONNX_SHA256 = "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193"
TOKENIZER_SHA256 = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"


def _verify_file_checksum(file_path: Path, expected_sha256: str) -> None:
    """Verifies that a local model file exists and matches its expected SHA256 hash."""
    if not file_path.exists():
        raise FileNotFoundError(f"Required model asset not found: {file_path}")
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"Checksum mismatch for {file_path}: expected {expected_sha256}, got {actual}")


def _mean_pooling(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Computes mean pooling of hidden states weighted by the attention mask."""
    mask_expanded = np.broadcast_to(np.expand_dims(attention_mask, -1), last_hidden_state.shape).astype(float)
    sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)
    sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
    return sum_embeddings / sum_mask


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Normalizes vectors to unit length using L2 norm."""
    norm = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.where(norm == 0, 1e-12, vectors / norm)


class MultilingualE5EmbeddingFunction(EmbeddingFunction[Documents]):
    """Local ONNX-backed embedding function for intfloat/multilingual-e5-small."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        """Initializes tokenizer and ONNX inference session after validating checksums."""
        onnx_path = model_dir / "model_quantized.onnx"
        tok_path = model_dir / "tokenizer.json"
        _verify_file_checksum(onnx_path, ONNX_SHA256)
        _verify_file_checksum(tok_path, TOKENIZER_SHA256)

        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_padding(pad_token="[PAD]", pad_id=0)
        self._tokenizer.enable_truncation(max_length=512)

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = [i.name for i in self._session.get_inputs()]

    def _embed(self, texts: list[str], prefix: str) -> Embeddings:
        """Encodes texts with the specified prefix, runs ONNX inference, and returns L2 vectors."""
        processed = [
            t if t.startswith("passage: ") or t.startswith("query: ") else f"{prefix}{t}"
            for t in texts
        ]
        encoded = self._tokenizer.encode_batch(processed)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feed_dict = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feed_dict["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(None, feed_dict)
        pooled = _mean_pooling(outputs[0], attention_mask)
        normalized = _l2_normalize(pooled)
        return [np.array(v, dtype=np.float32) for v in normalized]

    def __call__(self, input: Documents) -> Embeddings:
        """Embeds document chunks for ingestion using 'passage: ' prefix."""
        return self._embed(list(input), prefix="passage: ")

    def embed_query(self, input: Documents) -> Embeddings:
        """Embeds search query text using 'query: ' prefix."""
        return self._embed(list(input), prefix="query: ")


_EMBEDDING_FUNCTION_CACHE: dict[str, EmbeddingFunction[Documents]] = {}


def get_embedding_function(model_name: str | None = None) -> EmbeddingFunction[Documents]:
    """Factory returning cached singleton embedding function based on configuration switch."""
    chosen = model_name or settings.embedding_model
    if chosen not in _EMBEDDING_FUNCTION_CACHE:
        if chosen == "all-MiniLM-L6-v2":
            _EMBEDDING_FUNCTION_CACHE[chosen] = ONNXMiniLM_L6_V2()
        elif chosen in ("multilingual-e5-small", "intfloat/multilingual-e5-small"):
            _EMBEDDING_FUNCTION_CACHE[chosen] = MultilingualE5EmbeddingFunction()
        else:
            raise ValueError(f"Unknown or unsupported embedding model: {chosen!r}")
    return _EMBEDDING_FUNCTION_CACHE[chosen]
