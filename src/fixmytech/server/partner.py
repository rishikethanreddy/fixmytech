"""Partner (FlipValue) service configuration managed from the Settings page.

Security model (see docs/FLIPVALUE_INTEGRATION.md):

* The API key is a secret. It is stored only on the server, in a file written with owner-only permissions where the
  operating system supports it. It is **not encrypted at rest** (no key management is available to a local app);
  production deployments should set FLIPVALUE_API_KEY in the environment or a secret manager instead.
* The key is never returned by the API (only its last four characters), never logged and never part of a report.
* Settings endpoints are admin-only: loopback clients in development, or a matching FIXMYTECH_ADMIN_TOKEN header
  (required in production).
* The base URL must be https (plain http only for loopback in development) and, in production, must not point at a
  private, loopback or link-local address (SSRF guard, including after DNS resolution).
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from fixmytech.integrations.flipvalue.client import FlipValueClient, FlipValueSettings
from fixmytech.server.errors import ApiError

LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class PartnerConfig(BaseModel):
    base_url: str = Field(max_length=300)
    api_key: str = Field(min_length=8, max_length=300)


class PartnerStore:
    """One JSON file with the saved partner configuration."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PartnerConfig | None:
        try:
            return PartnerConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, config: PartnerConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".partner-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(config.model_dump()))
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass  # Windows ACLs differ; the file lives in the user's own data folder
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def key_hint(key: str) -> str:
    return f"••••{key[-4:]}"


def _is_forbidden_address(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def validate_base_url(url: str, env: str, resolve: bool = True) -> str:
    """Return the normalised base URL or raise ApiError(422)."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    bad = lambda why: ApiError(422, "invalid_partner_url", why)  # noqa: E731
    if parts.scheme not in ("https", "http") or not host:
        raise bad("Enter a full address such as https://api.example.com")
    if parts.username or parts.password:
        raise bad("The address must not contain a user name or password.")
    if parts.query or parts.fragment:
        raise bad("The address must not contain a query string or fragment.")
    loopback = host in ("localhost", "127.0.0.1", "::1")
    if env == "production":
        if parts.scheme != "https":
            raise bad("The address must start with https:// in production.")
        if loopback or host.endswith((".local", ".internal", ".localhost")) or _is_forbidden_address(host):
            raise bad("The address must be a public host, not a local or private network address.")
        if resolve:
            try:
                infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
            except OSError as exc:
                raise bad("That host name could not be resolved.") from exc
            if any(_is_forbidden_address(str(i[4][0])) for i in infos):
                raise bad("That host name points to a private or local network address.")
    elif parts.scheme != "https" and not loopback:
        raise bad("The address must start with https:// (http:// is allowed only for localhost in development).")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def require_admin(client_host: str | None, presented: str | None, admin_token: str | None, env: str) -> None:
    """Admin gate for the settings endpoints."""
    if admin_token:
        if presented and secrets.compare_digest(presented, admin_token):
            return
        raise ApiError(401, "admin_token_required", "Enter the administrator token to change these settings.")
    if env == "production":
        raise ApiError(403, "admin_not_configured",
                       "Set FIXMYTECH_ADMIN_TOKEN on the server to manage the partner key from Settings.")
    if (client_host or "") not in LOOPBACK_CLIENTS:
        raise ApiError(403, "admin_local_only", "Partner settings can only be changed from the same computer.")


@dataclass
class ResolvedClient:
    client: FlipValueClient
    origin: str  # "settings" | "environment" | "development_mock"


def resolve_client(st: object) -> ResolvedClient | None:
    """The client valuations should use, or None when no real partner service is configured.

    Order: key saved in Settings, then the FLIPVALUE_* environment (live), then the development mock, which is only
    used when FIXMYTECH_ALLOW_MOCK_VALUATION is set explicitly and never in production.
    """
    settings = st.settings  # type: ignore[attr-defined]
    saved = st.partner_store.load() if st.partner_store else None  # type: ignore[attr-defined]
    if saved:
        cache = st.partner_clients  # type: ignore[attr-defined]
        fingerprint = (saved.base_url, saved.api_key)
        if fingerprint not in cache:
            cache.clear()
            cache[fingerprint] = FlipValueClient(FlipValueSettings(
                mode="live", base_url=saved.base_url, api_key=saved.api_key, timeout_s=settings.flipvalue_timeout_s,
                max_retries=settings.flipvalue_max_retries, api_version=settings.flipvalue_api_version))
        return ResolvedClient(cache[fingerprint], "settings")
    env_client = st.flipvalue  # type: ignore[attr-defined]
    mode = env_client.settings.mode
    if mode == "live" and env_client.settings.base_url and env_client.settings.api_key:
        return ResolvedClient(env_client, "environment")
    if mode == "mock" and settings.allow_mock_valuation and settings.env != "production":
        return ResolvedClient(env_client, "development_mock")
    return None


def public_status(st: object) -> dict[str, object]:
    """What the browser may know: whether a service is configured, where from, and the key's last four characters."""
    resolved = resolve_client(st)
    saved = st.partner_store.load() if st.partner_store else None  # type: ignore[attr-defined]
    return {
        "configured": resolved is not None and resolved.origin != "development_mock",
        "origin": resolved.origin if resolved else None,
        "development_mock": bool(resolved and resolved.origin == "development_mock"),
        "base_url": saved.base_url if saved else None,
        "key_hint": key_hint(saved.api_key) if saved else None,
        "admin_token_required": bool(st.settings.admin_token) or st.settings.env == "production",  # type: ignore[attr-defined]
    }
