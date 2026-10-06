from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from src.config import settings


def init_telemetry():
    """Initialize OpenTelemetry SDK. Console exporter by default, OTLP if endpoint set."""
    # Traces
    provider = TracerProvider()
    if settings.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
        )
    else:
        provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter())
        )
    trace.set_tracer_provider(provider)

    # Metrics
    if settings.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        metrics.set_meter_provider(
            MeterProvider(
                metric_readers=[
                    PeriodicExportingMetricReader(
                        OTLPMetricExporter(endpoint=settings.otel_endpoint)
                    )
                ]
            )
        )
    else:
        metrics.set_meter_provider(
            MeterProvider(
                metric_readers=[PeriodicExportingMetricReader(ConsoleMetricExporter())]
            )
        )


tracer = trace.get_tracer("model-router")
meter = metrics.get_meter("model-router")

# Metrics
request_duration = meter.create_histogram(
    name="router.request.duration",
    unit="ms",
    description="Request latency in milliseconds",
)
model_selections = meter.create_counter(
    name="router.model.selections",
    unit="1",
    description="Number of model selections by model ID",
)
rate_limit_events = meter.create_counter(
    name="router.rate_limits",
    unit="1",
    description="Number of rate limit (429) events",
)
inventory_refreshes = meter.create_counter(
    name="router.inventory.refreshes",
    unit="1",
    description="Number of inventory refresh attempts",
)
inventory_errors = meter.create_counter(
    name="router.inventory.errors",
    unit="1",
    description="Number of inventory refresh failures",
)
handoff_events = meter.create_counter(
    name="router.handoffs",
    unit="1",
    description="Number of model handoff events",
)
errors_total = meter.create_counter(
    name="router.errors.total",
    unit="1",
    description="Total error count",
)
