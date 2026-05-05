"""Tests for GET /api/health per FE_CONTRACT §7.

The FE contract specifies:
  GET /api/health → 200 { status: "healthy"|string, database: "connected"|string, version: string }

The existing test_app.py tests /health (root, no prefix). This file tests the
/api/health variant which is required by the FE contract.
"""

from __future__ import annotations


async def test_api_health_returns_200(client):
    """GET /api/health must return 200 with the expected shape."""
    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert "version" in body
    assert isinstance(body["version"], str)


async def test_api_health_shape_matches_fe_contract(client):
    """Response must contain exactly the three fields the FE contract specifies."""
    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    # FE contract requires these three fields — no more, no less is strictly
    # enforced but all three must be present.
    assert set(body.keys()) >= {"status", "database", "version"}


async def test_root_health_still_works(client):
    """GET /health (no /api prefix) must still work for uptime monitors / k8s probes."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
