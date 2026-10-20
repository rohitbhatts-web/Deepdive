"""Shared OpenAI SDK client, pointed exclusively at Mesh API."""

from openai import OpenAI

from backend.config import get_settings


class MeshNotConfiguredError(RuntimeError):
    pass


def get_mesh_client() -> OpenAI:
    """Build an OpenAI client for Mesh API. Raises if MESH_API_KEY is unset."""
    settings = get_settings()
    if not settings.mesh_api_key:
        raise MeshNotConfiguredError("MESH_API_KEY is not set. Copy .env.example to .env and fill it in.")
    return OpenAI(api_key=settings.mesh_api_key, base_url=settings.mesh_base_url)


def mesh_configured() -> bool:
    return bool(get_settings().mesh_api_key)
