"""Prometheus metrics, collected in-process and exposed for scraping at /metrics.

Pull-based on purpose: Prometheus scrapes the endpoint, so nothing is pushed on
the request path. Recording a metric is an in-memory counter/histogram update
(microseconds), so this adds no meaningful latency to a query that spends
hundreds of milliseconds to seconds in OpenAI and Qdrant round trips.

Single default registry. That is correct for this deployment, which runs one
uvicorn worker; multiple workers would need prometheus_client's multiprocess
mode (a shared directory and a MultiProcessCollector) so each scrape aggregates
across processes.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# --- HTTP-level metrics (recorded by PrometheusMiddleware) ---
# `path` is the route *template* (e.g. /v1/documents/{document_id}), never the
# raw path, so a URL per document ID can't explode label cardinality.
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests handled.",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
)

# --- RAG-domain metrics (recorded when a query completes) ---
# Token spend is the metric most likely to become a surprise bill; it is read off
# responses the code already holds (see observability.record_token_usage).
rag_tokens_total = Counter(
    "rag_tokens_total",
    "OpenAI tokens consumed by query requests.",
    ["kind"],  # prompt | completion | embedding
)
rag_query_citations = Histogram(
    "rag_query_citations",
    "Citations returned per answered query.",
    buckets=(0, 1, 2, 4, 8, 16),
)


def observe_http(method: str, path: str, status: int, duration_seconds: float) -> None:
    """Record one completed HTTP request."""

    http_request_duration_seconds.labels(method, path).observe(duration_seconds)
    http_requests_total.labels(method, path, str(status)).inc()


def observe_query(citation_count: int, tokens: dict[str, int]) -> None:
    """Record the outcome of one answered query: citations and token spend."""

    rag_query_citations.observe(citation_count)
    for kind in ("prompt", "completion", "embedding"):
        rag_tokens_total.labels(kind).inc(tokens.get(kind, 0))


def render() -> tuple[bytes, str]:
    """Return the metrics exposition payload and its content type for /metrics."""

    return generate_latest(), CONTENT_TYPE_LATEST
