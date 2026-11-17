"""
Reads Markdown docs, splits by section with overlap, embeds, and upserts into ChromaDB.

Run once (or after doc changes):
    python -m app.rag.ingest
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import frontmatter
import yaml
import chromadb

from app.config import settings
from app.rag.embedder import get_embedding_function

DOCS_DIR = Path("data/docs")
MAPPING_FILE = DOCS_DIR / "meta_mapping.yaml"

# Chunking parameters
_MAX_WORDS_PER_CHUNK = 150   # target chunk size in words
_OVERLAP_WORDS = 50          # words shared between adjacent chunks


# ── Metadata helpers ──────────────────────────────────────────────────────────

def _load_meta_mapping() -> dict[str, dict[str, str]]:
    """Loads the fallback source→metadata mapping from meta_mapping.yaml."""
    if not MAPPING_FILE.exists():
        return {}
    with MAPPING_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _resolve_meta(
    filename: str,
    fm: dict[str, str],
    mapping: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    """
    Returns (source, department, access_level) — frontmatter takes priority
    over the mapping file, which falls back to safe defaults.
    """
    fallback = mapping.get(filename, {})
    source = fm.get("source") or filename
    department = fm.get("department") or fallback.get("department", "general")
    access_level = fm.get("access_level") or fallback.get("access_level", "all")
    return source, department, access_level


# ── Markdown chunking (legacy section mode) ──────────────────────────────────

def _split_sections(text: str) -> list[tuple[str, str]]:
    """Splits markdown body into (header, content) pairs on H1/H2/H3 boundaries."""
    header_re = re.compile(r"^(#{1,3}\s+.+)$", re.MULTILINE)
    matches = list(header_re.finditer(text))

    if not matches:
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[body_start:body_end].strip()
        if content:
            sections.append((title, content))

    return sections


def _chunk_section(title: str, content: str) -> list[str]:
    """Returns one or more word-window chunks for a section with fixed overlap."""
    words = content.split()
    prefix = f"{title}\n" if title else ""

    if len(words) <= _MAX_WORDS_PER_CHUNK:
        return [f"{prefix}{content}"]

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + _MAX_WORDS_PER_CHUNK, len(words))
        chunks.append(f"{prefix}" + " ".join(words[start:end]))
        if end == len(words):
            break
        start = end - _OVERLAP_WORDS
    return chunks


# ── Markdown chunking (hierarchical table-aware mode) ─────────────────────────

def _split_hierarchical_sections(text: str) -> list[tuple[str, str]]:
    """Splits markdown into (breadcrumb, content) sections tracking H1/H2/H3 hierarchy."""
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, str]] = []
    curr_lines: list[str] = []

    def get_breadcrumb() -> str:
        return " > ".join(title for _, title in stack)

    for line in lines:
        h_match = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if h_match:
            if curr_lines:
                sections.append((get_breadcrumb(), "\n".join(curr_lines).strip()))
                curr_lines = []
            level = len(h_match.group(1))
            title = h_match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            curr_lines.append(line)

    if curr_lines:
        sections.append((get_breadcrumb(), "\n".join(curr_lines).strip()))

    return [(b, c) for b, c in sections if c]


def _split_blocks(content: str) -> list[str]:
    """Splits section content into atomic blocks (paragraphs, markdown tables, lists)."""
    lines = content.splitlines()
    blocks: list[str] = []
    curr: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_tbl = stripped.startswith("|") and stripped.endswith("|")
        if is_tbl:
            if not in_table and curr:
                blocks.append("\n".join(curr).strip())
                curr = []
            in_table = True
            curr.append(line)
        else:
            if in_table and curr:
                blocks.append("\n".join(curr).strip())
                curr = []
                in_table = False
            if not stripped:
                if curr:
                    blocks.append("\n".join(curr).strip())
                    curr = []
            else:
                curr.append(line)

    if curr:
        blocks.append("\n".join(curr).strip())

    return [b for b in blocks if b]


def _chunk_hierarchical_blocks(
    breadcrumb: str, content: str, max_words: int = _MAX_WORDS_PER_CHUNK
) -> list[str]:
    """Assembles logical blocks into word-bounded chunks with preserved table structures."""
    blocks = _split_blocks(content)
    if not blocks:
        return []

    prefix = f"[{breadcrumb}]\n\n" if breadcrumb else ""
    chunks: list[str] = []
    curr_blocks: list[str] = []
    curr_count = 0

    for b in blocks:
        b_count = len(b.split())
        if curr_blocks and (curr_count + b_count > max_words):
            chunks.append(prefix + "\n\n".join(curr_blocks))
            curr_blocks = [b]
            curr_count = b_count
        else:
            curr_blocks.append(b)
            curr_count += b_count

    if curr_blocks:
        chunks.append(prefix + "\n\n".join(curr_blocks))
    return chunks


def create_chunks(content: str, mode: str | None = None) -> list[str]:
    """Dispatches document chunking to section or hierarchical mode based on configuration."""
    active_mode = mode or settings.chunking_mode
    if active_mode == "hierarchical":
        hierarchical_sections = _split_hierarchical_sections(content)
        h_chunks: list[str] = []
        for breadcrumb, body in hierarchical_sections:
            h_chunks.extend(_chunk_hierarchical_blocks(breadcrumb, body))
        return h_chunks

    sections = _split_sections(content)
    legacy_chunks: list[str] = []
    for title, body in sections:
        legacy_chunks.extend(_chunk_section(title, body))
    return legacy_chunks


# ── ChromaDB helpers ──────────────────────────────────────────────────────────

def _get_collection() -> chromadb.Collection:
    """Creates or opens the persistent ChromaDB collection, verifying embedding model metadata."""
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=get_embedding_function(),
        metadata={"hnsw:space": "cosine", "embedding_model": settings.embedding_model},
    )
    stored_model = collection.metadata.get("embedding_model") if collection.metadata else None
    if stored_model and stored_model != settings.embedding_model:
        raise RuntimeError(
            f"Collection {settings.chroma_collection!r} has model {stored_model!r}, "
            f"cannot mix with configured {settings.embedding_model!r}"
        )
    return collection


# ── Main ingestion ────────────────────────────────────────────────────────────

def ingest(docs_dir: Path = DOCS_DIR, mode: str | None = None) -> int:
    """Reads all .md files in docs_dir, chunks them according to mode, and upserts into ChromaDB."""
    active_mode = mode or settings.chunking_mode
    mapping = _load_meta_mapping()
    collection = _get_collection()
    total = 0

    for doc_path in sorted(docs_dir.glob("*.md")):
        post = frontmatter.load(str(doc_path))
        source, department, access_level = _resolve_meta(
            doc_path.name, dict(post.metadata), mapping
        )

        chunks = create_chunks(post.content, mode=active_mode)
        ids = [f"{source}::chunk_{i}" for i in range(len(chunks))]
        metadatas: list[dict[str, str | int]] = [
            {
                "source": source,
                "department": department,
                "access_level": access_level,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]

        try:
            collection.delete(where={"source": source})
        except Exception:
            pass

        collection.upsert(documents=chunks, ids=ids, metadatas=metadatas)
        total += len(chunks)
        print(f"  ✓ {source} ({active_mode}): {len(chunks)} chunks")

    try:
        from app.rag.retriever import _COLLECTION_CACHE  # noqa: PLC0415
        _COLLECTION_CACHE.clear()
    except Exception:
        pass

    print(f"\nIngestion complete — {total} chunks stored in '{settings.chroma_collection}'.")
    return total


if __name__ == "__main__":
    print(f"Ingesting from: {DOCS_DIR.resolve()}\n")
    count = ingest()
    sys.exit(0 if count > 0 else 1)

