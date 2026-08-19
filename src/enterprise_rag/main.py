"""FastAPI application factory and operational endpoints."""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from enterprise_rag.api.routes import router
from enterprise_rag.config import get_settings
from enterprise_rag.database import initialize_database

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Enterprise RAG API", version="0.2.0", description="Public, cited document intelligence API.")
# Angular's local development server uses port 4200. Keep this explicit rather
# than allowing arbitrary websites to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.on_event("startup")
def create_local_schema() -> None:
    """Initialize V2 document metadata before accepting API requests."""

    initialize_database()


@app.get("/healthz", tags=["Operations"])
def health_check() -> dict[str, str]:
    """Liveness probe: proves that the API process is accepting traffic."""

    return {"status": "ok"}
