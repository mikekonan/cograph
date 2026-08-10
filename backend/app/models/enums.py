from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"


class CodeNodeType(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    ATTRIBUTE = "attribute"


class SourceFileKind(StrEnum):
    CODE = "code"
    MARKDOWN = "markdown"
    OTHER = "other"


class CodeEdgeType(StrEnum):
    CALLS = "calls"
    INHERITS = "inherits"
    IMPORTS = "imports"
    DECLARES = "declares"


class CodeNodeRole(StrEnum):
    ENTRY_POINT = "entry_point"
    SERVICE = "service"
    REPOSITORY = "repository"
    MODEL = "model"
    HELPER = "helper"
    CONFIG = "config"
    TEST = "test"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    ATTRIBUTE = "attribute"
    OTHER = "other"


class RepoSource(StrEnum):
    GIT = "git"
    ZIP = "zip"


class RepositoryStatus(StrEnum):
    PENDING = "pending"
    CLONING = "cloning"
    INDEXING = "indexing"
    EMBEDDING = "embedding"
    GENERATING = "generating"
    READY = "ready"
    ERROR = "error"
    # The user pressed Delete and we kicked the cascade off to a
    # background worker — the row still exists in `repositories` until
    # the worker drains every child table, but read paths must hide it.
    DELETING = "deleting"


class RepositoryVisibility(StrEnum):
    PUBLIC = "public"
    ADMIN_ONLY = "admin_only"


class SyncSchedule(StrEnum):
    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    WEBHOOK = "webhook"


class MdCollectionVisibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"
    ADMIN_ONLY = "admin_only"


class MdLinkType(StrEnum):
    WIKI = "wiki"
    MARKDOWN = "markdown"
    ABSOLUTE = "absolute"


class MdJobKind(StrEnum):
    EMBED = "embed"
    RESOLVE_LINKS = "resolve_links"
    UPLOAD = "upload"


class MdJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class RepoSyncRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class RepoSyncTriggerKind(StrEnum):
    INITIAL = "initial"
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"


class SyncBatchKind(StrEnum):
    REPO_SYNC = "repo_sync"
    CONFLUENCE_EXPORT = "confluence_export"


class SyncBatchTrigger(StrEnum):
    INITIAL = "initial"
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"


class SyncJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SKIPPED = "skipped"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class SyncStep(StrEnum):
    CLONE = "clone"
    PARSE = "parse"
    EXTRACT_GRAPH = "extract_graph"
    EMBED = "embed"
    INDEX_REPO_DOCS = "index_repo_docs"
    EMBED_REPO_DOCS = "embed_repo_docs"
    GENERATE_SUMMARIES = "generate_summaries"
    GENERATE_WIKI = "generate_wiki"
    EXPORT_CONFLUENCE = "export_confluence"


class GrantLevel(StrEnum):
    """Access-level ladder for per-(group, resource) ACL grants.

    Two levels: READ (visible + queryable via API) and WRITE
    (additionally allowed to run jobs — reindex, upload, delete
    documents, etc.). NONE is the absence of a grant row, not an enum
    value. Grant administration (handing out grants) is OWNER/ADMIN
    role only and lives outside this ladder.

    The values are the same strings stored in the ``level``
    CHECK-constrained column on ``repository_grants`` and
    ``collection_grants``, so the enum compares directly against raw
    row values without translation.
    """

    READ = "read"
    WRITE = "write"


class QueryLogSource(StrEnum):
    """Channel a user-facing query arrived through.

    `REST` covers POST /retrieve, POST /md_collections/{id}/search and
    any future REST search endpoint. `MCP` covers tool calls from MCP
    clients (Claude Desktop, Cursor, Codex, etc.) — those go through
    backend/app/mcp/services.py payload builders.
    """

    REST = "rest"
    MCP = "mcp"


class QueryLogStatus(StrEnum):
    """Outcome of a single user-facing query.

    `OK` — the query returned at least one result.
    `EMPTY` — the query succeeded but returned zero results. Separated
    from OK so admins can find gaps in the index / wiki without
    grepping for `result_count = 0`.
    `ERROR` — the query raised. `error_code` carries the short slug.
    """

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


class SyncErrorCode(StrEnum):
    CHECKOUT_NOT_FOUND = "checkout_not_found"
    CHECKOUT_INVALID = "checkout_invalid"
    EMBEDDING_PROVIDER_FAILED = "embedding_provider_failed"
    GRAPH_INGEST_FAILED = "graph_ingest_failed"
    PARSE_DB_CONFLICT = "parse_db_conflict"
    GO_BUILD_CONSTRAINT_UNSUPPORTED = "go_build_constraint_unsupported"
    GO_BUILD_VARIANT_CONFLICT = "go_build_variant_conflict"
    SUMMARY_PROVIDER_FAILED = "summary_provider_failed"
    WIKI_PROVIDER_FAILED = "wiki_provider_failed"
    STEP_TIMEOUT = "step_timeout"
