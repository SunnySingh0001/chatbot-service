"""Global pytest configuration and automated test environment fixtures."""
from __future__ import annotations

import os

import pytest

# Ensure MOCK_TOKEN_DELAY_MS is zeroed out immediately for testing
os.environ["MOCK_TOKEN_DELAY_MS"] = "0"

from app.config import settings  # noqa: E402

settings.mock_token_delay_ms = 0


@pytest.fixture(autouse=True)
def _set_test_token_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disables simulated mock token delay so test suites execute at maximum speed."""
    monkeypatch.setattr(settings, "mock_token_delay_ms", 0)
