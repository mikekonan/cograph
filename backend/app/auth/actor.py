from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from backend.app.models.user import User

ActorMethod = Literal["cookie_jwt", "bearer_jwt", "pat"]


# Cookie / bearer-JWT actors implicitly hold every scope. PAT actors are
# gated by their row's `scopes` column.
ALL_SCOPES: frozenset[str] = frozenset({"api:read", "api:write", "mcp"})


@dataclass(slots=True, frozen=True)
class AuthenticatedActor:
    """Single resolver result used by every authenticated endpoint.

    `scopes` carries the row scopes for PAT actors; for cookie / bearer-JWT
    it is the full implicit set so `require_scope` can short-circuit the
    membership check uniformly.
    """

    user: User
    method: ActorMethod
    scopes: frozenset[str]
    token_id: UUID | None = None
