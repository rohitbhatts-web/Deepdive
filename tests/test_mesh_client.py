import pytest

from backend import config, mesh_client


def test_client_uses_mesh_base_url(monkeypatch):
    monkeypatch.setenv("MESH_API_KEY", "test-key")
    config.get_settings.cache_clear()
    client = mesh_client.get_mesh_client()
    assert str(client.base_url).startswith("https://api.meshapi.ai/v1")
    config.get_settings.cache_clear()


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("MESH_API_KEY", "")
    config.get_settings.cache_clear()
    with pytest.raises(mesh_client.MeshNotConfiguredError):
        mesh_client.get_mesh_client()
    config.get_settings.cache_clear()
