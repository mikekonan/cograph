"""Regression tests for EmbeddingSettings pre-flight api_key validation (G3)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.config import EmbeddingSettings


def test_enabled_with_empty_api_key_raises():
    """enabled=True with empty api_key must fail at construction time."""
    with pytest.raises(ValidationError) as exc_info:
        EmbeddingSettings(enabled=True, api_key="")
    errors = exc_info.value.errors()
    messages = " ".join(e["msg"] for e in errors).lower()
    assert "api_key" in messages


def test_enabled_with_valid_api_key_succeeds():
    """enabled=True with a non-empty api_key constructs fine."""
    s = EmbeddingSettings(enabled=True, api_key="test-real-key")
    assert s.enabled is True
    assert s.api_key.get_secret_value() == "test-real-key"


def test_disabled_with_empty_api_key_succeeds():
    """enabled=False with empty api_key is valid (disabled path unaffected)."""
    s = EmbeddingSettings(enabled=False, api_key="")
    assert s.enabled is False


def test_default_construction_succeeds():
    """Default EmbeddingSettings (enabled=False) constructs without any api_key."""
    s = EmbeddingSettings()
    assert s.enabled is False


def test_non_1536_dimensions_raises():
    """dimensions != 1536 must fail at construction time (schema pin, Task 2)."""
    with pytest.raises(ValidationError) as exc_info:
        EmbeddingSettings(dimensions=768)
    messages = " ".join(e["msg"] for e in exc_info.value.errors()).lower()
    assert "1536" in messages


def test_1536_dimensions_succeeds():
    """Default dimensions=1536 must construct fine."""
    s = EmbeddingSettings(dimensions=1536)
    assert s.dimensions == 1536
