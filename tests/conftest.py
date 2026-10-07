import pytest


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    # Тестовете никога не пишат в истинската памет в ~/.jarvis.
    monkeypatch.setenv("JARVIS_MEMORY_PATH", str(tmp_path / "memory.db"))
