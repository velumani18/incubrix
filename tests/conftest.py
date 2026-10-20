import pytest
from app.core.config import settings

@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch):
    """Ensure automated tests run with deterministic mock adapters by default."""
    monkeypatch.setattr(settings, "STT_PROVIDER", "mock")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
