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


def _slice_table_rows(table_lines: list[str], max_rows: int) -> list[str]:
    """Slices table data rows into sub-tables, repeating header and separator on each group."""
    if max_rows <= 0 or len(table_lines) <= 2:
        return ["\n".join(table_lines)]
    header, sep = table_lines[0], table_lines[1]
    data_rows = table_lines[2:]
    slices: list[str] = []
    for start in range(0, len(data_rows), max_rows):
        sub_rows = data_rows[start : start + max_rows]
        slices.append("\n".join([header, sep] + sub_rows))
    return slices


def _is_conditional_continuation(lines: list[str], current_index: int) -> bool:
    """Checks whether the next non-empty line continues an active conditional clause."""
    next_line = next((lines[k].strip() for k in range(current_index + 1, len(lines)) if lines[k].strip()), "")
    pattern = r"^(?:else\b|exception\b|otherwise\b|unless\b|elif\b)"
    return bool(re.match(pattern, next_line, re.IGNORECASE))


def _is_procedure_continuation(lines: list[str], current_index: int) -> bool:
    """Checks whether the next non-empty line continues a numbered procedure sequence."""
    next_line = next((lines[k].strip() for k in range(current_index + 1, len(lines)) if lines[k].strip()), "")
    return bool(re.match(r"^\d+\.\s+", next_line))


def _split_blocks(content: str, max_table_rows: int = 0) -> list[str]:
    """Splits section content into atomic blocks (paragraphs, markdown tables, lists, rules)."""
    lines = content.splitlines()
    blocks: list[str] = []
    curr: list[str] = []
    in_table, in_conditional, in_procedure = False, False, False

    def flush() -> None:
        nonlocal curr, in_table, in_conditional, in_procedure
        if curr:
            text = "\n".join(curr).strip()
            if text:
                blocks.append(text)
            curr = []
        in_table, in_conditional, in_procedure = False, False, False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_tbl = stripped.startswith("|") and stripped.endswith("|")

        if is_tbl:
            if not in_table:
                flush()
                in_table = True
            curr.append(line)
        elif in_table:
            table_lines = [l for l in curr if l.strip().startswith("|")]
            curr, in_table = [], False
            blocks.extend(_slice_table_rows(table_lines, max_table_rows))
            continue
        elif re.match(r"^\d+\.\s+", stripped):
            if not in_procedure and curr and not in_conditional:
                flush()
            in_procedure = True
            curr.append(line)
        elif in_procedure:
            if not stripped and not _is_procedure_continuation(lines, i):
                flush()
            else:
                curr.append(line)
        elif re.match(r"^(?:if\b|where\b)", stripped, re.IGNORECASE):
            if curr and not in_conditional:
                flush()
            in_conditional = True
            curr.append(line)
        elif in_conditional:
            if not stripped and not _is_conditional_continuation(lines, i):
                flush()
            else:
                curr.append(line)
        elif not stripped:
            if curr:
                flush()
        else:
            curr.append(line)
        i += 1

    flush()
    return [b for b in blocks if b]


def _chunk_hierarchical_blocks(
    breadcrumb: str,
    content: str,
    max_words: int = _MAX_WORDS_PER_CHUNK,
    max_table_rows: int = 0,
) -> list[str]:
    """Assembles logical blocks into word-bounded chunks with preserved table and rule structures."""
    blocks = _split_blocks(content, max_table_rows=max_table_rows)
    if not blocks:
        return []

    prefix = f"[{breadcrumb}]\n\n" if breadcrumb else ""
    chunks: list[str] = []
    curr_blocks: list[str] = []
    curr_count = 0

    for b in blocks:
        is_tbl = b.strip().startswith("|") and b.strip().endswith("|")
        b_count = len(b.split())
        if is_tbl and max_table_rows > 0:
            if curr_blocks:
                chunks.append(prefix + "\n\n".join(curr_blocks))
                curr_blocks = []
                curr_count = 0
            chunks.append(prefix + b)
            continue

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


def create_chunks(
    content: str,
    mode: str | None = None,
    max_table_rows: int | None = None,
) -> list[str]:
    """Dispatches document chunking to section or hierarchical mode based on configuration."""
    active_mode = mode or settings.chunking_mode
    active_rows = (
        max_table_rows
        if max_table_rows is not None
        else settings.table_max_rows_per_chunk
    )
    if active_mode == "hierarchical":
        hierarchical_sections = _split_hierarchical_sections(content)
        h_chunks: list[str] = []
        for breadcrumb, body in hierarchical_sections:
            h_chunks.extend(
                _chunk_hierarchical_blocks(
                    breadcrumb, body, max_table_rows=active_rows
                )
            )
        return h_chunks

    sections = _split_sections(content)
    legacy_chunks: list[str] = []
    for title, body in sections:
        legacy_chunks.extend(_chunk_section(title, body))
    return legacy_chunks


# ── ChromaDB helpers ──────────────────────────────────────────────────────────

def _get_collection(
    mode: str | None = None, max_table_rows: int | None = None
) -> chromadb.Collection:
    """Creates or opens the persistent ChromaDB collection, verifying model and mode metadata."""
    from app.rag.retriever import get_collection_name  # noqa: PLC0415

    active_mode = mode or settings.chunking_mode
    active_rows = (
        max_table_rows
        if max_table_rows is not None
        else settings.table_max_rows_per_chunk
    )
    col_name = get_collection_name(active_mode, table_max_rows=active_rows)
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_or_create_collection(
        name=col_name,
        embedding_function=get_embedding_function(),
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": settings.embedding_model,
            "chunking_mode": active_mode,
            "table_max_rows_per_chunk": active_rows,
        },
    )
    if collection.metadata:
        stored_model = collection.metadata.get("embedding_model")
        if stored_model and stored_model != settings.embedding_model:
            raise RuntimeError(
                f"Collection {col_name!r} has model {stored_model!r}, "
                f"cannot mix with configured {settings.embedding_model!r}"
            )
        stored_mode = collection.metadata.get("chunking_mode")
        if stored_mode and stored_mode != active_mode:
            raise RuntimeError(
                f"Collection {col_name!r} has chunking mode {stored_mode!r}, "
                f"cannot mix with configured {active_mode!r}"
            )
    return collection


# ── Main ingestion ────────────────────────────────────────────────────────────

def ingest(
    docs_dir: Path = DOCS_DIR,
    mode: str | None = None,
    max_table_rows: int | None = None,
) -> int:
    """Reads all .md files in docs_dir, chunks them according to mode, and upserts into ChromaDB."""
    active_mode = mode or settings.chunking_mode
    active_rows = (
        max_table_rows
        if max_table_rows is not None
        else settings.table_max_rows_per_chunk
    )
    mapping = _load_meta_mapping()
    collection = _get_collection(mode=active_mode, max_table_rows=active_rows)
    total = 0

    for doc_path in sorted(docs_dir.glob("*.md")):
        post = frontmatter.load(str(doc_path))
        source, department, access_level = _resolve_meta(
            doc_path.name, dict(post.metadata), mapping
        )

        chunks = create_chunks(post.content, mode=active_mode, max_table_rows=active_rows)
        row_tag = f"_t{active_rows}" if (active_mode == "hierarchical" and active_rows > 0) else ""
        ids = [f"{source}::{active_mode}{row_tag}::chunk_{i}" for i in range(len(chunks))]
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
        print(f"  ✓ {source} ({active_mode}{row_tag}): {len(chunks)} chunks")

    try:
        from app.rag.retriever import invalidate_collection_cache  # noqa: PLC0415

        invalidate_collection_cache()
    except Exception:
        pass

    print(f"\nIngestion complete — {total} chunks stored in '{collection.name}'.")
    return total


if __name__ == "__main__":
    print(f"Ingesting from: {DOCS_DIR.resolve()}\n")
    count = ingest()
    sys.exit(0 if count > 0 else 1)
