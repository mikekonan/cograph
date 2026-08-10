"""SCIM 2.0 User resource ↔ Cograph `User` translation.

This module covers only the User core schema (RFC 7643 §4.1). Groups,
ETag concurrency, sort, complex filters, and bulk are out of scope.

Mapping:

| SCIM                          | Cograph                              |
|-------------------------------|--------------------------------------|
| `userName`                    | `users.email`                        |
| `emails[primary].value`       | `users.email`                        |
| `name.givenName + familyName` | `users.name`                         |
| `active`                      | `users.is_active`                    |
| `externalId`                  | `user_identities.subject` (per IdP)  |
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.user import User


SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def user_to_scim(user: User, *, external_id: str | None = None) -> dict[str, Any]:
    """Render a Cograph `User` as a SCIM 2.0 User resource."""

    given, family = _split_name(user.name)
    body: dict[str, Any] = {
        "schemas": [SCIM_USER_SCHEMA],
        "id": str(user.id),
        "userName": user.email,
        "active": user.is_active,
        "emails": [{"value": user.email, "primary": True, "type": "work"}],
        "name": {
            "givenName": given,
            "familyName": family,
            "formatted": user.name or user.email,
        },
        "meta": {
            "resourceType": "User",
            "created": _iso(user.created_at),
            "lastModified": _iso(user.deactivated_at or user.created_at),
            "location": f"/scim/v2/Users/{user.id}",
        },
    }
    if external_id is not None:
        body["externalId"] = external_id
    return body


def _split_name(full: str | None) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = full.strip().split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def scim_error(*, status_code: int, scim_type: str | None, detail: str) -> dict[str, Any]:
    """Build the RFC 7644 §3.12 error envelope."""

    body: dict[str, Any] = {
        "schemas": [SCIM_ERROR_SCHEMA],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return body


def list_response(resources: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    """Wrap a list of resources in the SCIM ListResponse envelope."""

    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": total if total is not None else len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }
