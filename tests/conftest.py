"""Shared test setup."""

import pytest
from enterprise_rag.database import initialize_database


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Create the tables the API expects.

    ``TestClient(app)`` is constructed at module import rather than used as a
    context manager, so FastAPI's startup event — which normally initializes the
    schema — never fires during the test run.
    """

    initialize_database()
