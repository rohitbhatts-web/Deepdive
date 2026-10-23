"""Keep configured secrets out of anything we store or return (error strings, events, stop reasons)."""

from backend.config import Settings

REDACTED = "[redacted]"


def redact(text: str, settings: Settings) -> str:
    for secret in (settings.mesh_api_key, settings.tavily_api_key):
        if secret and len(secret) >= 6:
            text = text.replace(secret, REDACTED)
    return text
