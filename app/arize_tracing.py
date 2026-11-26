"""Arize tracing integration for the SiamCart multi-agent framework.

Complements ``app.observability`` (structured logging) with OpenTelemetry /
OpenInference tracing. Non-invasive: importing this module is safe even when
OpenTelemetry is not installed — every helper degrades to a no-op.

Call ``init_tracing()`` once at startup (it only activates when
``ARIZE_SPACE_ID`` + ``ARIZE_API_KEY`` are both set), then use the
``@trace_span`` decorator on the agent entry points.

Environment variables:
  ARIZE_SPACE_ID      (required to enable)  full Base64 space id from Arize
  ARIZE_API_KEY       (required to enable)  Arize API key
  ARIZE_PROJECT_NAME  (optional)            project name, default "siamcart"
"""

import functools
import json
import os
from typing import Any, Callable, Optional

try:  # degrade gracefully if OpenTelemetry is not installed
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_AVAILABLE = False

_tracer: Any = None
_tracing_enabled: bool = False


def init_tracing():
    """Initialize Arize tracing. No-op unless both credentials are present."""
    global _tracer, _tracing_enabled

    space_id = os.getenv("ARIZE_SPACE_ID", "").strip()
    api_key = os.getenv("ARIZE_API_KEY", "").strip()

    if not _OTEL_AVAILABLE or not (space_id and api_key):
        _tracing_enabled = False
        _tracer = None
        return None

    try:
        from arize.otel import register
        project_name = os.getenv("ARIZE_PROJECT_NAME", "siamcart")
        register(space_id=space_id, api_key=api_key, project_name=project_name)
    except Exception:
        _tracing_enabled = False
        _tracer = None
        return None

    # Auto-instrument the openai SDK so LLM calls (DeepSeek) are captured as
    # LLM spans when DEEPSEEK_ENABLED=true.
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor
        OpenAIInstrumentor().instrument()
    except Exception:
        pass

    _tracing_enabled = True
    _tracer = trace.get_tracer("siamcart")
    return _tracer


def is_enabled() -> bool:
    return _tracing_enabled


def get_tracer():
    global _tracer
    if not _OTEL_AVAILABLE:
        return None
    if _tracer is None:
        _tracer = trace.get_tracer("siamcart")
    return _tracer


def _set_attrs(span, result: Any, keys) -> None:
    if not isinstance(result, dict):
        return
    for key in keys:
        value = result.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            span.set_attribute(f"app.{key}", value)


# OpenInference semantic convention keys (stable string constants).
_SPAN_KIND = "openinference.span.kind"
_INPUT_VALUE = "input.value"
_INPUT_MIME = "input.mime_type"
_OUTPUT_VALUE = "output.value"
_OUTPUT_MIME = "output.mime_type"


def _extract_output(result: Any, output_key: Optional[str], attr_keys) -> str:
    """Pull a display string for the span's output from a dict return."""
    if not isinstance(result, dict):
        return str(result) if result not in (None, "") else ""
    if output_key and output_key in result:
        value = result.get(output_key)
        return str(value) if value not in (None, "") else ""
    if attr_keys:
        summary = {k: result.get(k) for k in attr_keys if k in result}
        if summary:
            return json.dumps(summary, ensure_ascii=False, default=str)
    return ""


# Real LLM failures (config states like "disabled"/"no_key" are not errors).
_REAL_LLM_ERRORS = {
    "timeout", "http_error", "validation",
    "connection_error", "empty_output", "unknown",
}


def compute_eval_scores(result: Any) -> dict:
    """Compute quality eval scores from a ``process_message`` result dict.

    Emits OpenInference eval attributes (``eval.<metric>.score`` /
    ``eval.<metric>.label``) so Arize surfaces them as eval metrics that can
    be charted and monitored. Deterministic SiamCart responses are always
    grounded (they come from policy/order data), so groundedness is structural.
    """
    if not isinstance(result, dict):
        return {}
    scores: dict = {}

    response = str(result.get("response") or "").strip()
    has_answer = bool(response)
    scores["eval.answer_quality.score"] = 1.0 if has_answer else 0.0
    scores["eval.answer_quality.label"] = "pass" if has_answer else "fail"

    source = result.get("response_source", "")
    llm_error = result.get("llm_error_type")
    # deterministic answers are grounded by construction; DeepSeek-formatted
    # answers are grounded only when they passed factual validation.
    grounded = source == "deterministic" or (
        source == "deepseek" and not result.get("llm_fallback_used")
    )
    scores["eval.groundedness.score"] = 1.0 if grounded else 0.0
    scores["eval.groundedness.label"] = "grounded" if grounded else "ungrounded"

    error_free = llm_error not in _REAL_LLM_ERRORS
    scores["eval.error_free.score"] = 1.0 if error_free else 0.0
    scores["eval.error_free.label"] = "pass" if error_free else "fail"

    return scores


def trace_span(
    name: str,
    kind: str = "CHAIN",
    attr_keys: Optional[list] = None,
    output_key: Optional[str] = "response",
    with_evals: bool = False,
) -> Callable:
    """Wrap a function call in an OpenInference-aware OpenTelemetry span.

    Sets the OpenInference semantic fields so Arize renders the span correctly:
      - ``openinference.span.kind`` (CHAIN / AGENT / TOOL / RETRIEVER ...)
      - ``input.value``  (first positional str argument)
      - ``output.value`` (``output_key`` from the dict return, or a JSON
        summary of ``attr_keys``)
    Any scalar values named in ``attr_keys`` are also copied as ``app.*``.
    When ``with_evals`` is true, quality eval scores are attached to the span.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            if tracer is None or not _tracing_enabled:
                return func(*args, **kwargs)
            with tracer.start_as_current_span(name) as span:
                span.set_attribute(_SPAN_KIND, kind)
                if args and isinstance(args[0], str):
                    span.set_attribute(_INPUT_VALUE, args[0])
                    span.set_attribute(_INPUT_MIME, "text/plain")
                try:
                    result = func(*args, **kwargs)
                    output = _extract_output(result, output_key, attr_keys)
                    if output:
                        span.set_attribute(_OUTPUT_VALUE, output)
                        span.set_attribute(_OUTPUT_MIME, "text/plain")
                    if attr_keys:
                        _set_attrs(span, result, attr_keys)
                    if with_evals:
                        for key, value in compute_eval_scores(result).items():
                            span.set_attribute(key, value)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR))
                    span.record_exception(exc)
                    raise
        return wrapper
    return decorator
