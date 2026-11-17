"""Constructs and executes the linear LangGraph pipeline with step and timeout limits."""
from __future__ import annotations

import concurrent.futures
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.orchestration.nodes import (
    authorize_node,
    finalize_node,
    generate_node,
    no_context_node,
    retrieve_node,
    route_after_retrieve,
)
from app.orchestration.state import ChatState

DEFAULT_RECURSION_LIMIT: int = 10
DEFAULT_TIMEOUT_SECONDS: float = 30.0


def build_chat_graph() -> CompiledStateGraph:
    """Constructs and compiles the in-memory linear LangGraph pipeline."""
    workflow: StateGraph[ChatState] = StateGraph(ChatState)

    workflow.add_node("authorize", authorize_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("no_context", no_context_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("finalize", finalize_node)

    workflow.add_edge(START, "authorize")
    workflow.add_edge("authorize", "retrieve")
    workflow.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {
            "no_context": "no_context",
            "generate": "generate",
        },
    )
    workflow.add_edge("no_context", "finalize")
    workflow.add_edge("generate", "finalize")
    workflow.add_edge("finalize", END)

    # Compile strictly without checkpointer (in-memory execution only)
    return workflow.compile(checkpointer=None)


# Compile singleton graph instance
chat_graph: CompiledStateGraph = build_chat_graph()


def execute_graph(
    initial_state: ChatState,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    step_limit: int = DEFAULT_RECURSION_LIMIT,
) -> ChatState:
    """Executes the graph synchronously, enforcing step limit and overall timeout."""
    config: dict[str, Any] = {"recursion_limit": step_limit}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(chat_graph.invoke, initial_state, config)
        try:
            return future.result(timeout=timeout_seconds)  # type: ignore[return-value]
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"Graph execution exceeded timeout of {timeout_seconds} seconds") from exc
