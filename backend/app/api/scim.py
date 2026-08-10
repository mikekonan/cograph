"""SCIM 2.0 provisioning endpoints (Phase 30.4).

Mounted at `/scim/v2/` (NOT under `/api/`). Bearer-only via
`resolve_scim_client`; CSRF is bypassed entirely. All responses use
content-type `application/scim+json` per RFC 7644 §3.1, and errors
follow the `{schemas:[…:Error], status:..., scimType:..., detail:...}`
envelope.

Supported subset:

| Method | Path                          | Notes                          |
|--------|-------------------------------|--------------------------------|
| POST   | /Users                        | Provision                      |
| GET    | /Users                        | filter `userName|externalId eq`|
| GET    | /Users/{id}                   | Read                           |
| PUT    | /Users/{id}                   | Replace name / email / active  |
| PATCH  | /Users/{id}                   | RFC 7644 §3.5.2 patch ops      |
| DELETE | /Users/{id}                   | active=false (soft-deprovision)|
| GET    | /ServiceProviderConfig        | Static                         |
| GET    | /ResourceTypes                | Static                         |
| GET    | /Schemas                      | Static                         |
| ANY    | /Groups[/...]                 | 501 notImplemented             |
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.scim_resolver import resolve_scim_client
from backend.app.core.deps import get_db_session
from backend.app.models.scim_client import SCIMClient
from backend.app.models.scim_event import SCIMEvent
from backend.app.models.user import User
from backend.app.models.user_identity import UserIdentity
from backend.app.scim.cascade import (
    SCIMLastAdminProtectedError,
    disable_user_cascade,
    enable_user,
)
from backend.app.scim.payload import build_idempotency_key, canonical_payload_hash
from backend.app.scim.resource import (
    list_response,
    scim_error,
    user_to_scim,
)
from backend.app.scim.service_provider import (
    resource_types,
    schemas,
    service_provider_config,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["scim"])

SCIM_MEDIA_TYPE = "application/scim+json"


# ---------------------------------------------------------------------------
# Bearer auth dependency
# ---------------------------------------------------------------------------


async def _require_scim_client(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> SCIMClient:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _scim_http(401, "invalidCredentials", "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise _scim_http(401, "invalidCredentials", "Empty bearer token")
    client_ip = request.client.host if request.client else None
    scim_client = await resolve_scim_client(token, session=session, client_ip=client_ip)
    if scim_client is None:
        raise _scim_http(401, "invalidCredentials", "Bearer token rejected")
    return scim_client


def _scim_http(status_code: int, scim_type: str | None, detail: str) -> _SCIMHTTPException:
    return _SCIMHTTPException(status_code, scim_error(status_code=status_code, scim_type=scim_type, detail=detail))


class _SCIMHTTPException(Exception):
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("detail"))
        self.status_code = status_code
        self.body = body


def _scim_response(body: dict[str, Any], *, status_code: int = 200) -> Response:
    import json as _json

    return Response(
        content=_json.dumps(body),
        status_code=status_code,
        media_type=SCIM_MEDIA_TYPE,
    )


# ---------------------------------------------------------------------------
# Idempotency helper
# ---------------------------------------------------------------------------


async def _record_event(
    session: AsyncSession,
    *,
    client: SCIMClient,
    operation: str,
    external_id: str | None,
    target_user_id: UUID | None,
    payload_hash: bytes,
    status: str,
    error_code: str | None = None,
) -> bool:
    """Insert one `scim_events` row. Returns True on insert, False on dedupe."""

    key = build_idempotency_key(
        provider_id=client.provider_id,
        external_id=external_id,
        operation=operation,
        payload_hash=payload_hash,
    )
    row = SCIMEvent(
        client_id=client.id,
        provider_id=client.provider_id,
        operation=operation,
        external_id=external_id,
        target_user_id=target_user_id,
        payload_hash=payload_hash,
        idempotency_key=key,
        status=status,
        error_code=error_code,
    )
    session.add(row)
    try:
        await session.flush()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def _existing_event(session: AsyncSession, key: str) -> SCIMEvent | None:
    return (
        await session.execute(select(SCIMEvent).where(SCIMEvent.idempotency_key == key))
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Service provider metadata
# ---------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def get_service_provider_config(
    _: SCIMClient = Depends(_require_scim_client),
) -> Response:
    return _scim_response(service_provider_config())


@router.get("/ResourceTypes")
async def get_resource_types(_: SCIMClient = Depends(_require_scim_client)) -> Response:
    return _scim_response(resource_types())


@router.get("/Schemas")
async def get_schemas(_: SCIMClient = Depends(_require_scim_client)) -> Response:
    return _scim_response(schemas())


# ---------------------------------------------------------------------------
# Group endpoints — explicitly not implemented
# ---------------------------------------------------------------------------


@router.api_route(
    "/Groups",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def groups_root_not_implemented(
    _: SCIMClient = Depends(_require_scim_client),
) -> Response:
    return _scim_response(
        scim_error(
            status_code=501,
            scim_type="notImplemented",
            detail="Cograph SCIM does not implement /Groups",
        ),
        status_code=501,
    )


@router.api_route(
    "/Groups/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def groups_subpath_not_implemented(
    rest: str,
    _: SCIMClient = Depends(_require_scim_client),
) -> Response:
    del rest
    return _scim_response(
        scim_error(
            status_code=501,
            scim_type="notImplemented",
            detail="Cograph SCIM does not implement /Groups",
        ),
        status_code=501,
    )


# ---------------------------------------------------------------------------
# /Users — read paths
# ---------------------------------------------------------------------------


@router.get("/Users")
async def list_users(
    request: Request,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    raw_filter = request.query_params.get("filter")
    if raw_filter is None:
        raise _scim_http(
            400,
            "tooMany",
            "Cograph SCIM requires a filter (userName eq or externalId eq)",
        )
    field, value = _parse_eq_filter(raw_filter)
    if field == "userName":
        user = (
            await session.execute(select(User).where(User.email == value))
        ).scalar_one_or_none()
        if user is None:
            return _scim_response(list_response([], total=0))
        external = await _external_id_for(session, user, client)
        return _scim_response(list_response([user_to_scim(user, external_id=external)]))
    if field == "externalId":
        identity = (
            await session.execute(
                select(UserIdentity).where(
                    UserIdentity.provider_id == client.provider_id,
                    UserIdentity.subject == value,
                )
            )
        ).scalar_one_or_none()
        if identity is None:
            return _scim_response(list_response([], total=0))
        user = await session.get(User, identity.user_id)
        if user is None:
            return _scim_response(list_response([], total=0))
        return _scim_response(list_response([user_to_scim(user, external_id=value)]))
    raise _scim_http(400, "invalidFilter", f"Unsupported filter: {raw_filter}")


@router.get("/Users/{user_id}")
async def get_user(
    user_id: UUID,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    user = await session.get(User, user_id)
    if user is None:
        raise _scim_http(404, None, f"User {user_id} not found")
    external = await _external_id_for(session, user, client)
    return _scim_response(user_to_scim(user, external_id=external))


# ---------------------------------------------------------------------------
# /Users — write paths
# ---------------------------------------------------------------------------


@router.post("/Users")
async def create_user(
    request: Request,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    payload = await _read_json(request)
    payload_hash = canonical_payload_hash(payload)
    external_id = payload.get("externalId") if isinstance(payload, dict) else None
    key = build_idempotency_key(
        provider_id=client.provider_id,
        external_id=external_id,
        operation="create",
        payload_hash=payload_hash,
    )
    cached = await _existing_event(session, key)
    if cached is not None and cached.target_user_id is not None:
        existing = await session.get(User, cached.target_user_id)
        if existing is not None:
            return _scim_response(
                user_to_scim(existing, external_id=external_id),
                status_code=200,
            )

    if not isinstance(payload, dict):
        raise _scim_http(400, "invalidSyntax", "Request body must be a SCIM User")
    email = _user_email(payload)
    if not email:
        raise _scim_http(400, "invalidValue", "userName / emails[primary] is required")

    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        # Treat as no-op create — return existing resource (idempotent
        # path for re-imported IdP rosters).
        await _record_event(
            session,
            client=client,
            operation="create",
            external_id=external_id,
            target_user_id=existing.id,
            payload_hash=payload_hash,
            status="no_op",
        )
        await session.commit()
        return _scim_response(user_to_scim(existing, external_id=external_id))

    user = User(
        email=email,
        password_hash=None,
        name=_full_name(payload),
        auth_source="oidc",
        is_active=bool(payload.get("active", True)),
    )
    session.add(user)
    await session.flush()

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=None,
            target_user_id=user.id,
            event_type="scim_user_provisioned",
            metadata={
                "client_id": str(client.id),
                "external_id": external_id,
                "email": email,
            },
        ),
    )
    inserted = await _record_event(
        session,
        client=client,
        operation="create",
        external_id=external_id,
        target_user_id=user.id,
        payload_hash=payload_hash,
        status="applied",
    )
    if not inserted:
        # Race: a peer SCIM event with the same key landed between our
        # cached lookup and the insert. Roll back our user creation and
        # return the cached record.
        await session.rollback()
        return await get_user(user_id=user.id, client=client, session=session)

    await session.commit()
    return _scim_response(user_to_scim(user, external_id=external_id), status_code=201)


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: UUID,
    request: Request,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    user = await session.get(User, user_id)
    if user is None:
        raise _scim_http(404, None, f"User {user_id} not found")
    payload = await _read_json(request)
    if not isinstance(payload, dict):
        raise _scim_http(400, "invalidSyntax", "Request body must be a SCIM User")

    payload_hash = canonical_payload_hash(payload)
    external_id = payload.get("externalId")
    return await _apply_replace(
        client=client,
        session=session,
        user=user,
        payload=payload,
        payload_hash=payload_hash,
        external_id=external_id if isinstance(external_id, str) else None,
    )


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: UUID,
    request: Request,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    user = await session.get(User, user_id)
    if user is None:
        raise _scim_http(404, None, f"User {user_id} not found")
    payload = await _read_json(request)
    if not isinstance(payload, dict) or "Operations" not in payload:
        raise _scim_http(400, "invalidSyntax", "PATCH body must include Operations")

    payload_hash = canonical_payload_hash(payload)
    external_id = await _external_id_for(session, user, client)

    key = build_idempotency_key(
        provider_id=client.provider_id,
        external_id=external_id,
        operation="patch",
        payload_hash=payload_hash,
    )
    cached = await _existing_event(session, key)
    if cached is not None:
        await session.refresh(user)
        return _scim_response(user_to_scim(user, external_id=external_id))

    new_active: bool | None = None
    new_name: str | None = None
    new_email: str | None = None
    for op in payload.get("Operations", []):
        if not isinstance(op, dict):
            continue
        path = (op.get("path") or "").lower()
        value = op.get("value")
        verb = (op.get("op") or "").lower()
        if verb not in {"add", "replace", "remove"}:
            raise _scim_http(400, "invalidSyntax", f"Unsupported op '{verb}'")
        if path == "active":
            if verb == "remove":
                new_active = False
            elif isinstance(value, bool):
                new_active = value
            elif isinstance(value, str):
                new_active = value.lower() == "true"
            else:
                raise _scim_http(400, "invalidValue", "active must be boolean")
        elif path == "username" or path == "emails[primary].value":
            if isinstance(value, str):
                new_email = value
            elif isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict) and isinstance(first.get("value"), str):
                    new_email = first["value"]
        elif path == "name.givenname" or path == "name.familyname" or path == "name":
            if isinstance(value, dict):
                given = value.get("givenName") or ""
                family = value.get("familyName") or ""
                merged = f"{given} {family}".strip()
                if merged:
                    new_name = merged
            elif isinstance(value, str):
                new_name = value
        # Unknown paths are silently ignored — Okta sends fields we don't
        # mirror (e.g. enterpriseUser extensions). Logging would just be
        # noise in steady state.

    return await _apply_mutation(
        client=client,
        session=session,
        user=user,
        operation="patch",
        payload_hash=payload_hash,
        external_id=external_id,
        new_active=new_active,
        new_name=new_name,
        new_email=new_email,
    )


@router.delete("/Users/{user_id}")
async def delete_user(
    user_id: UUID,
    client: SCIMClient = Depends(_require_scim_client),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    user = await session.get(User, user_id)
    if user is None:
        raise _scim_http(404, None, f"User {user_id} not found")
    external_id = await _external_id_for(session, user, client)
    payload_hash = canonical_payload_hash({"active": False})
    response = await _apply_mutation(
        client=client,
        session=session,
        user=user,
        operation="delete",
        payload_hash=payload_hash,
        external_id=external_id,
        new_active=False,
    )
    if response.status_code in (200, 204):
        # SCIM DELETE returns 204 No Content per RFC 7644 §3.6 even
        # though we soft-deprovision; clients ignore the body.
        return Response(status_code=204)
    return response


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


async def _apply_replace(
    *,
    client: SCIMClient,
    session: AsyncSession,
    user: User,
    payload: dict[str, Any],
    payload_hash: bytes,
    external_id: str | None,
) -> Response:
    new_email = _user_email(payload)
    new_name = _full_name(payload)
    raw_active = payload.get("active")
    if raw_active is None:
        new_active: bool | None = None
    elif isinstance(raw_active, bool):
        new_active = raw_active
    elif isinstance(raw_active, str):
        new_active = raw_active.lower() == "true"
    else:
        raise _scim_http(400, "invalidValue", "active must be boolean")

    return await _apply_mutation(
        client=client,
        session=session,
        user=user,
        operation="replace",
        payload_hash=payload_hash,
        external_id=external_id,
        new_active=new_active,
        new_email=new_email,
        new_name=new_name,
    )


async def _apply_mutation(
    *,
    client: SCIMClient,
    session: AsyncSession,
    user: User,
    operation: str,
    payload_hash: bytes,
    external_id: str | None,
    new_active: bool | None = None,
    new_email: str | None = None,
    new_name: str | None = None,
) -> Response:
    key = build_idempotency_key(
        provider_id=client.provider_id,
        external_id=external_id,
        operation=operation,
        payload_hash=payload_hash,
    )
    cached = await _existing_event(session, key)
    if cached is not None:
        await session.refresh(user)
        return _scim_response(user_to_scim(user, external_id=external_id))

    changed = False
    if new_email is not None and new_email != user.email:
        user.email = new_email
        changed = True
    if new_name is not None and new_name != (user.name or ""):
        user.name = new_name
        changed = True

    if new_active is False:
        try:
            await disable_user_cascade(
                target=user,
                actor_client=client,
                external_id=external_id,
                session=session,
            )
        except SCIMLastAdminProtectedError:
            await _record_event(
                session,
                client=client,
                operation=operation,
                external_id=external_id,
                target_user_id=user.id,
                payload_hash=payload_hash,
                status="rejected",
                error_code="LAST_ADMIN_PROTECTED",
            )
            await session.commit()
            raise _scim_http(
                403,
                "mutability",
                "Cannot disable the last administrator via SCIM",
            ) from None
        changed = True
    elif new_active is True and not user.is_active:
        await enable_user(
            target=user,
            actor_user_id=None,
            actor_client=client,
            session=session,
        )
        changed = True

    if changed and new_active is None:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=None,
                target_user_id=user.id,
                event_type="scim_user_updated",
                metadata={"client_id": str(client.id), "external_id": external_id},
            ),
        )

    inserted = await _record_event(
        session,
        client=client,
        operation=operation,
        external_id=external_id,
        target_user_id=user.id,
        payload_hash=payload_hash,
        status="applied" if changed else "no_op",
    )
    if not inserted:
        # Concurrent retry won the race — return the cached state.
        await session.rollback()
        await session.refresh(user)
        return _scim_response(user_to_scim(user, external_id=external_id))

    await session.commit()
    return _scim_response(user_to_scim(user, external_id=external_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError as exc:
        raise _scim_http(400, "invalidSyntax", "Body is not valid JSON") from exc


def _parse_eq_filter(raw: str) -> tuple[str, str]:
    """Tiny SCIM filter parser for `<attr> eq "<value>"`."""

    text = raw.strip()
    if " eq " not in text:
        raise _scim_http(400, "invalidFilter", f"Unsupported filter: {raw}")
    attr, _, rhs = text.partition(" eq ")
    rhs = rhs.strip()
    if not (rhs.startswith('"') and rhs.endswith('"')):
        raise _scim_http(400, "invalidFilter", f"Filter value must be quoted: {raw}")
    return attr.strip(), rhs[1:-1]


def _user_email(payload: dict[str, Any]) -> str | None:
    raw = payload.get("userName")
    if isinstance(raw, str) and raw:
        return raw.strip().lower()
    emails = payload.get("emails")
    if isinstance(emails, list):
        for entry in emails:
            if isinstance(entry, dict) and entry.get("primary") and isinstance(entry.get("value"), str):
                return entry["value"].strip().lower()
        for entry in emails:
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                return entry["value"].strip().lower()
    return None


def _full_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("name")
    if isinstance(name, dict):
        formatted = name.get("formatted")
        if isinstance(formatted, str) and formatted.strip():
            return formatted.strip()
        given = name.get("givenName") or ""
        family = name.get("familyName") or ""
        merged = f"{given} {family}".strip()
        if merged:
            return merged
    display = payload.get("displayName")
    if isinstance(display, str) and display.strip():
        return display.strip()
    return None


async def _external_id_for(
    session: AsyncSession,
    user: User,
    client: SCIMClient,
) -> str | None:
    """Look up the IdP-side subject (externalId) for this user / client."""

    identity = (
        await session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.provider_id == client.provider_id,
            )
        )
    ).scalar_one_or_none()
    return identity.subject if identity else None
