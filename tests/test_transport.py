import pytest

from media_mcp.__main__ import resolve_transport
from media_mcp.config import Settings


def test_transport_defaults_to_stdio(monkeypatch):
    """stdio must stay the default: local dev and Claude Desktop rely on it."""
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    assert Settings(_env_file=None).mcp_transport == "stdio"


def test_http_defaults_are_container_friendly(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080


def test_stdio_maps_to_itself():
    assert resolve_transport("stdio") == "stdio"


@pytest.mark.parametrize("value", ["http", "HTTP", "  http  ", "streamable-http"])
def test_http_aliases_map_to_streamable_http(value):
    assert resolve_transport(value) == "streamable-http"


def test_sse_is_passed_through():
    assert resolve_transport("sse") == "sse"


def test_unknown_transport_lists_accepted_values():
    with pytest.raises(ValueError, match="Unknown MCP_TRANSPORT") as exc:
        resolve_transport("grpc")
    assert "streamable-http" in str(exc.value)
