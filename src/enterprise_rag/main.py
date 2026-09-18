"""FastAPI application factory and operational endpoints."""

import logging
import time
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from enterprise_rag import metrics
from enterprise_rag.api.routes import router
from enterprise_rag.config import get_settings
from enterprise_rag.database import initialize_database
from enterprise_rag.observability import (
    JsonLogFormatter,
    RequestIdLogFilter,
    begin_token_accounting,
    new_request_id,
    request_id_var,
)

settings = get_settings()
# One root handler carrying the correlation ID (via RequestIdLogFilter) and one of
# two formats: human-readable text for local dev, or one-JSON-object-per-line for
# aggregators when LOG_FORMAT=json. The filter runs before formatting, so
# `[%(request_id)s]` / the JSON `request_id` field is always populated.
_log_handler = logging.StreamHandler()
if settings.log_format.lower() == "json":
    _log_handler.setFormatter(JsonLogFormatter())
else:
    _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
_log_handler.addFilter(RequestIdLogFilter())
logging.basicConfig(level=settings.log_level, handlers=[_log_handler])


class CorrelationIdMiddleware:
    """Give every request a correlation ID and a fresh token accumulator.

    A pure-ASGI middleware (not ``@app.middleware("http")``) so the ``ContextVar``
    is set in the same task that runs the endpoint, guaranteeing it propagates to
    the endpoint and its downstream service calls. An inbound ``X-Request-ID`` is
    honoured so a correlation ID from an upstream proxy is preserved; the chosen
    ID is echoed back in the response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode() for k, v in scope["headers"] if k == b"x-request-id"), None)
        request_id = incoming or new_request_id()
        token = request_id_var.set(request_id)
        begin_token_accounting()

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


class PrometheusMiddleware:
    """Time every HTTP request and record it for the /metrics endpoint.

    Pure-ASGI so it can read the matched route template from the scope after
    routing. Recording is in-memory, so it adds no request latency. The /metrics
    scrape is skipped so it doesn't count itself.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # The route template (e.g. /v1/documents/{document_id}) keeps label
            # cardinality bounded; unmatched paths (bots, 404s) collapse to one.
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            metrics.observe_http(
                scope.get("method", "UNKNOWN"), path, status["code"], time.perf_counter() - start
            )

app = FastAPI(title="Enterprise RAG API", version="0.2.0", description="Public, cited document intelligence API.")
# Keep the allowed origins explicit rather than allowing arbitrary websites to
# call the API. ALLOWED_ORIGINS defaults to Angular's local development server
# and is overridden with the deployed frontend's URL in hosted environments.
allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
    # Let the browser read the correlation ID off the response, since the API is
    # a different origin from the static frontend.
    expose_headers=["X-Request-ID"],
)
app.add_middleware(PrometheusMiddleware)
# Added last so it is the outermost middleware: the correlation ID is set before
# anything else (CORS, routing, metrics timing) runs, and stays set for the whole
# response.
app.add_middleware(CorrelationIdMiddleware)
app.include_router(router)


@app.on_event("startup")
def create_local_schema() -> None:
    """Initialize V2 document metadata before accepting API requests."""

    initialize_database()


@app.get("/healthz", tags=["Operations"])
def health_check() -> dict[str, str]:
    """Liveness probe: proves that the API process is accepting traffic."""

    return {"status": "ok"}


@app.get("/metrics", tags=["Operations"])
def prometheus_metrics() -> Response:
    """Expose Prometheus metrics for scraping.

    Unauthenticated, like the rest of this demo API. In a real deployment this
    would be bound to an internal network or protected, since it exposes request
    counts and token totals.
    """

    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)
