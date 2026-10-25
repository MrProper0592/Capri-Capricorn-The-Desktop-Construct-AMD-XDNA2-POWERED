"""
model_client.py
AMD XDNA2 NPU Client (via FastFlowLM `flm serve` on 127.0.0.1:52625)
with multi-backend fallback to Ollama (127.0.0.1:11434) or OpenAI-compatible
servers (e.g., Lemonade Server).

Automatically injects Capri's SYSTEM persona and generation parameters from
the repository Modelfile, strips `<think>...</think>` blocks and leading
`"Capri: "` prefixes during streaming, and records real-time XDNA2 NPU tok/s
metrics in `last_metrics`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from capri.ai.xdna2_engine import (
    DEFAULT_FLM_HOST,
    DEFAULT_FLM_PORT,
    ModelfileConfig,
    XDNA2Engine,
    get_engine,
    parse_modelfile,
)

FLM_BASE_URL = os.environ.get(
    "CAPRI_FLM_URL", f"http://{DEFAULT_FLM_HOST}:{DEFAULT_FLM_PORT}"
).rstrip("/")
OLLAMA_BASE_URL = os.environ.get("CAPRI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OPENAI_BASE_URL = os.environ.get("CAPRI_OPENAI_URL", f"{FLM_BASE_URL}/v1").rstrip("/")

# Backwards-compatible module constants
_DEFAULT_MODELFILE = parse_modelfile()
SYSTEM_PROMPT: str = _DEFAULT_MODELFILE.system_prompt
MODEL_NAME: str = os.environ.get("CAPRI_MODEL", _DEFAULT_MODELFILE.base_model or "gemma3:4b")
OLLAMA_URL: str = f"{FLM_BASE_URL}/api/chat"


def clean_reply_text(text: str) -> str:
    """
    Strip `<think>...</think>` reasoning blocks (including unclosed trailing blocks)
    and any leading `"Capri: "` / `"Capri > "` speaker prefix echoed by small models.
    """
    if not text:
        return ""
    # Strip complete <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
    # If an unclosed <think> tag remains, strip from <think> to end
    if "<think>" in cleaned.lower():
        cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Strip leading "Capri: " or "Capri > " prefix
    cleaned = re.sub(r"^\s*capri\s*[:>]\s*", "", cleaned, count=1, flags=re.IGNORECASE)
    # Strip surrounding quotes only if the entire reply was wrapped in quotes by mistake
    cleaned = cleaned.strip()
    return cleaned


class _StreamFilter:
    """
    Stateful token stream filter that suppresses `<think>...</think>` blocks and
    leading `"Capri: "` prefixes before tokens are emitted to `on_token`.
    """

    _PREFIX_PATTERN = re.compile(r"^(\s*capri\s*[:>]\s*)", flags=re.IGNORECASE)

    def __init__(self, on_token: Callable[[str], None]) -> None:
        self._on_token = on_token
        self._buffer = ""
        self._in_think = False
        self._header_checked = False
        self.emitted_text = ""

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk
        self._process(final=False)

    def flush(self) -> str:
        self._process(final=True)
        return self.emitted_text

    def _emit(self, text: str) -> None:
        if not text:
            return
        self.emitted_text += text
        self._on_token(text)

    def _process(self, final: bool = False) -> None:
        while self._buffer:
            if self._in_think:
                end_idx = self._buffer.lower().find("</think>")
                if end_idx == -1:
                    if final:
                        # Unclosed <think> block at end of stream; discard
                        self._buffer = ""
                    return
                # Advance past </think> and any immediate leading whitespace/newlines
                self._buffer = self._buffer[end_idx + len("</think>") :].lstrip("\r\n ")
                self._in_think = False
                continue

            # Check if a <think> block is starting
            lower_buf = self._buffer.lower()
            think_idx = lower_buf.find("<think>")
            if think_idx != -1:
                before = self._buffer[:think_idx]
                if before:
                    self._emit_with_prefix_check(before, final=True)
                self._buffer = self._buffer[think_idx + len("<think>") :]
                self._in_think = True
                continue

            # Hold if buffer ends with a partial prefix of "<think>"
            if not final and self._ends_with_partial_tag(lower_buf, "<think>"):
                return

            text_to_check = self._buffer
            self._buffer = ""
            self._emit_with_prefix_check(text_to_check, final=final)
            if not final:
                return

    def _emit_with_prefix_check(self, text: str, final: bool = False) -> None:
        if self._header_checked:
            self._emit(text)
            return

        # Wait until we have enough characters to determine if reply starts with "Capri: "
        stripped = text.lstrip()
        if not stripped and not final:
            self._buffer = text
            return

        # Could still be forming "Capri: " if shorter than 8 chars and matches prefix of "capri"
        if (
            not final
            and len(stripped) < 8
            and (
                "capri".startswith(stripped.lower().rstrip(":> "))
                or stripped.lower().startswith("capri")
            )
        ):
            self._buffer = text
            return

        cleaned = self._PREFIX_PATTERN.sub("", text, count=1)
        self._header_checked = True
        self._emit(cleaned)

    @staticmethod
    def _ends_with_partial_tag(text: str, tag: str) -> bool:
        for length in range(1, len(tag)):
            if text.endswith(tag[:length]):
                return True
        return False


class XDNA2Client:
    """
    Streams chat completions from AMD Ryzen AI XDNA2 NPU via FastFlowLM (`flm serve`
    on `http://127.0.0.1:52625`), with automatic Modelfile persona injection and
    optional fallback to Ollama (`http://127.0.0.1:11434`) or OpenAI-compatible servers.
    """

    def __init__(
        self,
        model: str | None = None,
        backend: str = "xdna2",
        base_url: str | None = None,
        auto_start_server: bool = True,
        modelfile_path: str | None = None,
        engine: XDNA2Engine | None = None,
        pmode: str | None = None,
        port: int | None = None,
        auto_serve: bool | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._history: list[dict[str, str]] = []

        self.modelfile: ModelfileConfig = parse_modelfile(modelfile_path)
        self.system_prompt: str = self.modelfile.system_prompt
        self.options: dict[str, Any] = self.modelfile.to_options_dict()

        raw_backend = os.environ.get("CAPRI_BACKEND", backend).lower().strip()
        self.backend: str = "xdna2" if raw_backend in ("auto", "xdna2", "flm") else raw_backend
        self.auto_start_server: bool = auto_start_server if auto_serve is None else bool(auto_serve)
        self.pmode: str = pmode or os.environ.get("CAPRI_PMODE", "performance")
        self.port: int = int(port or os.environ.get("CAPRI_PORT", DEFAULT_FLM_PORT))

        self.engine: XDNA2Engine = engine or get_engine(
            model=model,
            port=self.port,
            pmode=self.pmode,
            auto_start=False,
            modelfile_path=modelfile_path,
        )
        self.engine.port = self.port
        self.engine.pmode = self.pmode

        if self.backend == "xdna2":
            self.base_url = (base_url or self.engine.base_url).rstrip("/")
            self.model_name = self.engine.resolve_model(
                model or os.environ.get("CAPRI_MODEL") or self.modelfile.base_model
            )
        elif self.backend == "ollama":
            self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
            self.model_name = model or os.environ.get("CAPRI_MODEL") or "capri:latest"
        else:
            # Generic OpenAI-compatible endpoint (e.g. Lemonade Server or FLM /v1)
            self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
            self.model_name = (
                model or os.environ.get("CAPRI_MODEL") or self.modelfile.base_model
            )

        self.last_metrics: dict[str, Any] = {
            "backend": self.backend,
            "model": self.model_name,
            "tokens_per_sec": 0.0,
            "tok_per_sec": 0.0,
            "eval_count": 0,
            "eval_duration_ns": 0,
            "eval_duration_s": 0.0,
            "prompt_eval_count": 0,
            "prompt_eval_duration_ns": 0,
            "prompt_eval_duration_s": 0.0,
            "total_duration_s": 0.0,
            "ttft_s": 0.0,
        }

    # ── Public API ────────────────────────────────────────────────────

    def send(
        self,
        user_message: str,
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """
        Non-blocking. Spawns a background daemon thread to stream inference from the
        XDNA2 NPU (or configured fallback backend).

        on_token(chunk)  — called for each streamed token
        on_done(full)    — called when generation is complete
        on_error(msg)    — called if something goes wrong
        """
        thread = threading.Thread(
            target=self._stream,
            args=(user_message, on_token, on_done, on_error),
            daemon=True,
        )
        thread.start()

    def clear_history(self) -> None:
        """Clear conversation history."""
        with self._lock:
            self._history.clear()

    def is_connected(self) -> bool:
        """
        Check if the active inference backend is reachable.
        If `backend == "xdna2"` and `auto_start_server` is True, automatically
        starts `flm serve` on the XDNA2 NPU if it is not yet running.
        """
        if self.backend == "xdna2":
            if self.engine.is_server_running(timeout=1.5):
                return True
            if self.auto_start_server:
                started = self.engine.start_server(
                    model=self.model_name, wait=True, timeout=18.0
                )
                if started:
                    self.model_name = self.engine.active_model
                    return True
            # Optional fallback check: if Ollama is running locally when XDNA2 is unavailable
            if self._ping_url(f"{OLLAMA_BASE_URL}/api/tags"):
                print("[AI] XDNA2 server unavailable; falling back to local Ollama.")
                self.backend = "ollama"
                self.base_url = OLLAMA_BASE_URL
                return True
            return False

        if self.backend == "ollama":
            return self._ping_url(f"{self.base_url}/api/tags")

        # OpenAI-compatible server
        models_url = (
            f"{self.base_url}/models"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/models"
        )
        return self._ping_url(models_url)

    def get_metrics(self) -> dict[str, Any]:
        """Return a copy of the most recent generation performance metrics."""
        with self._lock:
            return dict(self.last_metrics)

    def get_npu_telemetry(self) -> dict[str, Any]:
        """Return live AMD XDNA2 NPU telemetry combined with latest tok/s metrics."""
        telemetry = self.engine.get_npu_telemetry()
        telemetry["last_metrics"] = self.get_metrics()
        telemetry["backend"] = self.backend
        return telemetry

    @property
    def model(self) -> str:
        return self.model_name

    @model.setter
    def model(self, value: str) -> None:
        self.model_name = value

    def set_model(self, model_tag: str) -> str:
        """Switch active inference model (restarting managed flm serve if needed)."""
        tag = model_tag.strip()
        if not tag:
            return f"ACTIVE MODEL: {self.model_name}"
        if self.backend == "xdna2":
            resolved = self.engine.resolve_model(tag)
            if resolved != self.engine.active_model and self.engine.is_server_running(timeout=1.0):
                self.engine.stop_server()
                self.engine.start_server(model=resolved, wait=True, timeout=18.0)
            self.model_name = resolved
            return f"XDNA2 ACTIVE MODEL SWITCHED TO: {self.model_name}"
        self.model_name = tag
        return f"ACTIVE MODEL SWITCHED TO: {self.model_name}"

    def get_status_report(self) -> str:
        """Return formatted NPU hardware & inference performance report."""
        report = self.engine.get_status_report()
        m = self.get_metrics()
        tok_s = m.get("tok_per_sec", 0.0)
        if tok_s and float(tok_s) > 0:
            perf_line = (
                f"  Last Infer : {tok_s:.2f} tok/s "
                f"({m.get('eval_count', 0)} tokens, TTFT {m.get('ttft_s', 0.0):.2f}s)"
            )
            lines = report.splitlines()
            lines.insert(-1, perf_line)
            return "\n".join(lines)
        return report

    # ── Internal Helpers ──────────────────────────────────────────────

    @staticmethod
    def _ping_url(url: str, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _build_messages(self, user_message: str) -> list[dict[str, str]]:
        """
        Build message payload with conversation history.
        Automatically prepends `{"role": "system", "content": SYSTEM_PROMPT}` extracted
        from `Modelfile` whenever the model does not have Capri's Modelfile baked in.
        """
        with self._lock:
            history_copy = list(self._history)

        needs_system_prompt = bool(self.system_prompt) and not (
            self.backend == "ollama" and self.model_name.lower().startswith("capri:")
        )

        messages: list[dict[str, str]] = []
        if needs_system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history_copy)
        messages.append({"role": "user", "content": user_message})
        return messages

    def _stream(
        self,
        user_message: str,
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        # Ensure XDNA2 server is running if needed
        if self.backend == "xdna2" and not self.engine.is_server_running(timeout=1.0):
            if self.auto_start_server:
                self.engine.start_server(model=self.model_name, wait=True, timeout=18.0)
                self.model_name = self.engine.active_model

        messages = self._build_messages(user_message)

        if self.backend in ("openai", "lemonade"):
            self._stream_openai(user_message, messages, on_token, on_done, on_error)
        else:
            self._stream_flm_or_ollama(user_message, messages, on_token, on_done, on_error)

    def _stream_flm_or_ollama(
        self,
        user_message: str,
        messages: list[dict[str, str]],
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        url = f"{self.base_url}/api/chat"
        payload_dict: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "options": self.options,
            "stream": True,
        }
        payload = json.dumps(payload_dict).encode("utf-8")

        stream_filter = _StreamFilter(on_token)
        t_start = time.perf_counter()
        t_first_token: float | None = None
        chunk_count = 0
        final_obj: dict[str, Any] = {}

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=90) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = obj.get("message") or {}
                    # Ignore `msg.get("thinking")` from reasoning models; only read `content`
                    chunk = msg.get("content") or ""
                    if chunk:
                        if t_first_token is None:
                            t_first_token = time.perf_counter()
                        chunk_count += 1
                        stream_filter.feed(chunk)

                    if obj.get("done"):
                        final_obj = obj
                        break

            stream_filter.flush()
            full_reply = clean_reply_text(stream_filter.emitted_text)
            t_end = time.perf_counter()

            self._record_metrics(
                final_obj=final_obj,
                t_start=t_start,
                t_first_token=t_first_token,
                t_end=t_end,
                fallback_tokens=chunk_count,
            )

            with self._lock:
                self._history.extend(
                    [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": full_reply},
                    ]
                )

            on_done(full_reply)

        except urllib.error.URLError:
            if self.backend == "xdna2":
                on_error(
                    f"// XDNA2 NPU CONNECTION LOST — is `flm serve {self.model_name}` running on {self.base_url}?"
                )
            else:
                on_error("// CONNECTION LOST — is local inference server running?")
        except Exception as exc:
            on_error(f"// ERROR: {exc}")

    def _stream_openai(
        self,
        user_message: str,
        messages: list[dict[str, str]],
        on_token: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        endpoint = (
            f"{self.base_url}/chat/completions"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/chat/completions"
        )
        payload = json.dumps(
            {
                "model": self.model_name,
                "messages": messages,
                "temperature": self.modelfile.temperature,
                "stream": True,
            }
        ).encode("utf-8")

        stream_filter = _StreamFilter(on_token)
        t_start = time.perf_counter()
        t_first_token: float | None = None
        chunk_count = 0

        try:
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content") or ""
                    if chunk:
                        if t_first_token is None:
                            t_first_token = time.perf_counter()
                        chunk_count += 1
                        stream_filter.feed(chunk)

            stream_filter.flush()
            full_reply = clean_reply_text(stream_filter.emitted_text)
            t_end = time.perf_counter()

            self._record_metrics(
                final_obj={},
                t_start=t_start,
                t_first_token=t_first_token,
                t_end=t_end,
                fallback_tokens=chunk_count,
            )

            with self._lock:
                self._history.extend(
                    [
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": full_reply},
                    ]
                )

            on_done(full_reply)

        except urllib.error.URLError:
            on_error(f"// CONNECTION LOST — cannot reach {endpoint}")
        except Exception as exc:
            on_error(f"// ERROR: {exc}")

    def _record_metrics(
        self,
        final_obj: dict[str, Any],
        t_start: float,
        t_first_token: float | None,
        t_end: float,
        fallback_tokens: int,
    ) -> None:
        eval_count = int(final_obj.get("eval_count") or fallback_tokens)
        eval_duration_ns = int(final_obj.get("eval_duration") or 0)
        prompt_eval_count = int(final_obj.get("prompt_eval_count") or 0)
        prompt_eval_duration_ns = int(final_obj.get("prompt_eval_duration") or 0)
        total_duration_ns = int(final_obj.get("total_duration") or 0)

        wall_total_s = max(1e-6, t_end - t_start)
        ttft_s = (t_first_token - t_start) if t_first_token is not None else wall_total_s

        if eval_duration_ns > 0:
            eval_duration_s = eval_duration_ns / 1e9
        else:
            eval_duration_s = max(1e-6, t_end - (t_first_token or t_start))

        prompt_eval_duration_s = (
            prompt_eval_duration_ns / 1e9 if prompt_eval_duration_ns > 0 else ttft_s
        )
        total_duration_s = total_duration_ns / 1e9 if total_duration_ns > 0 else wall_total_s
        tok_per_sec = round(eval_count / eval_duration_s, 2) if eval_duration_s > 0 else 0.0

        metrics = {
            "backend": self.backend,
            "model": final_obj.get("model") or self.model_name,
            "tokens_per_sec": tok_per_sec,
            "tok_per_sec": tok_per_sec,
            "eval_count": eval_count,
            "eval_duration": eval_duration_ns,
            "eval_duration_ns": eval_duration_ns,
            "eval_duration_s": round(eval_duration_s, 4),
            "prompt_eval_count": prompt_eval_count,
            "prompt_eval_duration": prompt_eval_duration_ns,
            "prompt_eval_duration_ns": prompt_eval_duration_ns,
            "prompt_eval_duration_s": round(prompt_eval_duration_s, 4),
            "total_duration_s": round(total_duration_s, 4),
            "ttft_s": round(ttft_s, 4),
        }

        with self._lock:
            self.last_metrics = metrics


# Alias for full backwards compatibility with `from capri.ai.model_client import OllamaClient`
OllamaClient = XDNA2Client
