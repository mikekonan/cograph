"""User-facing query observability.

Separate from `backend.app.audit` — audit_events records privileged
admin actions; this package records what users *ask* cograph via
search/retrieve from REST or MCP.

Write path is async via arq (see `record_query_log` task in
`backend.app.pipeline.worker`). Callers stay non-blocking and
log-failures never propagate to the user.
"""

from backend.app.query_logs.recorder import (
    QueryLogPayload,
    enqueue_query_log,
    truncate_query_text,
)

__all__ = [
    "QueryLogPayload",
    "enqueue_query_log",
    "truncate_query_text",
]
