from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
import json
import os
import re

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import safe_write_text
from lmit_wiki.policy import EXTERNAL_LLM_ALLOWED, filter_profiles_for_policy


SUPPORTED_PROVIDERS = {
    "openai_compatible",
    "gemini",
    "ollama",
}


class RuntimeSettingsError(ValueError):
    """Raised when runtime provider settings are invalid."""


class LLMInvocationError(RuntimeError):
    """Raised when every configured provider fails."""


@dataclass(frozen=True)
class LLMProfile:
    profile_id: str
    provider: str
    label: str
    base_url: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    enabled: bool = True
    temperature: float = 0.2
    timeout_seconds: int = 90


@dataclass(frozen=True)
class WikiRuntimeSettings:
    active_profile_id: str | None
    fallback_order: tuple[str, ...]
    profiles: tuple[LLMProfile, ...]

    def ordered_profiles(self) -> list[LLMProfile]:
        by_id = {profile.profile_id: profile for profile in self.profiles if profile.enabled}
        ordered: list[LLMProfile] = []
        seen: set[str] = set()

        if self.active_profile_id and self.active_profile_id in by_id:
            ordered.append(by_id[self.active_profile_id])
            seen.add(self.active_profile_id)

        for profile_id in self.fallback_order:
            if profile_id in by_id and profile_id not in seen:
                ordered.append(by_id[profile_id])
                seen.add(profile_id)

        for profile in self.profiles:
            if profile.enabled and profile.profile_id not in seen:
                ordered.append(profile)
                seen.add(profile.profile_id)
        return ordered


@dataclass(frozen=True)
class LLMCompletion:
    profile_id: str
    provider: str
    model: str
    content: str
    attempts: tuple[str, ...]


def default_runtime_settings_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "active_profile": "ollama-local",
        "fallback_order": [
            "ollama-local",
            "openai-compatible",
            "gemini",
        ],
        "profiles": [
            {
                "id": "ollama-local",
                "provider": "ollama",
                "label": "Local Ollama",
                "base_url": "http://localhost:11434/api",
                "model": "llama3.1",
                "api_key_env": "",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": 120,
            },
            {
                "id": "openai-compatible",
                "provider": "openai_compatible",
                "label": "OpenAI Compatible",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "api_key_env": "OPENAI_API_KEY",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": 120,
            },
            {
                "id": "gemini",
                "provider": "gemini",
                "label": "Gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "model": "gemini-2.5-flash",
                "api_key_env": "GEMINI_API_KEY",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": 120,
            },
        ],
    }


def ensure_runtime_settings_file(cfg: AppConfig) -> None:
    if cfg.wiki_runtime.settings_path.exists():
        return
    payload = default_runtime_settings_payload()
    safe_write_text(
        cfg.wiki_runtime.settings_path,
        cfg.wiki.root_dir,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def load_runtime_settings(cfg: AppConfig) -> WikiRuntimeSettings:
    ensure_runtime_settings_file(cfg)
    raw = json.loads(cfg.wiki_runtime.settings_path.read_text(encoding="utf-8"))
    return _settings_from_payload(raw)


def save_runtime_settings(
    cfg: AppConfig,
    payload: dict[str, Any],
) -> WikiRuntimeSettings:
    settings = _settings_from_payload(payload)
    serialized = {
        "version": 1,
        "active_profile": settings.active_profile_id,
        "fallback_order": list(settings.fallback_order),
        "profiles": [
            {
                "id": profile.profile_id,
                "provider": profile.provider,
                "label": profile.label,
                "base_url": profile.base_url,
                "model": profile.model,
                "api_key_env": profile.api_key_env,
                "enabled": profile.enabled,
                "temperature": profile.temperature,
                "timeout_seconds": profile.timeout_seconds,
            }
            for profile in settings.profiles
        ],
    }
    safe_write_text(
        cfg.wiki_runtime.settings_path,
        cfg.wiki.root_dir,
        json.dumps(serialized, ensure_ascii=False, indent=2),
    )
    return settings


def runtime_settings_public_payload(settings: WikiRuntimeSettings) -> dict[str, Any]:
    return {
        "active_profile": settings.active_profile_id,
        "fallback_order": list(settings.fallback_order),
        "profiles": [
            {
                "id": profile.profile_id,
                "provider": profile.provider,
                "label": profile.label,
                "base_url": profile.base_url,
                "model": profile.model,
                "api_key_env": profile.api_key_env,
                "enabled": profile.enabled,
                "temperature": profile.temperature,
                "timeout_seconds": profile.timeout_seconds,
                "api_key_present": bool(_resolved_api_key(profile)),
                "api_key_masked": _masked_secret(_resolved_api_key(profile)),
            }
            for profile in settings.profiles
        ],
    }


def merge_runtime_settings_payload(
    existing: WikiRuntimeSettings,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    existing_by_id = {profile.profile_id: profile for profile in existing.profiles}
    merged_profiles: list[dict[str, Any]] = []
    for raw in incoming.get("profiles", []):
        profile_id = str(raw.get("id", "")).strip()
        previous = existing_by_id.get(profile_id)
        api_key_env = str(raw.get("api_key_env", "") or "").strip()
        if not api_key_env and previous is not None:
            api_key_env = previous.api_key_env
        merged_profiles.append(
            {
                "id": profile_id,
                "provider": raw.get("provider"),
                "label": raw.get("label"),
                "base_url": raw.get("base_url"),
                "model": raw.get("model"),
                "api_key_env": api_key_env,
                "enabled": raw.get("enabled"),
                "temperature": raw.get("temperature"),
                "timeout_seconds": raw.get("timeout_seconds"),
            }
        )

    return {
        "version": 1,
        "active_profile": incoming.get("active_profile"),
        "fallback_order": incoming.get("fallback_order", []),
        "profiles": merged_profiles,
    }


def invoke_text_completion(
    cfg: AppConfig,
    messages: list[dict[str, str]],
    *,
    purpose: str,
    llm_policy: str = EXTERNAL_LLM_ALLOWED,
) -> LLMCompletion:
    settings = load_runtime_settings(cfg)
    attempts: list[str] = []
    providers = filter_profiles_for_policy(settings.ordered_profiles(), llm_policy)
    if not providers:
        raise LLMInvocationError(
            f"No LLM profiles are allowed for {llm_policy} policy."
        )

    for profile in providers:
        try:
            content = _invoke_profile(profile, messages)
            if not content.strip():
                raise RuntimeSettingsError("empty completion content")
            return LLMCompletion(
                profile_id=profile.profile_id,
                provider=profile.provider,
                model=profile.model,
                content=content.strip(),
                attempts=tuple(attempts),
            )
        except Exception as exc:
            attempts.append(f"{profile.profile_id}: {type(exc).__name__}: {exc}")

    details = "; ".join(attempts) or "no attempts recorded"
    raise LLMInvocationError(f"All providers failed for {purpose}: {details}")


def invoke_json_completion(
    cfg: AppConfig,
    messages: list[dict[str, str]],
    *,
    purpose: str,
    llm_policy: str = EXTERNAL_LLM_ALLOWED,
) -> tuple[dict[str, Any], LLMCompletion]:
    completion = invoke_text_completion(
        cfg,
        messages,
        purpose=purpose,
        llm_policy=llm_policy,
    )
    try:
        payload = parse_json_document(completion.content)
    except Exception as exc:
        raise LLMInvocationError(
            f"{completion.profile_id} returned invalid JSON for {purpose}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise LLMInvocationError(
            f"{completion.profile_id} returned non-object JSON for {purpose}."
        )
    return payload, completion


def parse_json_document(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(?P<body>\{.*\}|\[.*\])\s*```", stripped, re.DOTALL)
    if fenced:
        return json.loads(fenced.group("body"))

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(stripped[start : index + 1])
    raise json.JSONDecodeError("Unable to locate JSON document", stripped, 0)


def _settings_from_payload(payload: dict[str, Any]) -> WikiRuntimeSettings:
    raw_profiles = payload.get("profiles", [])
    if not isinstance(raw_profiles, list):
        raise RuntimeSettingsError("profiles must be a list")

    profiles: list[LLMProfile] = []
    seen_ids: set[str] = set()
    for item in raw_profiles:
        profile = _profile_from_payload(item)
        if profile.profile_id in seen_ids:
            raise RuntimeSettingsError(f"duplicate profile id: {profile.profile_id}")
        seen_ids.add(profile.profile_id)
        profiles.append(profile)

    fallback_order = tuple(str(item).strip() for item in payload.get("fallback_order", []))
    unknown_fallbacks = [item for item in fallback_order if item and item not in seen_ids]
    if unknown_fallbacks:
        raise RuntimeSettingsError(
            f"fallback profile ids not found: {', '.join(sorted(unknown_fallbacks))}"
        )

    active_profile_id = payload.get("active_profile")
    if active_profile_id is not None:
        active_profile_id = str(active_profile_id).strip() or None
        if active_profile_id is not None and active_profile_id not in seen_ids:
            raise RuntimeSettingsError(f"active profile not found: {active_profile_id}")

    return WikiRuntimeSettings(
        active_profile_id=active_profile_id,
        fallback_order=fallback_order,
        profiles=tuple(profiles),
    )


def _profile_from_payload(payload: dict[str, Any]) -> LLMProfile:
    profile_id = str(payload.get("id", "")).strip()
    if not profile_id:
        raise RuntimeSettingsError("profile id is required")

    provider = str(payload.get("provider", "")).strip()
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise RuntimeSettingsError(
            f"profile {profile_id} has unsupported provider {provider!r}; expected one of {supported}"
        )

    label = str(payload.get("label") or profile_id).strip()
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key_env = str(payload.get("api_key_env") or "").strip()
    legacy_api_key = str(payload.get("api_key") or "").strip()
    if not api_key_env:
        if provider == "openai_compatible":
            api_key_env = "OPENAI_API_KEY"
        elif provider == "gemini":
            api_key_env = "GEMINI_API_KEY"
    if not base_url:
        raise RuntimeSettingsError(f"profile {profile_id} is missing base_url")
    if not model:
        raise RuntimeSettingsError(f"profile {profile_id} is missing model")

    return LLMProfile(
        profile_id=profile_id,
        provider=provider,
        label=label,
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        api_key=legacy_api_key,
        enabled=bool(payload.get("enabled", True)),
        temperature=float(payload.get("temperature", 0.2)),
        timeout_seconds=max(1, int(payload.get("timeout_seconds", 90))),
    )


def _invoke_profile(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    if profile.provider == "openai_compatible":
        return _invoke_openai_compatible(profile, messages)
    if profile.provider == "gemini":
        return _invoke_gemini(profile, messages)
    if profile.provider == "ollama":
        return _invoke_ollama(profile, messages)
    raise RuntimeSettingsError(f"Unsupported provider: {profile.provider}")


def _invoke_openai_compatible(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    api_key = _resolved_api_key(profile)
    if not api_key:
        raise RuntimeSettingsError(f"profile {profile.profile_id} is missing an API key")
    data = _post_json(
        _openai_chat_url(profile.base_url),
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
        },
        timeout_seconds=profile.timeout_seconds,
    )
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeSettingsError("invalid OpenAI-compatible response") from exc
    return _flatten_message_content(content)


def _invoke_gemini(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    api_key = _resolved_api_key(profile)
    if not api_key:
        raise RuntimeSettingsError(f"profile {profile.profile_id} is missing an API key")

    system_parts = [message["content"] for message in messages if message["role"] == "system"]
    conversation = []
    for message in messages:
        if message["role"] == "system":
            continue
        conversation.append(
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
        )
    if not conversation:
        conversation = [{"role": "user", "parts": [{"text": ""}]}]

    body: dict[str, Any] = {
        "contents": conversation,
        "generationConfig": {
            "temperature": profile.temperature,
        },
    }
    if system_parts:
        body["system_instruction"] = {
            "parts": [{"text": "\n\n".join(system_parts)}],
        }

    data = _post_json(
        _gemini_generate_url(profile.base_url, profile.model),
        {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        body,
        timeout_seconds=profile.timeout_seconds,
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeSettingsError("invalid Gemini response") from exc
    return "\n".join(str(part.get("text", "")).strip() for part in parts).strip()


def _invoke_ollama(profile: LLMProfile, messages: list[dict[str, str]]) -> str:
    data = _post_json(
        _ollama_chat_url(profile.base_url),
        {
            "Content-Type": "application/json",
        },
        {
            "model": profile.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": profile.temperature,
            },
        },
        timeout_seconds=profile.timeout_seconds,
    )
    try:
        return str(data["message"]["content"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeSettingsError("invalid Ollama response") from exc


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset("utf-8")
        raw = response.read().decode(charset, errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(f"invalid JSON response from {url}") from exc


def _openai_chat_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _ollama_chat_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat") or normalized.endswith("/api/chat"):
        return normalized
    if normalized.endswith("/api"):
        return normalized + "/chat"
    return normalized + "/api/chat"


def _gemini_generate_url(base_url: str, model: str) -> str:
    normalized = base_url.rstrip("/")
    if ":generateContent" in normalized:
        return normalized
    model_name = model[7:] if model.startswith("models/") else model
    if normalized.endswith("/models"):
        return normalized + f"/{quote(model_name)}:generateContent"
    return normalized + f"/models/{quote(model_name)}:generateContent"


def _resolved_api_key(profile: LLMProfile) -> str:
    if profile.api_key_env:
        return os.environ.get(profile.api_key_env, "")
    if profile.api_key:
        return profile.api_key
    if profile.provider == "openai_compatible":
        return os.environ.get("OPENAI_API_KEY", "")
    if profile.provider == "gemini":
        return os.environ.get("GEMINI_API_KEY", "")
    return ""


def _flatten_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    return str(content)


def _masked_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]

