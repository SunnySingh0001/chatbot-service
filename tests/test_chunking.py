"""Unit tests for chunking modes (section legacy vs hierarchical table-aware)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag.ingest import (
    _chunk_hierarchical_blocks,
    _chunk_section,
    _split_blocks,
    _split_hierarchical_sections,
    _split_sections,
    create_chunks,
)


def test_chunking_mode_config_validation() -> None:
    """Verifies that CHUNKING_MODE only accepts 'section' or 'hierarchical'."""
    s_default = Settings(chunking_mode="section")
    assert s_default.chunking_mode == "section"

    s_hier = Settings(chunking_mode="hierarchical")
    assert s_hier.chunking_mode == "hierarchical"

    with pytest.raises(ValidationError):
        Settings(chunking_mode="unsupported_mode")


def test_section_mode_legacy_behavior() -> None:
    """Verifies that section mode chunks by H1/H2 boundaries with fixed prefix."""
    sample = "# Header 1\nContent of first section.\n\n## Header 2\nContent of second section."
    sections = _split_sections(sample)
    assert len(sections) == 2
    assert sections[0][0] == "# Header 1"
    assert sections[1][0] == "## Header 2"

    chunks = create_chunks(sample, mode="section")
    assert len(chunks) == 2
    assert chunks[0].startswith("# Header 1\n")
    assert chunks[1].startswith("## Header 2\n")


def test_hierarchical_mode_preserves_breadcrumbs() -> None:
    """Verifies that hierarchical mode builds full H1 > H2 > H3 breadcrumb chains."""
    sample = (
        "# Company Handbook\nWelcome.\n\n"
        "## Leave Policy\nRules for leaves.\n\n"
        "### Sick Leave\n7 days per year."
    )
    sections = _split_hierarchical_sections(sample)
    assert len(sections) == 3
    assert sections[0][0] == "Company Handbook"
    assert sections[1][0] == "Company Handbook > Leave Policy"
    assert sections[2][0] == "Company Handbook > Leave Policy > Sick Leave"

    chunks = create_chunks(sample, mode="hierarchical")
    assert len(chunks) == 3
    assert chunks[2].startswith("[Company Handbook > Leave Policy > Sick Leave]\n\n")


def test_hierarchical_mode_preserves_markdown_tables() -> None:
    """Verifies that markdown table rows and newlines are preserved without flattening."""
    sample_table = (
        "## Compensation\n"
        "| Level | Salary |\n"
        "|-------|--------|\n"
        "| L1    | 10 LPA |\n"
        "| L2    | 15 LPA |\n"
    )
    blocks = _split_blocks(sample_table)
    table_blocks = [b for b in blocks if b.startswith("|") and b.endswith("|")]
    assert len(table_blocks) == 1
    assert "\n| Level | Salary |\n" in f"\n{table_blocks[0]}\n"
    assert "10 LPA" in table_blocks[0]
    assert "15 LPA" in table_blocks[0]

    chunks = create_chunks(sample_table, mode="hierarchical")
    assert len(chunks) == 1
    assert "| Level | Salary |" in chunks[0]
    assert "\n| L1    | 10 LPA |" in chunks[0]


def test_create_chunks_defaults_to_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies create_chunks respects default section mode when mode parameter is None."""
    sample = "# Main\nParagraph content here."
    chunks = create_chunks(sample, mode=None)
    assert len(chunks) == 1
    assert chunks[0].startswith("# Main\n")
