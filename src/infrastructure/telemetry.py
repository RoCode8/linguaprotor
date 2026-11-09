"""OpenTelemetry setup and LLM-call tracing for LinguaProctor.

Provides:
- One-time telemetry bootstrap (:func:`setup_telemetry`)
- Convenient accessors for a shared :class:`Tracer` and :class:`Meter`
- A :func:`trace_llm_call` decorator / context-manager that automatically
  records model name, token counts, latency, and estimated cost as span
  attributes and metric data points.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, StatusCode, Tracer

from src.config import get_settings

logger = logging.getLogger(__name__)

# ── Module-level singletons ───────────────────────────────────────────

_tracer: Tracer | None = None
_meter: Meter | None = None
_initialized: bool = False


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class LLMCallMetrics:
    """Captured metrics for a single LLM API call."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    estimated_cost_usd: float = 0.0
    error: str | None = None


# ── Setup ─────────────────────────────────────────────────────────────


def setup_telemetry(service_name: str | None = None) -> None:
    """Initialise OpenTelemetry tracing and metrics for the application.

    Safe to call multiple times — subsequent calls are no-ops.

    This function configures:
    - A :class:`TracerProvider` with a :class:`BatchSpanProcessor`
    - A :class:`MeterProvider` with a :class:`PeriodicExportingMetricReader`
    - Azure Application Insights exporter (if the SDK is available and
      an instrumentation key / connection string is configured)

    Args:
        service_name: Logical service name embedded in all telemetry.
            Defaults to ``Settings.app.name``.
    """
    global _tracer, _meter, _initialized

    if _initialized:
        return

    settings = get_settings().app
    resolved_name = service_name or settings.name

    resource = Resource.create(
        {
            "service.name": resolved_name,
            "service.version": settings.version,
            "deployment.environment": settings.environment,
        }
    )

    # ── Tracing ────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)

    # Try to attach Azure Monitor exporter
    _attach_azure_monitor_exporters(tracer_provider, resource)

    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer(resolved_name, settings.version)

    # ── Metrics ────────────────────────────────────────────────────
    metric_readers = _build_metric_readers()
    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter(resolved_name, settings.version)

    _initialized = True
    logger.info(
        "OpenTelemetry initialised — service=%s env=%s",
        resolved_name,
        settings.environment,
    )


def _attach_azure_monitor_exporters(
    tracer_provider: TracerProvider,
    resource: Resource,
) -> None:
    """Best-effort attachment of Azure Monitor / App Insights exporters."""
    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

        exporter = AzureMonitorTraceExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("Attached Azure Monitor trace exporter")
    except Exception:
        # Fallback: console exporter for local development
        try:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.debug("Azure Monitor exporter unavailable; using ConsoleSpanExporter")
        except Exception:
            logger.debug("No span exporter configured")


def _build_metric_readers() -> list[PeriodicExportingMetricReader]:
    """Build metric readers, preferring Azure Monitor if available."""
    readers: list[PeriodicExportingMetricReader] = []
    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorMetricExporter

        reader = PeriodicExportingMetricReader(
            AzureMonitorMetricExporter(),
            export_interval_millis=60_000,
        )
        readers.append(reader)
        logger.info("Attached Azure Monitor metric exporter")
    except Exception:
        try:
            from opentelemetry.sdk.metrics.export import (
                ConsoleMetricExporter,
            )

            reader = PeriodicExportingMetricReader(
                ConsoleMetricExporter(),
                export_interval_millis=60_000,
            )
            readers.append(reader)
            logger.debug("Azure Monitor metric exporter unavailable; using ConsoleMetricExporter")
        except Exception:
            logger.debug("No metric reader configured")
    return readers


# ── Accessors ─────────────────────────────────────────────────────────


def get_tracer() -> Tracer:
    """Return the application :class:`Tracer`, initialising if needed.

    Returns:
        The shared OpenTelemetry tracer.
    """
    global _tracer
    if _tracer is None:
        setup_telemetry()
    assert _tracer is not None
    return _tracer


def get_meter() -> Meter:
    """Return the application :class:`Meter`, initialising if needed.

    Returns:
        The shared OpenTelemetry meter.
    """
    global _meter
    if _meter is None:
        setup_telemetry()
    assert _meter is not None
    return _meter


# ── LLM-call tracing ─────────────────────────────────────────────────

# Pre-defined metric instruments (lazily created)
_llm_latency_histogram: Any = None
_llm_token_counter: Any = None
_llm_cost_counter: Any = None


def _ensure_instruments() -> None:
    """Create metric instruments once."""
    global _llm_latency_histogram, _llm_token_counter, _llm_cost_counter
    if _llm_latency_histogram is not None:
        return
    meter = get_meter()
    _llm_latency_histogram = meter.create_histogram(
        name="llm.call.latency",
        description="Latency of LLM API calls in seconds",
        unit="s",
    )
    _llm_token_counter = meter.create_counter(
        name="llm.tokens.total",
        description="Cumulative token usage across LLM calls",
        unit="tokens",
    )
    _llm_cost_counter = meter.create_counter(
        name="llm.cost.usd",
        description="Cumulative estimated cost of LLM calls",
        unit="USD",
    )


def _record_metrics(m: LLMCallMetrics, span: Span) -> None:
    """Write metrics to the span attributes and metric instruments."""
    _ensure_instruments()

    attrs = {"model": m.model}

    span.set_attribute("llm.model", m.model)
    span.set_attribute("llm.prompt_tokens", m.prompt_tokens)
    span.set_attribute("llm.completion_tokens", m.completion_tokens)
    span.set_attribute("llm.total_tokens", m.total_tokens)
    span.set_attribute("llm.latency_seconds", m.latency_seconds)
    span.set_attribute("llm.estimated_cost_usd", m.estimated_cost_usd)

    if m.error:
        span.set_attribute("llm.error", m.error)
        span.set_status(StatusCode.ERROR, m.error)
    else:
        span.set_status(StatusCode.OK)

    _llm_latency_histogram.record(m.latency_seconds, attributes=attrs)
    _llm_token_counter.add(m.total_tokens, attributes=attrs)
    _llm_cost_counter.add(m.estimated_cost_usd, attributes=attrs)


@contextmanager
def trace_llm_call(
    operation_name: str = "llm_call",
    *,
    model: str = "",
) -> Generator[LLMCallMetrics, None, None]:
    """Context manager that traces an LLM API call.

    Automatically captures latency. The caller should populate token
    counts and cost on the yielded :class:`LLMCallMetrics` object before
    the block exits.

    Usage::

        with trace_llm_call("translate", model="gpt-4o") as m:
            response = await client.chat_completion(...)
            m.prompt_tokens = response["usage"].prompt_tokens
            m.completion_tokens = response["usage"].completion_tokens
            m.estimated_cost_usd = response["usage"].estimated_cost_usd

    Args:
        operation_name: Human-readable span name.
        model: The LLM model / deployment name.

    Yields:
        An :class:`LLMCallMetrics` instance to be populated by the caller.
    """
    tracer = get_tracer()
    call_metrics = LLMCallMetrics(model=model)

    with tracer.start_as_current_span(operation_name) as span:
        start = time.perf_counter()
        try:
            yield call_metrics
        except Exception as exc:
            call_metrics.error = str(exc)
            raise
        finally:
            call_metrics.latency_seconds = time.perf_counter() - start
            call_metrics.total_tokens = (
                call_metrics.prompt_tokens + call_metrics.completion_tokens
            )
            _record_metrics(call_metrics, span)


def trace_llm(
    operation_name: str = "llm_call",
    *,
    model: str = "",
) -> Callable:
    """Decorator variant of :func:`trace_llm_call`.

    Wraps an async function so that each invocation is traced. The
    decorated function receives an extra ``llm_metrics`` keyword
    argument (an :class:`LLMCallMetrics` instance) that it can populate.

    Usage::

        @trace_llm("generate_feedback", model="gpt-4o")
        async def generate_feedback(prompt: str, *, llm_metrics=None):
            ...
            if llm_metrics:
                llm_metrics.prompt_tokens = usage.prompt_tokens
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_llm_call(operation_name, model=model) as m:
                kwargs["llm_metrics"] = m
                return await fn(*args, **kwargs)

        return wrapper

    return decorator
