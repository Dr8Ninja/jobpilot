"""The shape of the API itself: versioning, errors, CORS, logging.

None of this changes what the endpoints *do*. The point is that the same
behaviour survives being deployed somewhere other than this laptop — and that
the dashboard, which is written against the unprefixed paths, keeps working
while it does.
"""

import pytest
from fastapi.testclient import TestClient
from jobpilot_api.main import app, get_db
from jobpilot_shared.settings import get_settings


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_old_unprefixed_paths_still_route(client) -> None:
    """The dashboard calls these. Breaking them is breaking the product."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/queue").status_code == 200
    assert client.get("/api/queue/counts").status_code == 200


def test_the_same_endpoints_answer_under_api_v1(client) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/queue").status_code == 200
    assert client.get("/api/v1/queue/counts").status_code == 200


def test_both_prefixes_return_the_same_body(client) -> None:
    assert client.get("/api/queue").json() == client.get("/api/v1/queue").json()


def test_only_the_versioned_routes_are_documented(client) -> None:
    """One entry per operation in the schema; the legacy mount is a compatibility
    shim, not a second public API."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/queue" in paths
    assert "/api/queue" not in paths


def test_an_error_still_carries_detail_where_the_dashboard_reads_it(client) -> None:
    """`lib/api.ts` and `Actions.tsx` both read `body.detail`. It stays."""
    response = client.get("/api/queue/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Application not found"


def test_an_error_also_carries_a_structured_object(client) -> None:
    response = client.get("/api/queue/999999")
    error = response.json()["error"]

    assert error["status"] == 404
    assert error["code"] == "not_found"
    assert error["message"] == "Application not found"
    assert error["request_id"]


def test_a_validation_error_is_shaped_the_same_way(client) -> None:
    response = client.get("/api/skill-gaps?min_jobs=not-a-number")

    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], str)
    assert body["error"]["code"] == "validation_error"


def test_every_response_carries_a_request_id(client) -> None:
    response = client.get("/api/health")

    assert response.headers["x-request-id"]


def test_cors_origins_come_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("JOBPILOT_CORS_ORIGINS", "https://jobpilot.example,https://other.example")
    assert get_settings(refresh=True).cors_origins_list() == [
        "https://jobpilot.example",
        "https://other.example",
    ]


def test_cors_defaults_to_the_local_dashboard() -> None:
    assert "http://localhost:3000" in get_settings().cors_origins_list()


def test_the_request_logger_can_actually_emit(client) -> None:
    """Request logging that goes nowhere is not request logging.

    Uvicorn configures its own `uvicorn.*` loggers and leaves the root alone, so
    without explicit setup a `jobpilot.api.access` INFO record falls through to
    `logging.lastResort`, which drops anything below WARNING. Every line this
    middleware writes was silently discarded.
    """
    import logging

    logger = logging.getLogger("jobpilot.api.access")

    assert logger.isEnabledFor(logging.INFO)
    reachable = logger
    while reachable is not None:
        if reachable.handlers:
            break
        reachable = reachable.parent
    assert reachable is not None, "no handler anywhere on the jobpilot.api.access chain"
