"""
Tests for Step 3 (login/logout/refresh endpoints) and Step 4 (CSRF enforcement).

Step 4 requirements:
- test_csrf_required_on_mutation: PATCH /api/repos/{host}/{owner}/{name} without X-CSRF-Token -> 403
- test_csrf_valid_token_accepted: same with correct header -> not 403
- test_login_sets_cookies: POST /api/auth/login with valid creds -> 200, all 3 cookies set

Step 5 (verification test):
- test_login_logout_refresh_cycle: full flow

Task 1 additions:
- test_login_rate_limited_by_email
- test_login_rate_limited_by_ip
- test_login_success_does_not_consume_email_budget

Task 2 additions:
- test_refresh_reuse_revokes_family
- test_logout_revokes_family_in_db
- test_refresh_with_unknown_family_fails
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, decode_token, hash_password
from backend.app.core.rate_limit import InMemoryRateLimiter
from backend.app.models.enums import UserRole
from backend.app.models.refresh_token_family import RefreshTokenFamily
from backend.app.models.user import User


async def _seed_admin(db_session, *, email: str = "admin@example.com", password: str = "supersecret99") -> User:
    """Create an admin user in the test DB and return it."""
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _set_access_cookie_with_csrf(client, settings, user: User, csrf: str = "test-csrf-token") -> None:
    """Set a valid access cookie on the test client with the given CSRF claim."""
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=csrf,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)


# ---------------------------------------------------------------------------
# CSRF enforcement tests (Step 4)
# ---------------------------------------------------------------------------


async def test_csrf_required_on_mutation(client, db_session, settings):
    """PATCH /api/repos/{host}/{owner}/{name} without X-CSRF-Token must return 403 CSRF_INVALID."""
    admin = await _seed_admin(db_session)
    await _set_access_cookie_with_csrf(client, settings, admin)

    response = await client.patch(
        "/api/repos/example.com/acme/demo",
        json={"sync_schedule": "manual"},
        # deliberately no X-CSRF-Token header
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_csrf_valid_token_accepted(client, db_session, settings):
    """PATCH /api/repos/{host}/{owner}/{name} with a matching X-CSRF-Token must NOT return 403."""
    admin = await _seed_admin(db_session)
    csrf = "my-valid-csrf"
    await _set_access_cookie_with_csrf(client, settings, admin, csrf=csrf)

    response = await client.patch(
        "/api/repos/example.com/acme/demo",
        json={"sync_schedule": "manual"},
        headers={"X-CSRF-Token": csrf},
    )

    # Should be 404 (repo doesn't exist) — anything but 403.
    assert response.status_code != 403


async def test_login_sets_cookies(client, db_session, settings):
    """POST /api/auth/login with valid credentials must return 200 and set all 3 cookies."""
    password = "correctpassword1"
    await _seed_admin(db_session, password=password)

    response = await client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": password},
    )

    assert response.status_code == 200
    body = response.json()
    assert "user" in body
    assert body["user"]["email"] == "admin@example.com"
    assert body["user"]["role"] == "admin"

    cookie_names = {c.name for c in response.cookies.jar}
    assert settings.auth.access_cookie_name in cookie_names
    assert settings.auth.refresh_cookie_name in cookie_names
    assert settings.auth.csrf_cookie_name in cookie_names


# ---------------------------------------------------------------------------
# Full login / /me / logout / refresh cycle (Step 5 verification test)
# ---------------------------------------------------------------------------


async def test_login_logout_refresh_cycle(client, db_session, settings):
    """Full auth cycle: login -> /me -> logout -> assert cookies cleared."""
    password = "longenoughpass42"
    await _seed_admin(db_session, email="cycle@example.com", password=password)

    # --- Login ---
    login_response = await client.post(
        "/api/auth/login",
        json={"email": "cycle@example.com", "password": password},
    )
    assert login_response.status_code == 200
    login_body = login_response.json()
    assert login_body["user"]["email"] == "cycle@example.com"

    # Verify all 3 cookies were set.
    cookie_names = {c.name for c in login_response.cookies.jar}
    assert settings.auth.access_cookie_name in cookie_names
    assert settings.auth.refresh_cookie_name in cookie_names
    assert settings.auth.csrf_cookie_name in cookie_names

    # httpx AsyncClient persists cookies from responses automatically.
    # --- /me with cookies ---
    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "cycle@example.com"

    # --- Logout ---
    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    # Logout must emit Set-Cookie for all three names with Max-Age=0 so the
    # browser evicts them. starlette's delete_cookie sets max-age=0 explicitly.
    set_cookie_headers = logout_response.headers.get_list("set-cookie")
    expected_names = {
        settings.auth.access_cookie_name,
        settings.auth.refresh_cookie_name,
        settings.auth.csrf_cookie_name,
    }
    cleared = {
        name
        for name in expected_names
        for header in set_cookie_headers
        if header.startswith(f"{name}=") and "max-age=0" in header.lower()
    }
    assert cleared == expected_names, f"not all cookies cleared: got {cleared}"

    # httpx's jar honours Max-Age=0 and evicts the cookies, so subsequent
    # requests from this client should behave as anonymous.
    me_after = await client.get("/api/auth/me")
    assert me_after.status_code == 401


async def test_login_invalid_credentials(client, db_session, settings):
    """POST /api/auth/login with wrong password must return 401 UNAUTHENTICATED."""
    await _seed_admin(db_session, password="correctpassword1")

    response = await client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_login_unknown_email(client):
    """POST /api/auth/login with unknown email must also return 401 (no user enumeration)."""
    response = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "anything"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_refresh_without_cookie_returns_401(client):
    """POST /api/auth/refresh with no refresh cookie must return 401 REFRESH_INVALID."""
    response = await client.post("/api/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_INVALID"


async def test_logout_is_idempotent(client):
    """POST /api/auth/logout called twice must both return 204."""
    r1 = await client.post("/api/auth/logout")
    r2 = await client.post("/api/auth/logout")
    assert r1.status_code == 204
    assert r2.status_code == 204


async def test_refresh_rotates_tokens(client, db_session, settings):
    """POST /api/auth/refresh returns 200 + {user} and rotates access + refresh JWTs."""
    password = "correctpassword1"
    await _seed_admin(db_session, email="rotate@example.com", password=password)

    login_response = await client.post(
        "/api/auth/login",
        json={"email": "rotate@example.com", "password": password},
    )
    assert login_response.status_code == 200

    original_access = client.cookies.get(settings.auth.access_cookie_name)
    original_refresh = client.cookies.get(settings.auth.refresh_cookie_name)
    assert original_access and original_refresh

    refresh_response = await client.post("/api/auth/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["user"]["email"] == "rotate@example.com"

    new_access = client.cookies.get(settings.auth.access_cookie_name)
    new_refresh = client.cookies.get(settings.auth.refresh_cookie_name)
    assert new_access and new_refresh

    # JWT `iat` is second-precision so the encoded strings may collide when the
    # test runs fast; identity of the rotated pair is proven by jti + csrf.
    original_access_claims = decode_token(
        original_access, settings=settings, expected_type=TokenType.ACCESS
    )
    new_access_claims = decode_token(
        new_access, settings=settings, expected_type=TokenType.ACCESS
    )
    assert new_access_claims.jti != original_access_claims.jti
    assert new_access_claims.csrf and new_access_claims.csrf != original_access_claims.csrf

    original_refresh_claims = decode_token(
        original_refresh, settings=settings, expected_type=TokenType.REFRESH
    )
    new_refresh_claims = decode_token(
        new_refresh, settings=settings, expected_type=TokenType.REFRESH
    )
    assert new_refresh_claims.jti != original_refresh_claims.jti
    # family should survive refresh-token rotation.
    assert new_refresh_claims.family == original_refresh_claims.family

    # The new csrf cookie must match the new access token's csrf claim.
    new_csrf_cookie = client.cookies.get(settings.auth.csrf_cookie_name)
    assert new_csrf_cookie == new_access_claims.csrf


async def test_csrf_mismatch_returns_403(client, db_session, settings):
    """Valid session but wrong CSRF header must return 403 CSRF_INVALID."""
    admin = await _seed_admin(db_session)
    await _set_access_cookie_with_csrf(client, settings, admin, csrf="expected")

    response = await client.patch(
        "/api/repos/example.com/acme/demo",
        json={"sync_schedule": "manual"},
        headers={"X-CSRF-Token": "wrong"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_unauthenticated_mutation_returns_401_not_403(client):
    """Mutation without any session must surface 401 (from auth), not 403 (CSRF)."""
    response = await client.patch(
        "/api/repos/example.com/acme/demo",
        json={"sync_schedule": "manual"},
        headers={"X-CSRF-Token": "anything"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# ---------------------------------------------------------------------------
# Task 1: Rate limiting tests
# ---------------------------------------------------------------------------


async def test_login_rate_limited_by_email(app, client, db_session, settings):
    """5 failed attempts per email window; 6th returns 429 with Retry-After.

    After the email is locked, even a correct-password attempt returns 429
    because the check happens before bcrypt.
    """
    password = "correctpass42!"
    await _seed_admin(db_session, email="rl-email@example.com", password=password)

    # Replace the shared rate limiter with a fresh isolated one for this test.
    app.state.rate_limiter = InMemoryRateLimiter()

    # 5 bad-password attempts — all should return 401.
    for i in range(5):
        r = await client.post(
            "/api/auth/login",
            json={"email": "rl-email@example.com", "password": "wrong"},
        )
        assert r.status_code == 401, f"attempt {i+1} expected 401, got {r.status_code}"

    # 6th bad attempt — should return 429.
    r6 = await client.post(
        "/api/auth/login",
        json={"email": "rl-email@example.com", "password": "wrong"},
    )
    assert r6.status_code == 429
    assert r6.json()["error"]["code"] == "RATE_LIMITED"
    assert "retry-after" in {h.lower() for h in r6.headers}

    # A correct-password attempt when the email is locked also returns 429
    # (check happens before bcrypt).
    r_correct = await client.post(
        "/api/auth/login",
        json={"email": "rl-email@example.com", "password": password},
    )
    assert r_correct.status_code == 429


async def test_login_rate_limited_by_ip(app, client, db_session, settings):
    """20 attempts from any email per IP window; 21st returns 429."""
    app.state.rate_limiter = InMemoryRateLimiter()

    # 20 attempts (any email, any password — IP limit counts all).
    for i in range(20):
        r = await client.post(
            "/api/auth/login",
            json={"email": f"user{i}@example.com", "password": "whatever"},
        )
        # Each returns 401 (wrong creds), not 429.
        assert r.status_code == 401, f"attempt {i+1} expected 401, got {r.status_code}"

    # 21st attempt — IP limit exceeded.
    r21 = await client.post(
        "/api/auth/login",
        json={"email": "overflow@example.com", "password": "whatever"},
    )
    assert r21.status_code == 429
    assert r21.json()["error"]["code"] == "RATE_LIMITED"
    assert "retry-after" in {h.lower() for h in r21.headers}


async def test_login_success_does_not_consume_email_budget(app, client, db_session, settings):
    """Successful logins must not count against the per-email failure budget.

    Verify by doing 4 successful logins + 1 failure and confirming the
    email counter is at 1 (only the failure counts), not 5.
    """
    password = "goodpassword42"
    await _seed_admin(db_session, email="budget@example.com", password=password)

    limiter = InMemoryRateLimiter()
    app.state.rate_limiter = limiter

    # 4 successful logins — should not consume email budget.
    for _ in range(4):
        r = await client.post(
            "/api/auth/login",
            json={"email": "budget@example.com", "password": password},
        )
        assert r.status_code == 200

    # 1 failure — should record 1 against the email budget.
    r_fail = await client.post(
        "/api/auth/login",
        json={"email": "budget@example.com", "password": "wrong"},
    )
    assert r_fail.status_code == 401

    # The email counter should show 4 remaining out of 5 (1 failure recorded).
    peek = await limiter.check(
        "rate:login:email:budget@example.com",
        window_seconds=900,
        limit=5,
    )
    assert peek.allowed
    assert peek.remaining == 4  # 5 limit - 1 failure = 4 remaining


# ---------------------------------------------------------------------------
# FE_CONTRACT: 429 body must include error.request_id
# ---------------------------------------------------------------------------


async def test_login_429_by_email_includes_request_id(app, client, db_session, settings):
    """429 responses from email rate-limit must include error.request_id per FE contract."""
    password = "pass12345x"
    await _seed_admin(db_session, email="rl-reqid@example.com", password=password)

    app.state.rate_limiter = InMemoryRateLimiter()

    # Exhaust the 5-failure email budget.
    for _ in range(5):
        await client.post(
            "/api/auth/login",
            json={"email": "rl-reqid@example.com", "password": "wrong"},
        )

    r429 = await client.post(
        "/api/auth/login",
        json={"email": "rl-reqid@example.com", "password": "wrong"},
    )

    assert r429.status_code == 429
    body = r429.json()
    assert "error" in body
    assert "request_id" in body["error"], "error.request_id must be present on 429 per FE contract"
    assert body["error"]["request_id"]  # non-empty string
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "retry-after" in {h.lower() for h in r429.headers}


async def test_login_429_by_ip_includes_request_id(app, client, settings):
    """429 responses from IP rate-limit must include error.request_id per FE contract."""
    app.state.rate_limiter = InMemoryRateLimiter()

    for i in range(20):
        await client.post(
            "/api/auth/login",
            json={"email": f"ip{i}@example.com", "password": "whatever"},
        )

    r429 = await client.post(
        "/api/auth/login",
        json={"email": "overflow2@example.com", "password": "whatever"},
    )

    assert r429.status_code == 429
    body = r429.json()
    assert "error" in body
    assert "request_id" in body["error"], "error.request_id must be present on 429 per FE contract"
    assert body["error"]["request_id"]  # non-empty string


# ---------------------------------------------------------------------------
# Task 2: Refresh family revocation tests
# ---------------------------------------------------------------------------


async def test_refresh_reuse_revokes_family(client, db_session, settings):
    """Replaying an already-rotated refresh token revokes the whole family.

    Steps:
    1. Login → cookie A
    2. Refresh → cookie B (rotates A)
    3. Put cookie A back and refresh → 401 REFRESH_INVALID (reuse detected)
    4. Try cookie B again → also 401 (family is now revoked)
    """
    password = "securepw12345"
    await _seed_admin(db_session, email="reuse@example.com", password=password)

    # Step 1: login.
    login_r = await client.post(
        "/api/auth/login",
        json={"email": "reuse@example.com", "password": password},
    )
    assert login_r.status_code == 200
    cookie_a = client.cookies.get(settings.auth.refresh_cookie_name)
    assert cookie_a

    # Step 2: refresh — rotates cookie A to cookie B.
    refresh1_r = await client.post("/api/auth/refresh")
    assert refresh1_r.status_code == 200
    cookie_b = client.cookies.get(settings.auth.refresh_cookie_name)
    assert cookie_b
    assert cookie_b != cookie_a

    # Step 3: replay cookie A — reuse detected, family revoked.
    client.cookies.set(settings.auth.refresh_cookie_name, cookie_a)
    reuse_r = await client.post("/api/auth/refresh")
    assert reuse_r.status_code == 401
    assert reuse_r.json()["error"]["code"] == "REFRESH_INVALID"

    # Step 4: try cookie B — family is revoked, must also fail.
    client.cookies.set(settings.auth.refresh_cookie_name, cookie_b)
    revoked_r = await client.post("/api/auth/refresh")
    assert revoked_r.status_code == 401
    assert revoked_r.json()["error"]["code"] == "REFRESH_INVALID"


async def test_logout_revokes_family_in_db(client, db_session, settings):
    """Logout must set revoked_at on the refresh family row."""
    password = "logouttest123"
    await _seed_admin(db_session, email="logout-family@example.com", password=password)

    login_r = await client.post(
        "/api/auth/login",
        json={"email": "logout-family@example.com", "password": password},
    )
    assert login_r.status_code == 200

    # Extract family UUID from the refresh token claims.
    refresh_cookie = client.cookies.get(settings.auth.refresh_cookie_name)
    assert refresh_cookie
    claims = decode_token(refresh_cookie, settings=settings, expected_type=TokenType.REFRESH)
    family_id = claims.family
    assert family_id

    # Logout.
    logout_r = await client.post("/api/auth/logout")
    assert logout_r.status_code == 204

    # Verify the family row is now revoked.
    result = await db_session.execute(
        select(RefreshTokenFamily).where(RefreshTokenFamily.family == family_id)
    )
    family_row = result.scalar_one_or_none()
    assert family_row is not None, "Family row must exist after login"
    assert family_row.revoked_at is not None, "revoked_at must be set after logout"


async def test_refresh_with_unknown_family_fails(client, db_session, settings):
    """A refresh token with a family UUID not in the DB returns 401."""
    await _seed_admin(db_session, email="unknown-family@example.com", password="pass12345!")

    login_r = await client.post(
        "/api/auth/login",
        json={"email": "unknown-family@example.com", "password": "pass12345!"},
    )
    assert login_r.status_code == 200

    from uuid import UUID as _UUID
    user_id = _UUID(login_r.json()["user"]["id"])

    # Forge a refresh token with a random family UUID that has no DB row.
    forged_token = create_token(
        user_id=user_id,
        role=UserRole.ADMIN,
        settings=settings,
        token_type=TokenType.REFRESH,
        family=uuid4(),
        jti=uuid4(),
    )

    client.cookies.set(settings.auth.refresh_cookie_name, forged_token)
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "REFRESH_INVALID"


async def test_refresh_parallel_race_only_one_wins(client, db_session, settings):
    """Two parallel refreshes with the same valid token: exactly one succeeds.

    Without compare-and-swap on current_jti, both requests would pass the
    equality check and both would issue new tokens, leaving the family in a
    broken state where only one of the two new jtis matches current_jti.
    The CAS UPDATE ensures the loser gets 401 REFRESH_INVALID.
    """
    import asyncio

    password = "parallelpass123"
    await _seed_admin(db_session, email="parallel@example.com", password=password)

    login_r = await client.post(
        "/api/auth/login",
        json={"email": "parallel@example.com", "password": password},
    )
    assert login_r.status_code == 200
    refresh_cookie = client.cookies.get(settings.auth.refresh_cookie_name)
    assert refresh_cookie

    async def call_refresh() -> int:
        # Each coroutine uses its own cookie jar so sessions don't share state;
        # both present the same original refresh cookie.
        import httpx
        transport = httpx.ASGITransport(app=client._transport.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            c.cookies.set(settings.auth.refresh_cookie_name, refresh_cookie)
            r = await c.post("/api/auth/refresh")
            return r.status_code

    results = await asyncio.gather(call_refresh(), call_refresh())
    successes = sum(1 for code in results if code == 200)
    failures = sum(1 for code in results if code == 401)
    assert successes == 1, f"expected exactly one winner, got {results}"
    assert failures == 1, f"expected exactly one loser (401), got {results}"
