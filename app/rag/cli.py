"""
Development CLI for the RAG pipeline.

Usage:
    python -m app.rag.cli "What is the leave policy?" --role employee
    python -m app.rag.cli "salary bands kya hain?" --role hr
"""
from __future__ import annotations

import argparse

from app.rag.llm import stream_llm
from app.rag.prompt import build_messages, build_no_context_messages
from app.rag.retriever import RetrievedChunk, retrieve

_SEP = "─" * 60


def _parse_args() -> argparse.Namespace:
    """Parses CLI arguments: positional question and optional --role."""
    parser = argparse.ArgumentParser(
        description="RAG CLI — query company docs from the terminal"
    )
    parser.add_argument("question", help="The question to ask the bot")
    parser.add_argument(
        "--role",
        default="employee",
        choices=["employee", "hr", "it", "finance", "operations", "admin", "super-admin", "superadmin", "founder"],
        help="Caller's role (controls which docs are accessible). Default: employee",
    )
    return parser.parse_args()


def _print_chunks(chunks: list[RetrievedChunk]) -> None:
    """Prints each retrieved chunk with its score and source for inspection."""
    print(f"\n{_SEP}")
    print(f"RETRIEVED CHUNKS  ({len(chunks)} above threshold)")
    print(_SEP)
    if not chunks:
        print("  (none — similarity below threshold; will use 'I don't know' path)")
        return
    for i, chunk in enumerate(chunks):
        score_str = f"{chunk.score:.4f}" if chunk.score is not None else "N/A"
        print(
            f"\n[{i}]  score={score_str}  "
            f"source={chunk.source}  dept={chunk.department}"
        )
        print("  " + "·" * 40)
        # Show first 350 chars so the terminal stays readable
        preview = chunk.text[:350].replace("\n", "\n  ")
        suffix = "…" if len(chunk.text) > 350 else ""
        print(f"  {preview}{suffix}")


def _stream_answer(question: str, chunks: list[RetrievedChunk]) -> None:
    """Builds the prompt, streams tokens to stdout, then prints sources."""
    if chunks:
        messages = build_messages(question, chunks)
    else:
        messages = build_no_context_messages(question)

    print(f"\n{_SEP}")
    print("ANSWER")
    print(_SEP + "\n")

    for token in stream_llm(messages):
        print(token, end="", flush=True)
    print()   # newline after streaming ends

    unique_sources = list(dict.fromkeys(c.source for c in chunks))
    print(f"\n{_SEP}")
    print(f"SOURCES: {', '.join(unique_sources) if unique_sources else 'none'}")
    print(_SEP)


def main() -> None:
    """Entry point: retrieve → display chunks → stream answer."""
    args = _parse_args()

    print(f"\nQuestion : {args.question}")
    print(f"Role     : {args.role}")

    chunks = retrieve(args.question, role=args.role)
    _print_chunks(chunks)
    _stream_answer(args.question, chunks)


if __name__ == "__main__":
    main()

