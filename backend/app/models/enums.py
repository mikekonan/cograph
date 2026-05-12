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
