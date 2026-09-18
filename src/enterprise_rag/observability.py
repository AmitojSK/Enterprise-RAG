"""Request-scoped observability primitives: a correlation ID and token accounting.

Both are carried in ``ContextVar``s so no function in the request path needs an
extra parameter. A ``ContextVar`` is the right tool here even though the API's
endpoints are plain ``def`` running in FastAPI's threadpool: the context is
*copied* into the worker thread, so a value set by the ASGI middleware is visible
to the synchronous endpoint and to every service call it makes on that thread.

None of this touches query latency. Setting/reading a ``ContextVar`` and adding
to a dict are in-memory operations measured in nanoseconds, against a query that
spends hundreds of milliseconds to seconds in OpenAI and Qdrant round trips. The
token counts are read off responses the code already holds -- no extra calls.
"""

import logging
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

# The correlation ID for the current request. Defaults to "-" so a log line
# emitted outside any request (startup, a background thread) still formats.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Per-request token accumulator. ``None`` outside a request; a fresh dict is
# installed by the middleware at the start of each one.
_token_usage_var: ContextVar[dict[str, int] | None] = ContextVar("token_usage", default=None)


def new_request_id() -> str:
    """Generate a fresh correlation ID."""

    return str(uuid4())


def begin_token_accounting() -> None:
    """Install a fresh, zeroed token accumulator for the current request."""

    _token_usage_var.set({"prompt": 0, "completion": 0, "embedding": 0})


def record_token_usage(usage: Any, *, embedding: bool = False) -> None:
    """Add one OpenAI response's ``usage`` to the request total. No-op if absent.

    Safe to call from anywhere in the request: it silently does nothing when
    there is no accumulator (outside a request) or no usage on the response.
    """

    accumulator = _token_usage_var.get()
    if accumulator is None or usage is None:
        return
    if embedding:
        accumulator["embedding"] += getattr(usage, "total_tokens", 0) or 0
    else:
        accumulator["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
        accumulator["completion"] += getattr(usage, "completion_tokens", 0) or 0


def get_token_usage() -> dict[str, int]:
    """Return the current request's token totals (zeros if none were recorded)."""

    return _token_usage_var.get() or {"prompt": 0, "completion": 0, "embedding": 0}


class RequestIdLogFilter(logging.Filter):
    """Attach the current ``request_id`` to every log record.

    Installed on the root handler so ``%(request_id)s`` in the log format is
    always populated, which is what lets the service-layer logs in ``rag.py`` and
    ``vector_store.py`` be joined to the request that produced them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
