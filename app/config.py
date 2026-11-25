"""Loads all application settings from environment variables / .env file."""
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict



class Settings(BaseSettings):
    """Single source of truth for every config value — all from env, never hardcoded."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM provider ──────────────────────────────────────────────────────────
    llm_provider: str = "groq"             # groq | openai | gemini
    llm_model: str = "openai/gpt-oss-20b"
    llm_temperature: float = Field(
        default=0.5, validation_alias=AliasChoices("LLM_TEMPERATURE", "llm_temperature")
    )
    groq_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # ── RAG / Vector store ────────────────────────────────────────────────────
    rag_score_threshold: float = Field(
        default=0.78, validation_alias=AliasChoices("RAG_SCORE_THRESHOLD", "rag_score_threshold")
    )
    rag_score_threshold_hinglish: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAG_SCORE_THRESHOLD_HINGLISH", "rag_score_threshold_hinglish"
        ),
    )
    rag_score_threshold_hindi: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAG_SCORE_THRESHOLD_HINDI", "rag_score_threshold_hindi"
        ),
    )
    rag_top_k: int = Field(default=10, validation_alias=AliasChoices("RAG_TOP_K", "rag_top_k"))
    chroma_persist_dir: str = Field(
        default="./chroma_db", validation_alias=AliasChoices("CHROMA_PERSIST_DIR", "chroma_persist_dir")
    )
    embedding_model: str = Field(
        default="multilingual-e5-small",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "embedding_model", "embed_model"),
    )
    chroma_collection: str = Field(
        default="company_docs_multilingual_e5_small",
        validation_alias=AliasChoices("CHROMA_COLLECTION", "chroma_collection"),
    )
    mongodb_uri: str = Field(
        default="", validation_alias=AliasChoices("MONGODB_URI", "mongodb_uri")
    )
    mongodb_db_name: str = Field(
        default="fakhri2_0",
        validation_alias=AliasChoices("MONGODB_DB_NAME", "mongodb_db_name"),
    )

    @property
    def embed_model(self) -> str:
        """Alias returning embedding_model for backwards compatibility."""
        return self.embedding_model

    # ── Auth (used from Phase 3 onwards) ──────────────────────────────────────
    jwt_secret: str = "dev-insecure-secret-key-must-be-changed-in-production-32bytes"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── Security & Limits ─────────────────────────────────────────────────────
    rate_limit: str = "20/minute"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    max_message_length: int = 1000        # max characters per user question
    max_history_length: int = 10          # max conversation turns accepted
    max_output_tokens: int = 500          # max response tokens generated
    require_tenant_isolation: bool = Field(
        default=False,
        validation_alias=AliasChoices("REQUIRE_TENANT_ISOLATION", "require_tenant_isolation"),
    )
    session_ttl_hours: int = Field(
        default=24,
        validation_alias=AliasChoices("SESSION_TTL_HOURS", "session_ttl_hours"),
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    mock_token_delay_ms: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "MOCK_TOKEN_DELAY_MS", "mock_token_delay_ms"
        ),
    )
    orchestrator: str = Field(
        default="langgraph", validation_alias=AliasChoices("ORCHESTRATOR", "orchestrator")
    )

    # ── RAG Quality Upgrade Flags (default off) ──────────────────────────────
    chunking_mode: str = Field(
        default="section", validation_alias=AliasChoices("CHUNKING_MODE", "chunking_mode")
    )
    table_max_rows_per_chunk: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "TABLE_MAX_ROWS_PER_CHUNK", "table_max_rows_per_chunk"
        ),
    )
    hybrid_search: bool = Field(
        default=False, validation_alias=AliasChoices("HYBRID_SEARCH", "hybrid_search")
    )
    hybrid_candidates: int = Field(
        default=8,
        validation_alias=AliasChoices("HYBRID_CANDIDATES", "hybrid_candidates"),
    )
    reranker: bool = Field(
        default=False, validation_alias=AliasChoices("RERANKER", "reranker")
    )
    reranker_model: str = Field(
        default="b", validation_alias=AliasChoices("RERANKER_MODEL", "reranker_model")
    )
    rerank_candidates: int = Field(
        default=8,
        validation_alias=AliasChoices("RERANK_CANDIDATES", "rerank_candidates"),
    )
    rerank_top_k: int = Field(
        default=3,
        validation_alias=AliasChoices("RERANK_TOP_K", "rerank_top_k"),
    )
    rerank_min_score: float = Field(
        default=0.0,
        validation_alias=AliasChoices("RERANK_MIN_SCORE", "rerank_min_score"),
    )
    rerank_max_tokens: int = Field(
        default=512,
        validation_alias=AliasChoices("RERANK_MAX_TOKENS", "rerank_max_tokens"),
    )
    rerank_intra_op_threads: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "RERANK_INTRA_OP_THREADS", "rerank_intra_op_threads"
        ),
    )
    rerank_inter_op_threads: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "RERANK_INTER_OP_THREADS", "rerank_inter_op_threads"
        ),
    )
    query_rewrite: bool = Field(
        default=False, validation_alias=AliasChoices("QUERY_REWRITE", "query_rewrite")
    )
    rewrite_timeout_s: float = Field(
        default=3.0,
        validation_alias=AliasChoices("REWRITE_TIMEOUT_S", "rewrite_timeout_s"),
    )
    rewrite_model: str = Field(
        default="",
        validation_alias=AliasChoices("REWRITE_MODEL", "rewrite_model"),
    )
    rewrite_mode: str = Field(
        default="replace",
        validation_alias=AliasChoices("REWRITE_MODE", "rewrite_mode"),
    )
    rewrite_backend: str = Field(
        default="llm",
        validation_alias=AliasChoices("REWRITE_BACKEND", "rewrite_backend"),
    )
    heuristic_variant: str = Field(
        default="a",
        validation_alias=AliasChoices("HEURISTIC_VARIANT", "heuristic_variant"),
    )
    grader: bool = Field(
        default=False, validation_alias=AliasChoices("GRADER", "grader")
    )

    @field_validator("orchestrator")
    @classmethod
    def validate_orchestrator(cls, v: str) -> str:
        """Enforces that orchestrator is strictly 'langgraph'."""
        val = v.lower().strip()
        if val != "langgraph":
            raise ValueError(
                f"Invalid ORCHESTRATOR={v!r}. The classic linear flow has been removed; only 'langgraph' is supported."
            )
        return val

    @field_validator("chunking_mode")
    @classmethod
    def validate_chunking_mode(cls, v: str) -> str:
        """Enforces that chunking_mode is strictly 'section' or 'hierarchical'."""
        val = v.lower().strip()
        if val not in ("section", "hierarchical"):
            raise ValueError(
                f"Invalid CHUNKING_MODE={v!r}. Must be either 'section' or 'hierarchical'."
            )
        return val

    @field_validator("reranker_model")
    @classmethod
    def validate_reranker_model(cls, v: str) -> str:
        """Enforces that reranker_model is strictly 'b' or 'c'."""
        val = v.lower().strip()
        if val not in ("b", "c"):
            raise ValueError(
                f"Invalid RERANKER_MODEL={v!r}. Must be either 'b' or 'c'."
            )
        return val

    @field_validator("rewrite_mode")
    @classmethod
    def validate_rewrite_mode(cls, v: str) -> str:
        """Enforces that rewrite_mode is strictly 'replace' or 'both'."""
        val = v.lower().strip()
        if val not in ("replace", "both"):
            raise ValueError(
                f"Invalid REWRITE_MODE={v!r}. Must be either 'replace' or 'both'."
            )
        return val

    @field_validator("rewrite_backend")
    @classmethod
    def validate_rewrite_backend(cls, v: str) -> str:
        """Enforces that rewrite_backend is 'llm', 'heuristic', or 'llm_then_heuristic'."""
        val = v.lower().strip()
        if val not in ("llm", "heuristic", "llm_then_heuristic"):
            raise ValueError(
                f"Invalid REWRITE_BACKEND={v!r}. Must be 'llm', 'heuristic', or 'llm_then_heuristic'."
            )
        return val

    @field_validator("heuristic_variant")
    @classmethod
    def validate_heuristic_variant(cls, v: str) -> str:
        """Enforces that heuristic_variant is in ('a', 'b', 'c', 'd', 'e', 'f')."""
        val = v.lower().strip()
        if val not in ("a", "b", "c", "d", "e", "f"):
            raise ValueError(
                f"Invalid HEURISTIC_VARIANT={v!r}. Must be 'a', 'b', 'c', 'd', 'e', or 'f'."
            )
        return val


# Module-level singleton — import this everywhere instead of re-instantiating.
settings = Settings()

