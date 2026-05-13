from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse


RESTRICTED_DOMAINS = {
    "chatgpt.com",
    "facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "www.facebook.com",
}

PUBLIC_WEB = "public_web"
RESTRICTED_OR_LOGIN = "restricted_or_login"
LOCAL_PRIVATE = "local_private"
EXTERNAL_LLM_ALLOWED = "external_llm_allowed"
LOCAL_ONLY = "local_only"


def source_visibility(record: dict[str, Any]) -> str:
    urls = [str(url) for url in record.get("urls", []) if str(url).strip()]
    if not urls:
        return LOCAL_PRIVATE
    hosts: set[str] = set()
    has_unclassified_url = False
    for url in urls:
        host = _host(url)
        if host:
            hosts.add(host)
        else:
            has_unclassified_url = True
    if any(_restricted_host(host) for host in hosts):
        return RESTRICTED_OR_LOGIN
    if has_unclassified_url or not hosts:
        return LOCAL_PRIVATE
    return PUBLIC_WEB


def llm_policy_for_sources(records: list[dict[str, Any]]) -> str:
    return EXTERNAL_LLM_ALLOWED


def provider_is_external(profile: Any) -> bool:
    provider = str(getattr(profile, "provider", "")).strip()
    if provider in {"ollama", "lmstudio_rest"}:
        return False
    if provider == "gemini":
        return True
    if provider != "openai_compatible":
        return True

    host = _host(str(getattr(profile, "base_url", "")))
    if host is None:
        return True
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
    )


def filter_profiles_for_policy(profiles: list[Any], llm_policy: str) -> list[Any]:
    return profiles


def _host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError:
        return None
    if host:
        return host.lower()
    try:
        return (urlparse(f"//{url}").hostname or "").lower() or None
    except ValueError:
        return None


def _restricted_host(host: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in RESTRICTED_DOMAINS)

