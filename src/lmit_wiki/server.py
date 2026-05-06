from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
import os
from pathlib import Path
from queue import Queue
from socketserver import ThreadingMixIn
from threading import RLock, Thread
from urllib.parse import parse_qs, quote
from uuid import uuid4
from wsgiref.simple_server import WSGIServer, make_server
import json

from lmit_wiki.builder import ingest_wiki, init_wiki, lint_wiki
from lmit_wiki.config import AppConfig, load_config, write_local_config
from lmit_wiki.auto import auto_sync_wiki, clear_sync_stop_request, request_sync_stop
from lmit_wiki.path_safety import ensure_within_root
from lmit_wiki.query import answer_wiki_query, stream_wiki_query_answer
from lmit_wiki.runtime import (
    default_runtime_settings_payload,
    fetch_model_choices,
    has_enabled_llm_profiles,
    load_runtime_settings,
    merge_runtime_settings_payload,
    runtime_settings_public_payload,
    save_runtime_settings,
)
from lmit_wiki.search import search_result_payload, search_wiki


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


@dataclass
class SyncJobState:
    job_id: str
    requested_limit: int | None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "Queued."
    total_sources: int | None = None
    processed_sources: int = 0
    current_source_title: str | None = None
    current_relative_path: str | None = None
    created_pages: int = 0
    updated_pages: int = 0
    pages: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    stop_requested: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "requested_limit": self.requested_limit,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "total_sources": self.total_sources,
            "processed_sources": self.processed_sources,
            "current_source_title": self.current_source_title,
            "current_relative_path": self.current_relative_path,
            "created_pages": self.created_pages,
            "updated_pages": self.updated_pages,
            "pages": self.pages,
            "error": self.error,
            "stop_requested": self.stop_requested,
        }


class SyncJobManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[str, SyncJobState] = {}
        self._latest_job_id: str | None = None
        self._active_job_id: str | None = None

    def start_job(
        self,
        cfg: AppConfig,
        *,
        limit: int | None = None,
        resume: bool = False,
    ) -> tuple[dict[str, object], bool]:
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status in {"queued", "running"}:
                    return active.payload(), False
            job = SyncJobState(
                job_id=uuid4().hex[:12],
                requested_limit=limit,
                message="Resuming sync job..." if resume else "Queued.",
            )
            self._jobs[job.job_id] = job
            self._latest_job_id = job.job_id
            self._active_job_id = job.job_id
        clear_sync_stop_request(cfg)
        Thread(target=self._run_job, args=(job.job_id, cfg, limit), daemon=True).start()
        return job.payload(), True

    def resume_job(self, cfg: AppConfig, *, limit: int | None = None) -> tuple[dict[str, object], bool]:
        with self._lock:
            if limit is None and self._latest_job_id is not None:
                latest = self._jobs.get(self._latest_job_id)
                if latest is not None:
                    limit = latest.requested_limit
        return self.start_job(cfg, limit=limit, resume=True)

    def get_job(self, job_id: str | None = None) -> dict[str, object] | None:
        with self._lock:
            target = job_id or self._latest_job_id
            if target is None:
                return None
            job = self._jobs.get(target)
            return job.payload() if job is not None else None

    def request_stop(self, cfg: AppConfig, job_id: str | None = None) -> tuple[dict[str, object] | None, bool]:
        with self._lock:
            target = job_id or self._active_job_id or self._latest_job_id
            if target is None:
                return None, False
            job = self._jobs.get(target)
            if job is None:
                return None, False
            if job.status not in {"queued", "running"}:
                return job.payload(), False
            job.stop_requested = True
            job.message = "Stop requested. Sync will stop after the current source finishes."
            payload = job.payload()
        request_sync_stop(cfg)
        return payload, True

    def _run_job(self, job_id: str, cfg: AppConfig, limit: int | None) -> None:
        self._update_job(
            job_id,
            status="running",
            started_at=_utc_now(),
            message="Preparing sync job...",
        )

        def report_progress(update: dict[str, object]) -> None:
            self._update_job(
                job_id,
                message=str(update.get("message") or "Sync running..."),
                total_sources=_int_or_none(update.get("total_sources")),
                processed_sources=_int_or_zero(update.get("processed_sources")),
                current_source_title=_str_or_none(update.get("current_source_title")),
                current_relative_path=_str_or_none(update.get("current_relative_path")),
                created_pages=_int_or_zero(update.get("created_pages")),
                updated_pages=_int_or_zero(update.get("updated_pages")),
            )

        try:
            result = auto_sync_wiki(
                cfg,
                limit=limit,
                progress=report_progress,
                should_stop=lambda: self._job_should_stop(job_id),
            )
            final_status = "stopped" if result.status == "stopped" else "completed"
            final_message = (
                f"Sync stopped after processing {result.processed_sources} source(s)."
                if result.status == "stopped"
                else (
                    "Sync finished with no wiki page changes."
                    if not result.pages
                    else f"Sync finished. Created {result.created_pages} page(s) and updated {result.updated_pages} page(s)."
                )
            )
            self._update_job(
                job_id,
                status=final_status,
                finished_at=_utc_now(),
                processed_sources=result.processed_sources,
                created_pages=result.created_pages,
                updated_pages=result.updated_pages,
                message=final_message,
                stop_requested=False,
                pages=[
                    {
                        "name": page.name,
                        "kind": page.kind,
                        "path": str(page.path),
                        "action": page.action,
                    }
                    for page in result.pages
                ],
            )
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                finished_at=_utc_now(),
                message="Sync failed.",
                error=str(exc),
                stop_requested=False,
            )
        finally:
            clear_sync_stop_request(cfg)
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def _update_job(self, job_id: str, **changes: object) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                if value is not None or key in {"current_source_title", "current_relative_path", "error", "stop_requested"}:
                    setattr(job, key, value)

    def _job_should_stop(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.stop_requested)


def serve_wiki_ui(
    cfg: AppConfig,
    *,
    config_path: Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    host = host or cfg.wiki_runtime.serve_host
    port = port or cfg.wiki_runtime.serve_port
    app = WikiWebApp(cfg, config_path=config_path)
    pid_path = server_pid_path(cfg, config_path=config_path)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    with make_server(host, port, app, server_class=ThreadingWSGIServer) as server:
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        try:
            print(f"Wiki UI: http://{host}:{port}")
            server.serve_forever()
        finally:
            if pid_path.exists():
                pid_path.unlink()


def stop_wiki_ui(
    cfg: AppConfig | None = None,
    *,
    config_path: Path | None = None,
    pid: int | None = None,
) -> tuple[bool, str]:
    pid_path = server_pid_path(cfg, config_path=config_path)
    target_pid = pid
    if target_pid is None:
        if not pid_path.exists():
            return False, f"Wiki UI pid file not found: {pid_path}"
        raw_pid = pid_path.read_text(encoding="utf-8").strip()
        if not raw_pid:
            pid_path.unlink(missing_ok=True)
            return False, f"Wiki UI pid file was empty and has been removed: {pid_path}"
        try:
            target_pid = int(raw_pid)
        except ValueError:
            pid_path.unlink(missing_ok=True)
            return False, f"Wiki UI pid file was invalid and has been removed: {pid_path}"

    try:
        os.kill(target_pid, 15)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return False, f"Wiki UI process {target_pid} was not running. Removed stale pid file."
    except PermissionError as exc:
        return False, f"Could not stop Wiki UI process {target_pid}: {exc}"

    pid_path.unlink(missing_ok=True)
    return True, f"Sent stop signal to Wiki UI process {target_pid}."


class WikiWebApp:
    def __init__(self, cfg: AppConfig, *, config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path.resolve() if config_path is not None else None
        self._cfg_lock = RLock()
        self._sync_jobs = SyncJobManager()

    def __call__(self, environ, start_response):
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/":
                return self._html(start_response, INDEX_HTML)
            if method == "GET" and path == "/manual":
                return self._html(start_response, MANUAL_HTML)
            if method == "GET" and path == "/document":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                rel_path = query.get("path", [""])[0].strip()
                if not rel_path:
                    raise ValueError("Document path is required.")
                file_path = _resolve_document_path(self._current_cfg(), rel_path)
                return self._text(
                    start_response,
                    file_path.read_text(encoding="utf-8", errors="ignore"),
                    content_type="text/markdown; charset=utf-8",
                )
            if method == "GET" and path == "/api/status":
                return self._json(start_response, self._status_payload())
            if method == "POST" and path == "/api/config":
                payload = self._read_json(environ)
                if self.config_path is None:
                    raise ValueError("This server was started without --config, so paths cannot be saved.")
                root_text = str(payload.get("root_dir", "")).strip()
                source_dirs = _payload_source_dirs(payload.get("source_dirs"))
                if not root_text:
                    raise ValueError("Knowledge base path is required.")
                if not source_dirs:
                    raise ValueError("At least one raw source path is required.")
                old_cfg = self._current_cfg()
                new_cfg = write_local_config(
                    self.config_path,
                    root_dir=Path(root_text),
                    source_dirs=source_dirs,
                    serve_host=old_cfg.wiki_runtime.serve_host,
                    serve_port=old_cfg.wiki_runtime.serve_port,
                    auto_sync_on_ingest=old_cfg.wiki_runtime.auto_sync_on_ingest,
                    search_limit=old_cfg.wiki_runtime.search_limit,
                    task_schedule=old_cfg.windows.task_schedule,
                )
                init_wiki(new_cfg)
                with self._cfg_lock:
                    self.cfg = load_config(self.config_path)
                return self._json(start_response, self._status_payload())
            if method == "POST" and path == "/api/ingest":
                payload = self._read_json(environ)
                cfg = self._current_cfg()
                confirm_fallback = bool(payload.get("confirm_fallback"))
                ingest_mode = "standard"
                if not has_enabled_llm_profiles(cfg):
                    ingest_mode = "fallback"
                    if not confirm_fallback:
                        return self._json(
                            start_response,
                            {
                                "requires_confirmation": True,
                                "ingest_mode": ingest_mode,
                                "warning": (
                                    "No enabled LLM profile is configured yet. "
                                    "Configure an LLM first for a curated wiki, or continue with fallback ingest."
                                ),
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                result = ingest_wiki(cfg, ingest_mode=ingest_mode)
                return self._json(
                    start_response,
                    {
                        "source_count": result.source_count,
                        "copied_raw_count": result.copied_raw_count,
                        "source_note_count": result.source_note_count,
                        "index_path": str(result.index_path),
                        "source_catalog_path": str(result.source_catalog_path),
                        "log_path": str(result.log_path),
                        "ingest_mode": result.ingest_mode,
                    },
                )
            if method == "POST" and path == "/api/lint":
                warnings = lint_wiki(self._current_cfg())
                return self._json(
                    start_response,
                    {
                        "passed": not warnings,
                        "warnings": warnings,
                    },
                )
            if method == "GET" and path == "/api/sync":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                job = self._sync_jobs.get_job(query.get("job_id", [""])[0].strip() or None)
                return self._json(start_response, {"job": job})
            if method == "GET" and path == "/api/search":
                query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0]
                results = [
                    _search_result_payload(self._current_cfg(), item)
                    for item in search_wiki(self._current_cfg(), query, include_raw=True)
                ]
                return self._json(start_response, {"results": results})
            if method == "POST" and path == "/api/query/stream":
                payload = self._read_json(environ)
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Question is required.")
                return self._stream_query(
                    start_response,
                    question=question,
                    save=bool(payload.get("save", True)),
                )
            if method == "POST" and path == "/api/query":
                payload = self._read_json(environ)
                answer = answer_wiki_query(
                    self._current_cfg(),
                    str(payload.get("question", "")).strip(),
                    save=bool(payload.get("save", True)),
                )
                return self._json(
                    start_response,
                    {
                        "title": answer.title,
                        "answer_markdown": answer.answer_markdown,
                        "follow_up_questions": list(answer.follow_up_questions),
                        "saved_path": str(answer.saved_path) if answer.saved_path else None,
                        "search_results": [
                            _search_result_payload(self._current_cfg(), item)
                            for item in answer.search_results
                        ],
                        "llm": (
                            {
                                "profile_id": answer.completion.profile_id,
                                "provider": answer.completion.provider,
                                "model": answer.completion.model,
                            }
                            if answer.completion is not None
                            else None
                        ),
                    },
                )
            if method == "POST" and path == "/api/sync":
                payload = self._read_json(environ)
                raw_limit = payload.get("limit")
                limit = int(raw_limit) if raw_limit not in (None, "") else None
                job, started = self._sync_jobs.start_job(self._current_cfg(), limit=limit)
                return self._json(
                    start_response,
                    {
                        "started": started,
                        "job": job,
                    },
                    status=HTTPStatus.ACCEPTED if started else HTTPStatus.OK,
                )
            if method == "POST" and path == "/api/sync/stop":
                payload = self._read_json(environ)
                job_id = str(payload.get("job_id", "")).strip() or None
                job, stopped = self._sync_jobs.request_stop(self._current_cfg(), job_id=job_id)
                if job is None:
                    raise ValueError("No sync job is available to stop.")
                return self._json(
                    start_response,
                    {
                        "stopped": stopped,
                        "job": job,
                    },
                    status=HTTPStatus.OK if stopped else HTTPStatus.CONFLICT,
                )
            if method == "POST" and path == "/api/sync/resume":
                payload = self._read_json(environ)
                raw_limit = payload.get("limit")
                limit = int(raw_limit) if raw_limit not in (None, "") else None
                job, started = self._sync_jobs.resume_job(self._current_cfg(), limit=limit)
                return self._json(
                    start_response,
                    {
                        "started": started,
                        "job": job,
                    },
                    status=HTTPStatus.ACCEPTED if started else HTTPStatus.OK,
                )
            if method == "GET" and path == "/api/settings":
                settings = load_runtime_settings(self._current_cfg())
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            if method == "GET" and path == "/api/models":
                query = parse_qs(environ.get("QUERY_STRING", ""))
                provider = query.get("provider", [""])[0].strip()
                base_url = query.get("base_url", [""])[0].strip()
                api_key_env = query.get("api_key_env", [""])[0].strip()
                if not provider:
                    raise ValueError("Provider is required before fetching models.")
                if not base_url:
                    raise ValueError("Base URL is required before fetching models.")
                return self._json(
                    start_response,
                    {
                        "models": fetch_model_choices(
                            provider,
                            base_url,
                            api_key_env=api_key_env,
                        )
                    },
                )
            if method == "POST" and path == "/api/settings/defaults":
                settings = save_runtime_settings(
                    self._current_cfg(),
                    default_runtime_settings_payload(),
                )
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            if method == "POST" and path == "/api/settings":
                payload = self._read_json(environ)
                cfg = self._current_cfg()
                existing = load_runtime_settings(cfg)
                merged = merge_runtime_settings_payload(existing, payload)
                settings = save_runtime_settings(cfg, merged)
                return self._json(
                    start_response,
                    runtime_settings_public_payload(settings),
                )
            return self._json(
                start_response,
                {"error": f"Unknown route: {method} {path}"},
                status=HTTPStatus.NOT_FOUND,
            )
        except Exception as exc:
            return self._json(
                start_response,
                {"error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )

    def _read_json(self, environ) -> dict[str, object]:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        body = environ["wsgi.input"].read(length) if length > 0 else b"{}"
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _html(self, start_response, content: str):
        encoded = content.encode("utf-8")
        start_response(
            f"{HTTPStatus.OK.value} {HTTPStatus.OK.phrase}",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(encoded))),
            ],
        )
        return [encoded]

    def _json(self, start_response, payload: dict[str, object], *, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _text(
        self,
        start_response,
        content: str,
        *,
        status=HTTPStatus.OK,
        content_type: str = "text/plain; charset=utf-8",
    ):
        encoded = content.encode("utf-8")
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(encoded))),
            ],
        )
        return [encoded]

    def _stream_query(
        self,
        start_response,
        *,
        question: str,
        save: bool,
    ):
        queue: Queue[dict[str, object] | None] = Queue()
        cfg = self._current_cfg()

        def emit(payload: dict[str, object]) -> None:
            queue.put(payload)

        def worker() -> None:
            try:
                emit({"type": "start", "message": "Searching the wiki and starting the model..."})
                answer = stream_wiki_query_answer(
                    cfg,
                    question,
                    save=save,
                    on_chunk=lambda chunk: emit({"type": "chunk", "text": chunk}),
                )
                emit(
                    {
                        "type": "done",
                        "title": answer.title,
                        "answer_markdown": answer.answer_markdown,
                        "follow_up_questions": list(answer.follow_up_questions),
                        "saved_path": str(answer.saved_path) if answer.saved_path else None,
                        "search_results": [
                            _search_result_payload(cfg, item)
                            for item in answer.search_results
                        ],
                        "llm": (
                            {
                                "profile_id": answer.completion.profile_id,
                                "provider": answer.completion.provider,
                                "model": answer.completion.model,
                            }
                            if answer.completion is not None
                            else None
                        ),
                    }
                )
            except Exception as exc:
                emit({"type": "error", "error": str(exc)})
            finally:
                queue.put(None)

        Thread(target=worker, daemon=True).start()

        start_response(
            f"{HTTPStatus.OK.value} {HTTPStatus.OK.phrase}",
            [
                ("Content-Type", "application/x-ndjson; charset=utf-8"),
                ("Cache-Control", "no-cache"),
            ],
        )

        def stream():
            while True:
                item = queue.get()
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")

        return stream()

    def _status_payload(self) -> dict[str, object]:
        cfg = self._current_cfg()
        source_dirs = [
            {
                "path": str(path),
                "exists": None,
            }
            for path in cfg.wiki_ingest.source_dirs
        ]
        return {
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "root_dir": str(cfg.wiki.root_dir),
            "root_exists": None,
            "source_dirs": [item["path"] for item in source_dirs],
            "source_dir_status": source_dirs,
            "index_path": str(cfg.wiki.index_path),
            "index_exists": None,
            "log_path": str(cfg.wiki.log_path),
            "serve_host": cfg.wiki_runtime.serve_host,
            "serve_port": cfg.wiki_runtime.serve_port,
        }

    def _current_cfg(self) -> AppConfig:
        with self._cfg_lock:
            return self.cfg


def _payload_source_dirs(value: object) -> list[Path]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = str(value or "").replace(";", "\n").splitlines()
        items = [item.strip() for item in items]
    return [Path(item) for item in items if item]


def _search_result_payload(cfg: AppConfig, result) -> dict[str, object]:
    payload = search_result_payload(result)
    payload["document_url"] = _document_url(result.rel_path)
    raw_rel_path = _raw_rel_path_for_result(cfg, result)
    payload["raw_rel_path"] = raw_rel_path
    payload["raw_url"] = _document_url(raw_rel_path) if raw_rel_path else None
    return payload


def _raw_rel_path_for_result(cfg: AppConfig, result) -> str | None:
    if result.kind == "raw":
        return result.rel_path

    manifest_path = cfg.wiki.root_dir / "manifest.json"
    if not manifest_path.exists():
        return None

    result_path = result.path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest.get("sources", []):
        raw_path = Path(str(record.get("raw_path", "")))
        source_note_path = Path(str(record.get("source_note_path", "")))
        if result_path == source_note_path.resolve():
            stored = str(record.get("stored_raw_path") or "").strip()
            return stored or raw_path.relative_to(cfg.wiki.root_dir).as_posix()
    return None


def _document_url(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    return f"/document?path={quote(rel_path)}"


def _resolve_document_path(cfg: AppConfig, rel_path: str) -> Path:
    target = ensure_within_root(cfg.wiki.root_dir / Path(rel_path), cfg.wiki.root_dir)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Document not found: {rel_path}")
    return target


def server_pid_path(cfg: AppConfig | None = None, *, config_path: Path | None = None) -> Path:
    if config_path is not None:
        config_dir = config_path.resolve().parent
        return config_dir / "logs" / "wiki-server.pid"
    if cfg is None:
        raise ValueError("cfg or config_path is required to locate the wiki server pid file")
    return cfg.wiki.root_dir / ".wiki_server.pid"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _int_or_zero(value: object) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def _str_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LMIT-2 Wiki Console</title>
  <style>
    :root {
      --bg: #f6f1e8;
      --panel: #fffaf2;
      --ink: #1e2430;
      --muted: #697281;
      --line: #d8cbb8;
      --accent: #136f63;
      --accent-soft: #d9efe6;
      --warm: #c05d33;
      --shadow: 0 18px 50px rgba(30, 36, 48, 0.08);
      --radius: 8px;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(19, 111, 99, 0.14), transparent 28%),
        radial-gradient(circle at bottom right, rgba(192, 93, 51, 0.12), transparent 22%),
        var(--bg);
    }

    .shell {
      max-width: 1440px;
      margin: 0 auto;
      padding: 20px;
    }

    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: linear-gradient(135deg, rgba(255,250,242,0.96), rgba(236,248,243,0.96));
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }

    .hero h1 {
      margin: 0 0 4px;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .hero p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      word-break: break-all;
    }

    .grid {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 24px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 20px;
    }

    h2 {
      margin: 0 0 14px;
      font-size: 20px;
      letter-spacing: -0.02em;
    }

    .stack { display: grid; gap: 14px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
    input:not([type="checkbox"]), textarea, select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      color: var(--ink);
      font: inherit;
    }

    select {
      min-height: 46px;
    }

    textarea { min-height: 120px; resize: vertical; }

    button {
      border: 0;
      border-radius: var(--radius);
      padding: 11px 18px;
      font: inherit;
      cursor: pointer;
      background: var(--accent);
      color: white;
    }

    button.secondary {
      background: var(--accent-soft);
      color: var(--accent);
    }

    button.warm {
      background: var(--warm);
    }

    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--radius);
      padding: 11px 18px;
      color: white;
      background: var(--accent);
      text-decoration: none;
      white-space: nowrap;
    }

    .button-link.secondary {
      color: var(--accent);
      background: var(--accent-soft);
    }

    .result, .profile {
      padding: 14px;
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
    }

    .result h3, .profile h3 {
      margin: 0 0 6px;
      font-size: 16px;
    }

    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }

    .mono {
      font-family: Consolas, "Courier New", monospace;
      white-space: pre-wrap;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      line-height: 1.6;
      overflow-x: auto;
    }

    .row {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .tiny {
      color: var(--muted);
      font-size: 12px;
    }

    .path-list {
      display: grid;
      gap: 8px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      color: var(--muted);
      word-break: break-all;
    }

    .path-input {
      min-height: 74px;
    }

    .field {
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    .field span {
      color: var(--muted);
      font-size: 12px;
    }

    .profile-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .profile-header h3 {
      margin: 0;
    }

    .profile-header button {
      padding: 8px 12px;
    }

    .empty-state {
      color: var(--muted);
      font-size: 13px;
      padding: 12px 0;
    }

    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }

    .manual-panel {
      display: none;
      margin-bottom: 16px;
    }

    .manual-panel.open {
      display: block;
    }

    .manual-panel ol, .manual-panel ul {
      margin: 0 0 0 20px;
      padding: 0;
    }

    .manual-panel li {
      margin: 6px 0;
    }

    .manual-panel code {
      background: #eef2f1;
      border-radius: 4px;
      padding: 2px 5px;
    }

    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .hero { align-items: stretch; flex-direction: column; }
      .button-link { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <h1>LMIT-2 Wiki Console</h1>
        <p>Config: <span id="configPath">loading...</span></p>
      </div>
      <button class="secondary" onclick="toggleManual()">Web UI Guide</button>
    </section>

    <section id="manualPanel" class="panel manual-panel">
      <h2>Web UI Guide</h2>
      <div class="row">
        <div>
          <h3>First Run</h3>
          <ol>
            <li>If no config exists yet, the launcher asks for the knowledge base and raw Markdown folders.</li>
            <li>Inside the Web UI, you can still edit <code>Knowledge Base Path</code> and <code>Raw Source Paths</code>.</li>
            <li>Click <code>Save Paths</code>, then run <code>Ingest</code>.</li>
            <li>For LM Studio REST or LiteLLM, start the local server or proxy first, then use <code>Fetch Models</code> to fill the model id.</li>
            <li><code>Ask The Wiki</code> now streams live output, so once tokens start arriving the answer will keep filling in without waiting for one giant final response.</li>
          </ol>
        </div>
        <div>
          <h3>Troubleshooting</h3>
          <ul>
            <li><code>Save Paths</code> does not run Ingest or use any LLM.</li>
            <li><code>Sync Now</code> runs in the background. Keep this page open to watch progress.</li>
            <li>If a button stalls, check the message shown under that button.</li>
            <li>If <code>Ask The Wiki</code> produces no live text at all, the current model may still be loading or the provider may not support streaming for that endpoint.</li>
            <li>Launcher logs are under <code>%APPDATA%\\LMIT-2\\logs</code>.</li>
          </ul>
        </div>
      </div>
    </section>

    <div class="grid">
      <section class="panel stack">
        <div>
          <h2>Search</h2>
          <div class="toolbar">
            <input id="searchInput" placeholder="Search the wiki, source notes, and raw markdown">
            <button onclick="runSearch()">Search</button>
          </div>
        </div>
        <div id="searchStatus" class="status"></div>
        <div id="searchResults" class="stack"></div>

        <div>
          <h2>Ask The Wiki</h2>
          <div class="stack">
            <textarea id="questionInput" placeholder="Ask a synthesis question. The answer will be grounded in the current wiki and can be filed back into wiki/queries."></textarea>
            <div class="toolbar">
              <button class="warm" onclick="runQuery(true)">Ask And Save</button>
              <button class="secondary" onclick="runQuery(false)">Ask Only</button>
            </div>
          </div>
        </div>
        <div id="queryStatus" class="status"></div>
        <div id="queryOutput" class="stack"></div>

        <div>
          <h2>Knowledge Base</h2>
          <div class="row">
            <label class="field">
              <span>Knowledge Base Path</span>
              <textarea id="kbRootInput" class="path-input" placeholder="C:\\Users\\You\\Documents\\LMIT-2\\knowledge_base"></textarea>
            </label>
            <label class="field">
              <span>Raw Source Paths</span>
              <textarea id="sourceDirsInput" class="path-input" placeholder="C:\\Users\\You\\Documents\\LMIT\\output\\raw"></textarea>
            </label>
          </div>
          <div id="kbStatusPanel" class="path-list"></div>
          <div class="tiny">If no LLM profile is enabled, Ingest will ask whether you want to configure an LLM first or continue with fallback ingest. Imported sources will still be tracked in the Source Catalog.</div>
        </div>
        <div class="toolbar">
          <button onclick="savePaths()">Save Paths</button>
          <button onclick="runIngest()">Ingest</button>
          <button class="secondary" onclick="runLint()">Lint</button>
        </div>
        <div id="pathStatus" class="status"></div>
        <div id="ingestStatus" class="status"></div>
        <div id="ingestOutput" class="stack"></div>
        <div id="lintStatus" class="status"></div>
        <div id="lintOutput" class="stack"></div>

        <div>
          <h2>Auto Sync</h2>
          <div class="toolbar">
            <input id="syncLimit" placeholder="Optional source limit">
            <button id="syncButton" onclick="runSync()">Sync Now</button>
            <button id="syncStopButton" class="secondary" onclick="stopSync()">Stop Sync</button>
            <button id="syncResumeButton" class="secondary" onclick="resumeSync()">Resume Sync</button>
          </div>
        </div>
        <div id="syncStatus" class="status"></div>
        <div id="syncOutput" class="stack"></div>
      </section>

      <section class="panel stack">

        <div>
          <h2>LLM Settings</h2>
        </div>
        <div class="row">
          <label class="field">
            <span>Active Profile</span>
            <input id="activeProfile" placeholder="ollama-local">
          </label>
          <label class="field">
            <span>Fallback Order</span>
            <input id="fallbackOrder" placeholder="ollama-local, lm-studio-rest, litellm-local, openai-compatible, gemini">
          </label>
        </div>
        <div class="toolbar">
          <button class="secondary" onclick="addProfile('ollama')">Add Ollama</button>
          <button class="secondary" onclick="addProfile('lmstudioRest')">Add LM Studio REST</button>
          <button class="secondary" onclick="addProfile('litellm')">Add LiteLLM</button>
          <button class="secondary" onclick="addProfile('openai')">Add OpenAI</button>
          <button class="secondary" onclick="addProfile('gemini')">Add Gemini</button>
          <button class="secondary" onclick="restoreDefaults()">Restore Defaults</button>
          <button onclick="saveSettings()">Save Settings</button>
        </div>
        <div id="profiles" class="stack"></div>
        <div id="settingsStatus" class="status"></div>
      </section>
    </div>
  </div>

  <template id="profileTemplate">
    <div class="profile stack">
      <div class="profile-header">
        <h3 data-field="heading">Profile</h3>
        <button class="secondary" onclick="removeProfile(this)">Remove</button>
      </div>
      <div class="row">
        <label class="field">
          <span>Profile ID</span>
          <input data-field="id" placeholder="ollama-local">
        </label>
        <label class="field">
          <span>Label</span>
          <input data-field="label" placeholder="Local Ollama">
        </label>
      </div>
      <div class="row">
        <label class="field">
          <span>Provider</span>
          <select data-field="provider">
            <option value="ollama">ollama</option>
            <option value="openai_compatible">openai_compatible</option>
            <option value="lmstudio_rest">lmstudio_rest</option>
            <option value="gemini">gemini</option>
          </select>
        </label>
        <label class="field">
          <span>Model</span>
          <input data-field="model" placeholder="llama3.1">
        </label>
      </div>
      <label class="field">
        <span>Base URL</span>
        <input data-field="base_url" placeholder="http://localhost:11434/api">
      </label>
      <div class="toolbar">
        <button class="secondary" onclick="fetchModels(this)">Fetch Models</button>
      </div>
      <div class="tiny" data-field="model_hint">For LiteLLM or LM Studio REST, start the local proxy/server first, then fetch models and use the returned id.</div>
      <label class="field">
        <span>API Key Environment Variable</span>
        <input data-field="api_key_env" placeholder="OPENAI_API_KEY">
      </label>
      <div class="row">
        <label class="field">
          <span>Temperature</span>
          <input data-field="temperature" placeholder="0.2">
        </label>
        <label class="field">
          <span>Timeout Seconds</span>
          <input data-field="timeout_seconds" placeholder="120">
        </label>
      </div>
      <label class="tiny"><input type="checkbox" data-field="enabled"> Enabled</label>
    </div>
  </template>

  <script>
    const PROFILE_PRESETS = {
      ollama: {
        id: "ollama-local",
        provider: "ollama",
        label: "Local Ollama",
        base_url: "http://localhost:11434/api",
        model: "llama3.1",
        api_key_env: "",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 300
      },
      openai: {
        id: "openai-compatible",
        provider: "openai_compatible",
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4.1-mini",
        api_key_env: "OPENAI_API_KEY",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      },
      lmstudioRest: {
        id: "lm-studio-rest",
        provider: "lmstudio_rest",
        label: "LM Studio REST",
        base_url: "http://localhost:1234/api/v1",
        model: "local-model",
        api_key_env: "",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 300
      },
      litellm: {
        id: "litellm-local",
        provider: "openai_compatible",
        label: "LiteLLM",
        base_url: "http://localhost:4000",
        model: "gpt-5",
        api_key_env: "LITELLM_API_KEY",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 300
      },
      gemini: {
        id: "gemini",
        provider: "gemini",
        label: "Gemini",
        base_url: "https://generativelanguage.googleapis.com/v1beta",
        model: "gemini-2.5-flash",
        api_key_env: "GEMINI_API_KEY",
        enabled: false,
        temperature: 0.2,
        timeout_seconds: 120
      }
    };

    let currentSyncJobId = null;
    let syncPollTimer = null;

    async function boot() {
      await loadStatus();
      await loadSettings();
      await loadSyncJob();
    }

    function status(id, text) {
      document.getElementById(id).textContent = text || "";
    }

    function toggleManual() {
      document.getElementById("manualPanel").classList.toggle("open");
    }

    function requestJson(url, options = {}, timeoutSeconds = 20) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutSeconds * 1000);
      return fetch(url, { ...options, signal: controller.signal })
        .then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data.error) {
            throw new Error(data.error || `${response.status} ${response.statusText}`);
          }
          return data;
        })
        .catch((error) => {
          if (error.name === "AbortError") {
            throw new Error(`Request timed out after ${timeoutSeconds} seconds.`);
          }
          throw error;
        })
        .finally(() => clearTimeout(timer));
    }

    async function loadStatus() {
      try {
        const data = await requestJson("/api/status", {}, 10);
        document.getElementById("configPath").textContent = data.config_path || "not saved";
        document.getElementById("kbRootInput").value = data.root_dir || "";
        document.getElementById("sourceDirsInput").value = (data.source_dirs || []).join("\\n");
        const root = document.getElementById("kbStatusPanel");
        root.innerHTML = "";
        const rows = [
          ["KB", data.root_dir || ""],
          ["Raw", (data.source_dir_status || []).map((item) => item.path).join("; ")],
          ["Index", data.index_path],
          ["Log", data.log_path]
        ];
        for (const [label, value] of rows) {
          const div = document.createElement("div");
          div.textContent = `${label}: ${value || ""}`;
          root.appendChild(div);
        }
      } catch (error) {
        document.getElementById("configPath").textContent = "status unavailable";
        status("pathStatus", `Status load failed: ${error.message}`);
      }
    }

    async function savePaths() {
      try {
        status("pathStatus", "Saving paths...");
        const sourceDirs = document.getElementById("sourceDirsInput").value
          .split(/\\r?\\n|;/)
          .map((item) => item.trim())
          .filter(Boolean);
        const payload = {
          root_dir: document.getElementById("kbRootInput").value.trim(),
          source_dirs: sourceDirs
        };
        await requestJson("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 20);
        await loadStatus();
        await loadSettings();
        status("pathStatus", "Paths saved. This did not run Ingest or call an LLM.");
      } catch (error) {
        status("pathStatus", `Save failed: ${error.message}`);
      }
    }

    function renderProfiles(profiles) {
      const root = document.getElementById("profiles");
      root.innerHTML = "";
      if (!profiles.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No LLM profiles are configured.";
        root.appendChild(empty);
        return;
      }
      for (const profile of profiles) {
        const node = document.getElementById("profileTemplate").content.firstElementChild.cloneNode(true);
        node.querySelector('[data-field="heading"]').textContent = profile.label || profile.id || "Profile";
        node.querySelector('[data-field="id"]').value = profile.id || "";
        node.querySelector('[data-field="label"]').value = profile.label || "";
        node.querySelector('[data-field="provider"]').value = profile.provider || "ollama";
        node.querySelector('[data-field="model"]').value = profile.model || "";
        node.querySelector('[data-field="base_url"]').value = profile.base_url || "";
        const apiKeyInput = node.querySelector('[data-field="api_key_env"]');
        apiKeyInput.value = profile.api_key_env || "";
        apiKeyInput.placeholder = apiKeyPlaceholder(profile);
        node.querySelector('[data-field="temperature"]').value = profile.temperature ?? 0.2;
        node.querySelector('[data-field="timeout_seconds"]').value = profile.timeout_seconds ?? 90;
        node.querySelector('[data-field="enabled"]').checked = Boolean(profile.enabled);
        root.appendChild(node);
      }
    }

    function apiKeyPlaceholder(profile) {
      const provider = profile.provider || "ollama";
      const profileId = String(profile.id || "").toLowerCase();
      const label = String(profile.label || "").toLowerCase();
      const baseUrl = String(profile.base_url || "").toLowerCase();
      if (provider === "openai_compatible" && (profileId.includes("litellm") || label.includes("litellm") || baseUrl.includes(":4000"))) {
        return "LITELLM_API_KEY";
      }
      if (provider === "openai_compatible") {
        return "OPENAI_API_KEY";
      }
      if (provider === "lmstudio_rest") {
        return "LM_STUDIO_API_TOKEN";
      }
      if (provider === "gemini") {
        return "GEMINI_API_KEY";
      }
      return "";
    }

    async function fetchModels(button) {
      const node = button.closest(".profile");
      const provider = node.querySelector('[data-field="provider"]').value;
      const baseUrl = node.querySelector('[data-field="base_url"]').value.trim();
      const apiKeyEnv = node.querySelector('[data-field="api_key_env"]').value.trim();
      const modelInput = node.querySelector('[data-field="model"]');
      const hint = node.querySelector('[data-field="model_hint"]');
      if (!["openai_compatible", "lmstudio_rest"].includes(provider)) {
        hint.textContent = "Fetch Models currently supports OpenAI-compatible, LiteLLM, and LM Studio REST profiles.";
        return;
      }
      if (!baseUrl) {
        hint.textContent = "Enter a Base URL before fetching models.";
        return;
      }
      try {
        hint.textContent = "Fetching models...";
        const params = new URLSearchParams({
          provider,
          base_url: baseUrl
        });
        if (apiKeyEnv) {
          params.set("api_key_env", apiKeyEnv);
        }
        const data = await requestJson(`/api/models?${params.toString()}`, {}, 8);
        const models = data.models || [];
        if (!models.length) {
          hint.textContent = "No models returned by this endpoint.";
          return;
        }
        const first = models[0];
        modelInput.value = typeof first === "string" ? first : (first.id || "");
        hint.textContent = `Models: ${models.map((item) => typeof item === "string" ? item : (item.label || item.id || "")).join(", ")}`;
      } catch (error) {
        hint.textContent = `Model fetch failed: ${error.message}`;
      }
    }

    function addProfile(kind) {
      const preset = PROFILE_PRESETS[kind] || PROFILE_PRESETS.ollama;
      const profile = { ...preset };
      profile.id = uniqueProfileId(profile.id);
      if (profile.id !== preset.id) {
        profile.label = `${preset.label} ${profile.id.replace(`${preset.id}-`, "")}`;
      }
      renderProfiles([...collectProfiles({ includeIncomplete: true }), profile]);
      status("settingsStatus", `Added ${profile.label}.`);
    }

    function removeProfile(button) {
      button.closest(".profile").remove();
      status("settingsStatus", "Profile removed. Save settings to keep this change.");
    }

    function uniqueProfileId(base) {
      const ids = new Set(collectProfiles({ includeIncomplete: true }).map((profile) => profile.id).filter(Boolean));
      if (!ids.has(base)) {
        return base;
      }
      let index = 2;
      while (ids.has(`${base}-${index}`)) {
        index += 1;
      }
      return `${base}-${index}`;
    }

    function collectProfiles(options = {}) {
      return [...document.querySelectorAll(".profile")].map((node) => ({
        id: node.querySelector('[data-field="id"]').value.trim(),
        label: node.querySelector('[data-field="label"]').value.trim(),
        provider: node.querySelector('[data-field="provider"]').value,
        model: node.querySelector('[data-field="model"]').value.trim(),
        base_url: node.querySelector('[data-field="base_url"]').value.trim(),
        api_key_env: node.querySelector('[data-field="api_key_env"]').value.trim(),
        enabled: node.querySelector('[data-field="enabled"]').checked,
        temperature: Number(node.querySelector('[data-field="temperature"]').value || "0.2"),
        timeout_seconds: Number(node.querySelector('[data-field="timeout_seconds"]').value || "90")
      })).filter((profile) => options.includeIncomplete || profile.id);
    }

    async function loadSettings() {
      try {
        status("settingsStatus", "Loading settings...");
        const data = await requestJson("/api/settings", {}, 10);
        document.getElementById("activeProfile").value = data.active_profile || "";
        document.getElementById("fallbackOrder").value = (data.fallback_order || []).join(", ");
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Settings loaded.");
      } catch (error) {
        renderProfiles([]);
        status("settingsStatus", `Settings load failed: ${error.message}`);
      }
    }

    async function restoreDefaults() {
      try {
        status("settingsStatus", "Restoring default profiles...");
        const data = await requestJson("/api/settings/defaults", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        }, 20);
        document.getElementById("activeProfile").value = data.active_profile || "";
        document.getElementById("fallbackOrder").value = (data.fallback_order || []).join(", ");
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Default profiles restored.");
      } catch (error) {
        status("settingsStatus", `Restore failed: ${error.message}`);
      }
    }

    async function saveSettings() {
      try {
        status("settingsStatus", "Saving settings...");
        const profiles = collectProfiles({ includeIncomplete: true });
        const missingId = profiles.find((profile) => !profile.id);
        if (missingId) {
          status("settingsStatus", "Every profile needs a Profile ID before saving.");
          return;
        }
        const seen = new Set();
        for (const profile of profiles) {
          if (seen.has(profile.id)) {
            status("settingsStatus", `Duplicate profile id: ${profile.id}`);
            return;
          }
          seen.add(profile.id);
        }
        const payload = {
          active_profile: document.getElementById("activeProfile").value.trim() || null,
          fallback_order: document.getElementById("fallbackOrder").value.split(",").map((item) => item.trim()).filter(Boolean),
          profiles
        };
        const data = await requestJson("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 20);
        renderProfiles(data.profiles || []);
        status("settingsStatus", "Settings saved. This did not call an LLM.");
      } catch (error) {
        status("settingsStatus", `Save failed: ${error.message}`);
      }
    }

    async function loadSyncJob(jobId = "") {
      try {
        const suffix = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
        const data = await requestJson(`/api/sync${suffix}`, {}, 10);
        if (!data.job) {
          currentSyncJobId = null;
          stopSyncPolling();
          setSyncActionButtons(null);
          return;
        }
        currentSyncJobId = data.job.job_id || null;
        renderSyncJob(data.job);
        if (isSyncJobActive(data.job)) {
          queueSyncPoll();
        } else {
          stopSyncPolling();
        }
      } catch (error) {
        status("syncStatus", `Sync status failed: ${error.message}`);
      }
    }

    function isSyncJobActive(job) {
      return job && ["queued", "running"].includes(job.status);
    }

    function queueSyncPoll(delayMs = 1500) {
      stopSyncPolling();
      syncPollTimer = setTimeout(() => {
        pollSyncJob().catch((error) => {
          status("syncStatus", `Sync status failed: ${error.message}`);
        });
      }, delayMs);
    }

    function stopSyncPolling() {
      if (syncPollTimer) {
        clearTimeout(syncPollTimer);
        syncPollTimer = null;
      }
    }

    function setSyncActionButtons(job) {
      const startButton = document.getElementById("syncButton");
      const stopButton = document.getElementById("syncStopButton");
      const resumeButton = document.getElementById("syncResumeButton");
      if (!startButton || !stopButton || !resumeButton) {
        return;
      }
      const isBusy = isSyncJobActive(job);
      const canResume = Boolean(job && ["failed", "stopped"].includes(job.status));
      startButton.disabled = Boolean(isBusy);
      startButton.textContent = isBusy ? "Sync Running..." : "Sync Now";
      stopButton.disabled = !isBusy;
      resumeButton.disabled = !canResume;
    }

    async function pollSyncJob() {
      if (!currentSyncJobId) {
        stopSyncPolling();
        return;
      }
      const data = await requestJson(`/api/sync?job_id=${encodeURIComponent(currentSyncJobId)}`, {}, 10);
      if (!data.job) {
        currentSyncJobId = null;
        stopSyncPolling();
        setSyncActionButtons(null);
        return;
      }
      renderSyncJob(data.job);
      if (isSyncJobActive(data.job)) {
        queueSyncPoll();
      } else {
        stopSyncPolling();
        await loadStatus();
      }
    }

    function renderSyncJob(job) {
      const root = document.getElementById("syncOutput");
      root.innerHTML = "";
      setSyncActionButtons(job);

      const summary = document.createElement("div");
      summary.className = "result";
      const progressParts = [];
      if (job.total_sources !== null && job.total_sources !== undefined) {
        progressParts.push(`processed ${job.processed_sources || 0} / ${job.total_sources} source(s)`);
      } else {
        progressParts.push(`processed ${job.processed_sources || 0} source(s)`);
      }
      if (job.requested_limit !== null && job.requested_limit !== undefined) {
        progressParts.push(`limit ${job.requested_limit}`);
      }
      progressParts.push(`created ${job.created_pages || 0}`);
      progressParts.push(`updated ${job.updated_pages || 0}`);
      const detailLines = [];
      if (job.current_source_title) {
        detailLines.push(`<div>Current source: ${escapeHtml(job.current_source_title)}</div>`);
      }
      if (job.current_relative_path) {
        detailLines.push(`<div class="tiny">${escapeHtml(job.current_relative_path)}</div>`);
      }
      if (job.error) {
        detailLines.push(`<div>${escapeHtml(job.error)}</div>`);
      }
      summary.innerHTML = `
        <h3>Sync ${escapeHtml(titleCase(job.status || "queued"))}</h3>
        <div class="meta">${progressParts.join(" / ")}</div>
        <div>${escapeHtml(job.message || "")}</div>
        ${detailLines.join("")}
      `;
      root.appendChild(summary);

      for (const page of job.pages || []) {
        const item = document.createElement("div");
        item.className = "result";
        item.innerHTML = `<h3>${escapeHtml(page.name)}</h3><div class="meta">${escapeHtml(page.kind)} / ${escapeHtml(page.action)} / ${escapeHtml(page.path)}</div>`;
        root.appendChild(item);
      }

      if (job.status === "failed") {
        status("syncStatus", job.error ? `Sync failed: ${job.error}` : "Sync failed.");
      } else if (job.status === "stopped") {
        status("syncStatus", job.message || "Sync stopped.");
      } else if (isSyncJobActive(job)) {
        status("syncStatus", job.message || "Sync running...");
      } else {
        status("syncStatus", job.message || "Sync complete.");
      }
    }

    function titleCase(value) {
      const text = String(value || "").trim();
      if (!text) {
        return "";
      }
      return text.charAt(0).toUpperCase() + text.slice(1);
    }

    async function runSearch() {
      const query = document.getElementById("searchInput").value.trim();
      if (!query) {
        status("searchStatus", "Enter a search query.");
        return;
      }
      status("searchStatus", "Searching...");
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      const root = document.getElementById("searchResults");
      root.innerHTML = "";
      for (const item of data.results || []) {
        const div = document.createElement("div");
        div.className = "result";
        const links = [];
        if (item.document_url) {
          links.push(`<a class="button-link secondary" href="${escapeAttribute(item.document_url)}" target="_blank" rel="noopener">Open Result</a>`);
        }
        if (item.raw_url && item.raw_url !== item.document_url) {
          links.push(`<a class="button-link secondary" href="${escapeAttribute(item.raw_url)}" target="_blank" rel="noopener">Open Raw</a>`);
        }
        div.innerHTML = `
          <h3>${escapeHtml(item.title)}</h3>
          <div class="meta">${escapeHtml(item.kind)} / ${escapeHtml(item.rel_path)} / score ${item.score}</div>
          <div>${escapeHtml(item.snippet)}</div>
          ${links.length ? `<div class="toolbar">${links.join("")}</div>` : ""}
        `;
        root.appendChild(div);
      }
      status("searchStatus", `${(data.results || []).length} result(s).`);
    }

    async function runQuery(save) {
      const question = document.getElementById("questionInput").value.trim();
      if (!question) {
        status("queryStatus", "Enter a question.");
        return;
      }
      const root = document.getElementById("queryOutput");
      root.innerHTML = "";
      status("queryStatus", save ? "Searching and streaming answer..." : "Searching and streaming answer...");

      const meta = document.createElement("div");
      meta.className = "result";
      meta.innerHTML = `<h3>${escapeHtml(question)}</h3><div class="meta">Waiting for the model to respond...</div>`;
      root.appendChild(meta);
      const answer = document.createElement("div");
      answer.className = "mono";
      answer.textContent = "";
      root.appendChild(answer);

      let response;
      try {
        response = await fetch("/api/query/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, save })
        });
      } catch (error) {
        status("queryStatus", `Query failed to start: ${error.message}`);
        return;
      }

      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        status("queryStatus", failure.error || `${response.status} ${response.statusText}`);
        return;
      }

      if (!response.body) {
        status("queryStatus", "Streaming is not available in this browser.");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamedText = "";
      let sawDone = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let newlineIndex = buffer.indexOf("\\n");
        while (newlineIndex >= 0) {
          const line = buffer.slice(0, newlineIndex).trim();
          buffer = buffer.slice(newlineIndex + 1);
          if (line) {
            const event = JSON.parse(line);
            if (event.type === "start") {
              status("queryStatus", event.message || "Starting model...");
            } else if (event.type === "chunk") {
              streamedText += event.text || "";
              answer.textContent = streamedText;
              status("queryStatus", "Streaming answer...");
            } else if (event.type === "done") {
              sawDone = true;
              renderFinalQueryResult(root, meta, answer, event, streamedText || event.answer_markdown || "");
              status("queryStatus", "Answer ready.");
            } else if (event.type === "error") {
              status("queryStatus", event.error || "Query failed.");
            }
          }
          newlineIndex = buffer.indexOf("\\n");
        }
      }
      if (!sawDone && streamedText) {
        status("queryStatus", "Streaming ended before a final summary arrived.");
      }
    }

    function renderFinalQueryResult(root, meta, answer, data, fallbackAnswer) {
      meta.innerHTML = `<h3>${escapeHtml(data.title || "Answer")}</h3><div class="meta">${data.saved_path ? escapeHtml(data.saved_path) : "not saved"}${data.llm ? ` / ${escapeHtml(data.llm.profile_id)} / ${escapeHtml(data.llm.model)}` : ""}</div>`;
      answer.textContent = data.answer_markdown || fallbackAnswer || "";

      const existingFollow = root.querySelector('[data-query-section="follow"]');
      if (existingFollow) {
        existingFollow.remove();
      }
      const existingSources = root.querySelector('[data-query-section="sources"]');
      if (existingSources) {
        existingSources.remove();
      }

      if ((data.follow_up_questions || []).length) {
        const follow = document.createElement("div");
        follow.className = "result";
        follow.dataset.querySection = "follow";
        follow.innerHTML = `<h3>Follow-up Questions</h3>${data.follow_up_questions.map((item) => `<div>- ${escapeHtml(item)}</div>`).join("")}`;
        root.appendChild(follow);
      }

      if ((data.search_results || []).length) {
        const sources = document.createElement("div");
        sources.className = "result";
        sources.dataset.querySection = "sources";
        sources.innerHTML = `
          <h3>Sources</h3>
          ${(data.search_results || []).map((item) => {
            const links = [];
            if (item.document_url) {
              links.push(`<a class="button-link secondary" href="${escapeAttribute(item.document_url)}" target="_blank" rel="noopener">Open Result</a>`);
            }
            if (item.raw_url && item.raw_url !== item.document_url) {
              links.push(`<a class="button-link secondary" href="${escapeAttribute(item.raw_url)}" target="_blank" rel="noopener">Open Raw</a>`);
            }
            return `
              <div>
                <div class="meta">${escapeHtml(item.title)} / ${escapeHtml(item.kind)} / ${escapeHtml(item.rel_path)}</div>
                ${links.length ? `<div class="toolbar">${links.join("")}</div>` : ""}
              </div>
            `;
          }).join("")}
        `;
        root.appendChild(sources);
      }
    }

    async function runIngest(options = {}) {
      const root = document.getElementById("ingestOutput");
      root.innerHTML = "";
      status("ingestStatus", options.confirmFallback ? "Ingesting with fallback mode..." : "Ingesting...");
      let response;
      try {
        response = await fetch("/api/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirm_fallback: Boolean(options.confirmFallback)
          })
        });
      } catch (error) {
        status("ingestStatus", `Ingest failed to start: ${error.message}`);
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (data.requires_confirmation) {
          const proceed = window.confirm(
            data.warning || "No enabled LLM profile is configured yet. Continue with fallback ingest?"
          );
          if (!proceed) {
            status("ingestStatus", "Ingest cancelled so you can configure an LLM first.");
            return;
          }
          await runIngest({ confirmFallback: true });
          return;
        }
        status("ingestStatus", data.error || `${response.status} ${response.statusText}`);
        return;
      }
      const summary = document.createElement("div");
      summary.className = "result";
      summary.innerHTML = `<h3>Ingest Summary</h3><div class="meta">sources ${data.source_count} / raw copies ${data.copied_raw_count} / source notes ${data.source_note_count} / mode ${escapeHtml(data.ingest_mode || "standard")}</div><div class="tiny">${escapeHtml(data.index_path || "")}</div><div class="tiny">${escapeHtml(data.source_catalog_path || "")}</div>`;
      root.appendChild(summary);
      await loadStatus();
      status("ingestStatus", data.ingest_mode === "fallback" ? "Fallback ingest complete." : "Ingest complete.");
    }

    async function runLint() {
      status("lintStatus", "Linting...");
      const response = await fetch("/api/lint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const data = await response.json();
      const root = document.getElementById("lintOutput");
      root.innerHTML = "";
      if (data.error) {
        status("lintStatus", data.error);
        return;
      }
      const summary = document.createElement("div");
      summary.className = "result";
      if (data.passed) {
        summary.innerHTML = '<h3>Lint Passed</h3><div class="meta">No warnings found.</div>';
      } else {
        summary.innerHTML = `<h3>Lint Warnings</h3>${(data.warnings || []).map((item) => `<div>${escapeHtml(item)}</div>`).join("")}`;
      }
      root.appendChild(summary);
      status("lintStatus", data.passed ? "Lint passed." : `${(data.warnings || []).length} warning(s).`);
    }

    async function runSync() {
      try {
        status("syncStatus", "Starting sync...");
        const raw = document.getElementById("syncLimit").value.trim();
        const payload = raw ? { limit: Number(raw) } : {};
        const data = await requestJson("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 15);
        if (!data.job) {
          status("syncStatus", "Sync did not return a job.");
          return;
        }
        currentSyncJobId = data.job.job_id || null;
        renderSyncJob(data.job);
        if (data.started) {
          status("syncStatus", data.job.message || "Sync started in the background.");
        } else {
          status("syncStatus", "A sync job is already running. Showing current progress.");
        }
        if (isSyncJobActive(data.job)) {
          queueSyncPoll(1000);
        }
      } catch (error) {
        status("syncStatus", `Sync failed to start: ${error.message}`);
      }
    }

    async function stopSync() {
      try {
        status("syncStatus", "Requesting stop...");
        const payload = currentSyncJobId ? { job_id: currentSyncJobId } : {};
        const data = await requestJson("/api/sync/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 10);
        if (!data.job) {
          status("syncStatus", "No sync job is available to stop.");
          return;
        }
        currentSyncJobId = data.job.job_id || currentSyncJobId;
        renderSyncJob(data.job);
        if (isSyncJobActive(data.job)) {
          queueSyncPoll(1000);
        }
      } catch (error) {
        status("syncStatus", `Stop request failed: ${error.message}`);
      }
    }

    async function resumeSync() {
      try {
        status("syncStatus", "Resuming sync...");
        const raw = document.getElementById("syncLimit").value.trim();
        const payload = raw ? { limit: Number(raw) } : {};
        const data = await requestJson("/api/sync/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }, 15);
        if (!data.job) {
          status("syncStatus", "Resume did not return a job.");
          return;
        }
        currentSyncJobId = data.job.job_id || null;
        renderSyncJob(data.job);
        if (isSyncJobActive(data.job)) {
          queueSyncPoll(1000);
        }
      } catch (error) {
        status("syncStatus", `Resume failed to start: ${error.message}`);
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function escapeAttribute(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll('"', "&quot;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }

    boot();
  </script>
</body>
</html>
"""


MANUAL_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LMIT-2 Web UI Guide</title>
  <style>
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans TC", "Noto Sans", sans-serif;
      color: #1e2430;
      background:
        radial-gradient(circle at top left, rgba(19, 111, 99, 0.14), transparent 28%),
        radial-gradient(circle at bottom right, rgba(192, 93, 51, 0.12), transparent 22%),
        #f6f1e8;
      line-height: 1.65;
    }
    main {
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }
    section {
      background: rgba(255, 250, 242, 0.96);
      border: 1px solid #d8cbb8;
      border-radius: 8px;
      padding: 20px;
      margin-top: 16px;
    }
    h1, h2 { line-height: 1.2; }
    code {
      background: #e9f3ee;
      padding: 2px 5px;
      border-radius: 4px;
    }
    li { margin: 6px 0; }
  </style>
</head>
<body>
  <main>
    <h1>LMIT-2 Web UI 操作教學</h1>
    <section>
      <h2>第一次啟動</h2>
      <ol>
        <li>開啟 <code>LMIT-2 Wiki Console</code>。</li>
        <li>第一次啟動若還沒有 <code>%APPDATA%\\LMIT-2\\wiki-only.toml</code>，啟動器會先請你選擇 knowledge base 資料夾與 LMIT-1 raw Markdown 來源資料夾。</li>
        <li>進入 Web UI 後，仍可在 <code>Knowledge Base Path</code> 與 <code>Raw Source Paths</code> 修改路徑。</li>
        <li>按 <code>Save Paths</code>。LMIT-2 會寫回目前使用者的 <code>wiki-only.toml</code>，並初始化 knowledge base 目錄；它不會執行 Ingest，也不會呼叫任何 LLM。</li>
      </ol>
    </section>
    <section>
      <h2>日常流程</h2>
      <ol>
        <li><code>Ingest</code>：讀取 raw Markdown，複製成安全短檔名，產生 source notes、manifest、精簡首頁與 <code>Source Catalog</code>。</li>
        <li>若目前沒有啟用任何 LLM profile，<code>Ingest</code> 會先跳出警示。你可以先去設定 LLM，或明確選擇 fallback ingest。</li>
        <li>fallback ingest 仍會保存 raw/source note/manifest traceability，但首頁不會退化成整頁 source dump。</li>
        <li><code>wiki/index.md</code> 現在會保留目前的 curation 狀態；fallback ingest 後仍會顯示 pre-curation 提示，不會在下一次 refresh 時遺失。</li>
        <li>首頁也會顯示最近一次 sync 摘要與最近的 query pages，讓它更像真正的 wiki 首頁，而不是靜態檔案總表。</li>
        <li>系統也會自動維護三個 hub pages：<code>Knowledge Map</code>、<code>Recent Work</code>、<code>Open Questions</code>，作為更高信號的導航層。</li>
        <li><code>Lint</code>：檢查 knowledge base 必要目錄與索引是否存在。</li>
        <li><code>Search</code>：查詢已 ingest 的 source notes、raw copy 與 wiki 頁面。</li>
        <li>搜尋結果可用 <code>Open Result</code> 打開目前文件；若結果對應 source note，還會出現 <code>Open Raw</code> 直接打開 raw markdown。</li>
        <li><code>Ask The Wiki</code>：根據目前 wiki 回答問題；<code>Ask And Save</code> 會把結果存入 <code>wiki/queries</code>。</li>
        <li><code>Ask The Wiki</code> 現在會串流顯示答案，只要模型已開始輸出 token，畫面就會持續更新。</li>
        <li><code>Sync Now</code>：背景執行 LLM auto sync，把已 ingest 的素材提升成 topic/entity pages，並更新首頁訊號。</li>
        <li><code>Stop Sync</code>：要求目前背景 sync 在當前 source 完成後停止，不會回滾已完成的 page update。</li>
        <li><code>Resume Sync</code>：從下一筆未完成 source 繼續，不會重跑已成功完成的 source。</li>
      </ol>
    </section>
    <section>
      <h2>LLM Settings</h2>
      <ul>
        <li><code>Add Ollama</code> 建立本機 Ollama profile；通常不需要 API key env。</li>
        <li><code>Add LM Studio REST</code> 建立 LM Studio 原生 REST profile，預設使用 <code>http://localhost:1234/api/v1</code>；若你在 LM Studio 啟用了 API token，可填入對應環境變數名稱。</li>
        <li><code>Add LiteLLM</code> 建立本機 LiteLLM proxy profile，預設使用 <code>http://localhost:4000</code>；若你的 LiteLLM proxy 啟用了 master key，可填入例如 <code>LITELLM_API_KEY</code>。</li>
        <li>本機 LLM profile 預設 <code>Timeout Seconds</code> 為 300；慢模型或長上下文可再往上調。</li>
        <li><code>Add OpenAI</code> 或 <code>Add Gemini</code> 只保存環境變數名稱，不保存密鑰值。</li>
        <li>API key 可放在 Windows 使用者/系統環境變數，也可放在安裝資料夾的 <code>.env</code> 檔，例如 <code>OPENAI_API_KEY=...</code>。</li>
        <li>LM Studio REST 或 LiteLLM 的 <code>Model</code> 要填 API 回傳的 model id 或 model key。先啟動 local server/proxy，再按 <code>Fetch Models</code>。</li>
        <li>若 <code>Fetch Models</code> 顯示無法連線，通常是本機 server/proxy 沒啟動、port 不正確，或被防火牆/權限擋住。</li>
        <li><code>Ask The Wiki</code> 串流最適合本機 Ollama、LiteLLM 與 LM Studio REST；若 provider 不支援串流，仍會在完成時一次顯示結果。</li>
        <li><code>Active Profile</code> 是優先使用的 profile；<code>Fallback Order</code> 是失敗時的備援順序。</li>
        <li>修改 profile 後必須按 <code>Save Settings</code>。</li>
        <li><code>Restore Defaults</code> 會重建預設 profile 清單。</li>
      </ul>
    </section>
    <section>
      <h2>LM Studio 與 LiteLLM</h2>
      <ul>
        <li>LM Studio 原生 REST API 使用 <code>/api/v1/*</code>，LMIT-2 的預設 LM Studio profile 只保留這條路徑。</li>
        <li>LiteLLM proxy 提供 OpenAI-style gateway，官方 quick start 預設會跑在 <code>http://localhost:4000</code>。</li>
        <li>LiteLLM 與 OpenAI / Anthropic / Gemini 等遠端模型整合時，通常是把真正的 provider key 配在 LiteLLM proxy 上，LMIT-2 只需要連到 proxy。</li>
        <li>如果 query 回傳 <code>timed out</code>，先把該 profile 的 <code>Timeout Seconds</code> 提高，再重試。</li>
        <li>如果 <code>Sync Now</code> 也回傳 <code>timed out</code>，處理方式相同，因為它使用同一組 LLM profile 與 timeout 設定。</li>
      </ul>
    </section>
    <section>
      <h2>Auto Sync 與排程</h2>
      <ul>
        <li><code>Sync Now</code> 會在背景工作執行，不會把瀏覽器卡在同一個 HTTP 請求上。</li>
        <li>只要 Web UI 還開著，就會持續輪詢並顯示目前進度與完成結果。</li>
        <li>第一次安裝不建議立即建立排程，因為路徑、ingest 結果與 LLM profile 通常還沒確認。</li>
        <li>需要自動化時，再重新安裝並勾選排程，或用工作排程器手動建立。</li>
      </ul>
    </section>
    <section>
      <h2>疑難排解</h2>
      <ul>
        <li>Web UI 打不開時，先確認 <code>127.0.0.1:8765</code> 沒被其他程式佔用。</li>
        <li>啟動器錯誤記錄位於 <code>%APPDATA%\\LMIT-2\\logs</code>。</li>
        <li>若需要從命令列關閉目前的 Web UI server，可執行 <code>lmit-wiki stop --config "%APPDATA%\\LMIT-2\\wiki-only.toml"</code>。</li>
        <li>Ingest 找不到資料時，檢查 <code>Raw Source Paths</code> 是否指向 LMIT-1 的 <code>output/raw</code>。</li>
        <li><code>Save Paths</code> 只儲存路徑與初始化 KB，不會執行 Ingest，也不會呼叫任何 LLM。</li>
        <li>如果按鈕顯示 timeout，通常是 server 未回應、路徑位於慢速/離線磁碟，或另一個長時間操作仍在執行。</li>
        <li>如果只有 <code>Ask The Wiki</code> 沒有任何串流輸出，先確認模型已載入、provider 支援串流，或提高 <code>Timeout Seconds</code>。</li>
        <li>如果 <code>Sync Now</code> 顯示背景任務失敗，先看進度訊息，再檢查目前 LLM profile 的 <code>Timeout Seconds</code>。</li>
        <li>如果背景 sync 因模型回傳雜訊而失敗，<code>Resume Sync</code> 會從下一筆未完成 source 接著跑；已成功完成的 source 不會重做。</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""

