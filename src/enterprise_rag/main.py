"""FastAPI application factory and operational endpoints."""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from enterprise_rag.api.routes import router
from enterprise_rag.config import get_settings
from enterprise_rag.database import initialize_database
from enterprise_rag.observability import (
    RequestIdLogFilter,
    begin_token_accounting,
    new_request_id,
    request_id_var,
)

settings = get_settings()
# `[%(request_id)s]` is populated by RequestIdLogFilter for every record, so the
# service-layer logs carry the same correlation ID the client and audit line see.
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdLogFilter())


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
# Added last so it is the outermost middleware: the correlation ID is set before
# anything else (CORS, routing) runs, and stays set for the whole response.
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
