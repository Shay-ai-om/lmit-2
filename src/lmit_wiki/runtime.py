from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
import json
import os
import re
import sys

from lmit_wiki.config import AppConfig
from lmit_wiki.path_safety import safe_write_text
from lmit_wiki.policy import EXTERNAL_LLM_ALLOWED, filter_profiles_for_policy


SUPPORTED_PROVIDERS = {
    "openai_compatible",
    "lmstudio_rest",
    "gemini",
    "ollama",
}


class RuntimeSettingsError(ValueError):
    """Raised when runtime provider settings are invalid."""


class LLMInvocationError(RuntimeError):
    """Raised when every configured provider fails."""


DEFAULT_REMOTE_TIMEOUT_SECONDS = 120
DEFAULT_LOCAL_TIMEOUT_SECONDS = 300
DEFAULT_LOCAL_PROFILE_IDS = {
    "ollama-local",
    "lm-studio-local",
    "lm-studio-rest",
    "litellm-local",
}


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
            "lm-studio-rest",
            "litellm-local",
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
                "timeout_seconds": DEFAULT_LOCAL_TIMEOUT_SECONDS,
            },
            {
                "id": "lm-studio-rest",
                "provider": "lmstudio_rest",
                "label": "LM Studio REST",
                "base_url": "http://localhost:1234/api/v1",
                "model": "local-model",
                "api_key_env": "",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": DEFAULT_LOCAL_TIMEOUT_SECONDS,
            },
            {
                "id": "litellm-local",
                "provider": "openai_compatible",
                "label": "LiteLLM",
                "base_url": "http://localhost:4000",
                "model": "gpt-5",
                "api_key_env": "LITELLM_API_KEY",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": DEFAULT_LOCAL_TIMEOUT_SECONDS,
            },
            {
                "id": "openai-compatible",
                "provider": "openai_compatible",
                "label": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "api_key_env": "OPENAI_API_KEY",
                "enabled": False,
                "temperature": 0.2,
                "timeout_seconds": DEFAULT_REMOTE_TIMEOUT_SECONDS,
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
                "timeout_seconds": DEFAULT_REMOTE_TIMEOUT_SECONDS,
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
    on_chunk: Callable[[str], None] | None = None,
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
            content = _invoke_profile(profile, messages, on_chunk=on_chunk)
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
    decoder = json.JSONDecoder()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload, _index = decoder.raw_decode(stripped)
            return payload
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(?P<body>[\s\S]*?)\s*```", stripped, re.DOTALL)
    if fenced:
        return parse_json_document(fenced.group("body"))

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start < 0:
            continue
        try:
            payload, _index = decoder.raw_decode(stripped[start:])
            return payload
        except json.JSONDecodeError:
            continue
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
    if not base_url:
        raise RuntimeSettingsError(f"profile {profile_id} is missing base_url")
    if not model:
        raise RuntimeSettingsError(f"profile {profile_id} is missing model")
    if not api_key_env:
        if provider == "openai_compatible" and not _is_local_base_url(base_url):
            api_key_env = "OPENAI_API_KEY"
        elif provider == "gemini":
            api_key_env = "GEMINI_API_KEY"

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
        timeout_seconds=_normalized_timeout_seconds(
            payload.get("timeout_seconds"),
            profile_id=profile_id,
            provider=provider,
            base_url=base_url,
        ),
    )


def _invoke_profile(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    if profile.provider == "openai_compatible":
        return _invoke_openai_compatible(profile, messages, on_chunk=on_chunk)
    if profile.provider == "lmstudio_rest":
        return _invoke_lmstudio_rest(profile, messages, on_chunk=on_chunk)
    if profile.provider == "gemini":
        return _invoke_gemini(profile, messages, on_chunk=on_chunk)
    if profile.provider == "ollama":
        return _invoke_ollama(profile, messages, on_chunk=on_chunk)
    raise RuntimeSettingsError(f"Unsupported provider: {profile.provider}")


def _invoke_openai_compatible(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    api_key = _resolved_api_key(profile)
    if profile.api_key_env and not api_key:
        raise RuntimeSettingsError(f"profile {profile.profile_id} is missing an API key")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        if on_chunk is None:
            data = _post_json(
                _openai_chat_url(profile.base_url),
                headers,
                {
                    "model": profile.model,
                    "messages": messages,
                    "temperature": profile.temperature,
                },
                timeout_seconds=profile.timeout_seconds,
            )
        else:
            return _stream_openai_compatible(profile, messages, headers, on_chunk=on_chunk)
    except RuntimeSettingsError as exc:
        if _looks_like_lmstudio_openai_url(profile.base_url):
            raise RuntimeSettingsError(
                f"{exc} If this is LM Studio, confirm the selected model id is chat-capable "
                "or switch this profile to LM Studio REST."
            ) from exc
        raise
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeSettingsError("invalid OpenAI-compatible response") from exc
    return _flatten_message_content(content)


def _invoke_lmstudio_rest(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    api_key = _resolved_api_key(profile)
    if profile.api_key_env and not api_key:
        raise RuntimeSettingsError(f"profile {profile.profile_id} is missing an API key")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    system_prompt, input_text = _lmstudio_rest_prompt(messages)
    if on_chunk is None:
        data = _post_json(
            _lmstudio_chat_url(profile.base_url),
            headers,
            {
                "model": profile.model,
                "input": input_text,
                "system_prompt": system_prompt,
                "temperature": profile.temperature,
                "store": False,
            },
            timeout_seconds=profile.timeout_seconds,
        )
    else:
        return _stream_lmstudio_rest(
            profile,
            system_prompt,
            input_text,
            headers,
            on_chunk=on_chunk,
        )

    return _lmstudio_content_from_result(data)


def _invoke_gemini(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
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
    content = "\n".join(str(part.get("text", "")).strip() for part in parts).strip()
    if on_chunk is not None and content:
        on_chunk(content)
    return content


def _invoke_ollama(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    if on_chunk is None:
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
    return _stream_ollama(profile, messages, on_chunk=on_chunk)


def _stream_openai_compatible(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    headers: dict[str, str],
    *,
    on_chunk: Callable[[str], None],
) -> str:
    content_parts: list[str] = []

    def handle_event(_event_name: str, raw_data: str) -> None:
        if raw_data == "[DONE]":
            return
        chunk = json.loads(raw_data)
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            fragment = _flatten_message_content(delta.get("content"))
            if fragment:
                content_parts.append(fragment)
                on_chunk(fragment)

    _post_json_sse(
        _openai_chat_url(profile.base_url),
        headers,
        {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
            "stream": True,
        },
        timeout_seconds=profile.timeout_seconds,
        on_event=handle_event,
    )
    return "".join(content_parts).strip()


def _stream_lmstudio_rest(
    profile: LLMProfile,
    system_prompt: str,
    input_text: str,
    headers: dict[str, str],
    *,
    on_chunk: Callable[[str], None],
) -> str:
    content_parts: list[str] = []
    stream_error: str | None = None
    final_result: dict[str, Any] | None = None

    def handle_event(event_name: str, raw_data: str) -> None:
        nonlocal stream_error, final_result
        payload = json.loads(raw_data)
        event_type = payload.get("type") or event_name
        if event_type == "message.delta":
            fragment = str(payload.get("content", ""))
            if fragment:
                content_parts.append(fragment)
                on_chunk(fragment)
            return
        if event_type == "error":
            error = payload.get("error") or {}
            stream_error = str(error.get("message") or "LM Studio REST streaming error")
            return
        if event_type == "chat.end":
            result = payload.get("result")
            if isinstance(result, dict):
                final_result = result

    _post_json_sse(
        _lmstudio_chat_url(profile.base_url),
        headers,
        {
            "model": profile.model,
            "input": input_text,
            "system_prompt": system_prompt,
            "temperature": profile.temperature,
            "store": False,
            "stream": True,
        },
        timeout_seconds=profile.timeout_seconds,
        on_event=handle_event,
    )
    if stream_error:
        raise RuntimeSettingsError(stream_error)
    content = "".join(content_parts).strip()
    if content:
        return content
    if final_result is None:
        raise RuntimeSettingsError("LM Studio REST returned no message content")
    return _lmstudio_content_from_result(final_result)


def _stream_ollama(
    profile: LLMProfile,
    messages: list[dict[str, str]],
    *,
    on_chunk: Callable[[str], None],
) -> str:
    content_parts: list[str] = []

    def handle_object(payload: dict[str, Any]) -> None:
        message = payload.get("message")
        if not isinstance(message, dict):
            return
        fragment = str(message.get("content") or "")
        if fragment:
            content_parts.append(fragment)
            on_chunk(fragment)

    _post_json_lines(
        _ollama_chat_url(profile.base_url),
        {"Content-Type": "application/json"},
        {
            "model": profile.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": profile.temperature,
            },
        },
        timeout_seconds=profile.timeout_seconds,
        on_object=handle_object,
    )
    return "".join(content_parts).strip()


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset("utf-8")
            raw = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeSettingsError(_http_error_message(url, exc)) from exc
    except URLError as exc:
        raise RuntimeSettingsError(_network_error_message(url, exc)) from exc
    except TimeoutError as exc:
        raise RuntimeSettingsError(_timeout_error_message(url, timeout_seconds)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(f"invalid JSON response from {url}") from exc


def _post_json_sse(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    on_event: Callable[[str, str], None],
) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={**headers, "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset("utf-8")
            event_name = ""
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode(charset, errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        on_event(event_name, "\n".join(data_lines))
                    event_name = ""
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                on_event(event_name, "\n".join(data_lines))
    except HTTPError as exc:
        raise RuntimeSettingsError(_http_error_message(url, exc)) from exc
    except URLError as exc:
        raise RuntimeSettingsError(_network_error_message(url, exc)) from exc
    except TimeoutError as exc:
        raise RuntimeSettingsError(_timeout_error_message(url, timeout_seconds)) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(f"invalid streaming JSON response from {url}") from exc


def _post_json_lines(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    on_object: Callable[[dict[str, Any]], None],
) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset("utf-8")
            for raw_line in response:
                line = raw_line.decode(charset, errors="replace").strip()
                if not line:
                    continue
                payload_obj = json.loads(line)
                if isinstance(payload_obj, dict) and payload_obj.get("error"):
                    raise RuntimeSettingsError(str(payload_obj["error"]))
                if isinstance(payload_obj, dict):
                    on_object(payload_obj)
    except HTTPError as exc:
        raise RuntimeSettingsError(_http_error_message(url, exc)) from exc
    except URLError as exc:
        raise RuntimeSettingsError(_network_error_message(url, exc)) from exc
    except TimeoutError as exc:
        raise RuntimeSettingsError(_timeout_error_message(url, timeout_seconds)) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(f"invalid streaming JSON response from {url}") from exc


def _get_json(
    url: str,
    headers: dict[str, str],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset("utf-8")
            raw = response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeSettingsError(_http_error_message(url, exc)) from exc
    except URLError as exc:
        raise RuntimeSettingsError(_network_error_message(url, exc)) from exc
    except TimeoutError as exc:
        raise RuntimeSettingsError(_timeout_error_message(url, timeout_seconds)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeSettingsError(f"invalid JSON response from {url}") from exc


def fetch_model_choices(
    provider: str,
    base_url: str,
    *,
    api_key_env: str = "",
    timeout_seconds: int = 8,
) -> list[dict[str, str]]:
    profile = LLMProfile(
        profile_id="model-discovery",
        provider=provider,
        label="Model Discovery",
        base_url=base_url,
        model="_",
        api_key_env=api_key_env,
        enabled=True,
    )
    if provider == "openai_compatible":
        payload = _get_json(_openai_models_url(base_url), _json_headers(profile), timeout_seconds=timeout_seconds)
        return [{"id": item, "label": item} for item in _openai_model_ids_from_payload(payload)]
    if provider == "lmstudio_rest":
        payload = _get_json(_lmstudio_models_url(base_url), _json_headers(profile), timeout_seconds=timeout_seconds)
        return _lmstudio_model_choices_from_payload(payload)
    raise RuntimeSettingsError(
        "Fetch Models currently supports OpenAI-compatible, LiteLLM, and LM Studio REST providers."
    )


def _openai_chat_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _openai_models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/models"):
        return normalized
    return normalized + "/models"


def _is_local_base_url(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname
    return hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _looks_like_lmstudio_openai_url(base_url: str) -> bool:
    normalized = base_url.rstrip("/").lower()
    return _is_local_base_url(normalized) and normalized.endswith("/v1")


def _ollama_chat_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat") or normalized.endswith("/api/chat"):
        return normalized
    if normalized.endswith("/api"):
        return normalized + "/chat"
    return normalized + "/api/chat"


def _lmstudio_chat_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat"):
        return normalized
    return normalized + "/chat"


def _lmstudio_models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/models"):
        return normalized
    return normalized + "/models"


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
        return _environment_secret(profile.api_key_env)
    if profile.api_key:
        return profile.api_key
    if profile.provider == "openai_compatible":
        return _environment_secret("OPENAI_API_KEY")
    if profile.provider == "gemini":
        return _environment_secret("GEMINI_API_KEY")
    return ""


def _json_headers(profile: LLMProfile) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = _resolved_api_key(profile)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _normalized_timeout_seconds(
    raw_value: object,
    *,
    profile_id: str,
    provider: str,
    base_url: str,
) -> int:
    default_timeout = _default_timeout_seconds(provider, base_url)
    if raw_value in (None, ""):
        return default_timeout

    timeout = max(1, int(raw_value))
    if (
        timeout == DEFAULT_REMOTE_TIMEOUT_SECONDS
        and profile_id in DEFAULT_LOCAL_PROFILE_IDS
        and not _provider_is_remote(provider, base_url)
    ):
        return DEFAULT_LOCAL_TIMEOUT_SECONDS
    return timeout


def _default_timeout_seconds(provider: str, base_url: str) -> int:
    if _provider_is_remote(provider, base_url):
        return DEFAULT_REMOTE_TIMEOUT_SECONDS
    return DEFAULT_LOCAL_TIMEOUT_SECONDS


def _provider_is_remote(provider: str, base_url: str) -> bool:
    if provider in {"ollama", "lmstudio_rest"}:
        return False
    if provider == "openai_compatible":
        return not _is_local_base_url(base_url)
    return provider == "gemini"


def _lmstudio_rest_prompt(messages: list[dict[str, str]]) -> tuple[str, str]:
    system_parts: list[str] = []
    dialogue_parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).strip() or "user"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        prefix = "Assistant" if role == "assistant" else "User"
        dialogue_parts.append(f"{prefix}:\n{content}")
    if not dialogue_parts:
        dialogue_parts.append("User:\n")
    return "\n\n".join(system_parts).strip(), "\n\n".join(dialogue_parts).strip()


def _lmstudio_content_from_result(result: dict[str, Any]) -> str:
    output = result.get("output")
    if not isinstance(output, list):
        raise RuntimeSettingsError("invalid LM Studio REST response")
    parts = [
        str(item.get("content", "")).strip()
        for item in output
        if isinstance(item, dict) and item.get("type") == "message"
    ]
    content = "\n\n".join(part for part in parts if part)
    if not content:
        raise RuntimeSettingsError("LM Studio REST returned no message content")
    return content


def _openai_model_ids_from_payload(payload: object) -> list[str]:
    if isinstance(payload, dict):
        raw_models = payload.get("data")
        if raw_models is None:
            raw_models = payload.get("models")
    else:
        raw_models = payload
    if not isinstance(raw_models, list):
        return []

    model_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        model_id = ""
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            for key in ("id", "model", "name", "path"):
                value = item.get(key)
                if value:
                    model_id = str(value)
                    break
        model_id = model_id.strip()
        if model_id and model_id not in seen:
            model_ids.append(model_id)
            seen.add(model_id)
    return model_ids


def _lmstudio_model_choices_from_payload(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []

    choices: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("key") or "").strip()
        if not model_id or model_id in seen:
            continue
        loaded_instances = item.get("loaded_instances")
        loaded_suffix = ""
        if isinstance(loaded_instances, list) and loaded_instances:
            loaded_suffix = " [loaded]"
        display_name = str(item.get("display_name") or model_id).strip()
        choices.append(
            {
                "id": model_id,
                "label": f"{display_name} -> {model_id}{loaded_suffix}",
            }
        )
        seen.add(model_id)
    return choices


def _http_error_message(url: str, exc: HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace").strip()
    detail = _error_detail_from_text(body) or exc.reason or "request failed"
    return f"{exc.code} from {url}: {detail}"


def _network_error_message(url: str, exc: URLError) -> str:
    return f"Could not connect to {url}: {exc.reason}"


def _timeout_error_message(url: str, timeout_seconds: int) -> str:
    hint = f"Request to {url} timed out after {timeout_seconds} seconds."
    if _looks_like_lmstudio_openai_url(url) or "/api/v1/" in url:
        return (
            f"{hint} Increase Timeout Seconds for this profile, or switch to "
            "LM Studio REST if the OpenAI-compatible path is slow on this model."
        )
    return f"{hint} Increase Timeout Seconds for this profile and try again."


def _error_detail_from_text(text: str) -> str:
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "error", "detail"):
                value = error.get(key)
                if value:
                    return str(value)
        if error:
            return str(error)
        for key in ("message", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
    return text[:300]


def _environment_secret(name: str) -> str:
    if not name:
        return ""
    value = os.environ.get(name)
    if value:
        return value
    return _dotenv_values().get(name, "")


def _dotenv_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in _dotenv_candidate_paths():
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            key, value = _parse_dotenv_line(line)
            if key and key not in values:
                values[key] = value
    return values


def _dotenv_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    configured = os.environ.get("LMIT_WIKI_DOTENV")
    if configured:
        paths.append(Path(configured).expanduser())
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / ".env")
    paths.append(Path.cwd() / ".env")

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def _parse_dotenv_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return "", ""
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    key, separator, raw_value = stripped.partition("=")
    if not separator:
        return "", ""
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return "", ""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


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

