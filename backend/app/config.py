from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cograph"
    echo: bool = False


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    allow_in_memory_rate_limit_fallback: bool = False


class GitSettings(BaseModel):
    checkouts_root: Path = Path(".cograph/checkouts")


class ArchiveUploadSettings(BaseModel):
    """Caps for the `POST /repos/upload` (zip) ingest path.

    Compressed cap protects the inbound side; decompressed and per-file
    caps plus the inflation ratio guard against zip-bomb payloads. All
    sizes in bytes.
    """

    max_compressed_bytes: int = 200 * 1024 * 1024
    max_decompressed_bytes: int = 1024 * 1024 * 1024
    max_per_file_bytes: int = 50 * 1024 * 1024
    max_inflation_ratio: float = 100.0
    max_entries: int = 200_000


class AuthSettings(BaseModel):
    jwt_secret: SecretStr = SecretStr("dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 28800
    refresh_token_ttl_seconds: int = 2592000
    access_cookie_name: str = "cograph_access"
    refresh_cookie_name: str = "cograph_refresh"
    csrf_cookie_name: str = "cograph_csrf"
    registration_enabled: bool = False
    public_read: bool = False
    secure_cookies: bool = False
    # External URL used for OIDC redirect_uri building (Phase 30.3).
    # Most IdPs require an exact-match callback URL, so the operator pins
    # the public origin here. When unset, request.base_url is used as a
    # last resort (works in dev and behind correctly-proxied prod).
    external_url: str | None = None
    # OIDC PKCE/state TTL — short window between authorize redirect and
    # callback. 600 s is the OIDC convention (Okta default = 600).
    oidc_state_ttl_seconds: int = 600
    # Independent encryption secrets (CRIT-03). When set, the LLM and OIDC
    # ciphers derive their Fernet keys from these instead of `jwt_secret`,
    # so a leak of `jwt_secret` no longer compromises encrypted-at-rest
    # provider/IdP credentials. Both default to None for backwards
    # compatibility — existing deployments keep using the JWT-derived
    # keys until the operator sets these AND runs
    # ``cograph-backend reencrypt-secrets`` to re-encrypt rows under
    # the new key. Production boot warns when either is unset.
    llm_encryption_secret: SecretStr | None = None
    oidc_encryption_secret: SecretStr | None = None


class CorsSettings(BaseModel):
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:3000",
        ]
    )


class LoggingSettings(BaseModel):
    """Process-wide logging knobs.

    The defaults make ``backend.*`` loggers visible at INFO so login
    flows, OIDC discovery, PAT use, and per-request access lines surface
    in ``docker logs``. Flip ``format=json`` for structured collectors.
    """

    level: str = "INFO"
    format: str = "text"
    access_log: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "LoggingSettings":
        if self.level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(
                "logging.level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
        if self.format not in ("text", "json"):
            raise ValueError("logging.format must be 'text' or 'json'")
        return self


class EmbeddingSettings(BaseModel):
    """Embedding provider config.  disabled by default until a provider is configured."""

    enabled: bool = False
    api_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    batch_size: int = 256

    @model_validator(mode="after")
    def _require_api_key_when_enabled(self) -> "EmbeddingSettings":
        if self.enabled and not self.api_key.get_secret_value():
            raise ValueError(
                "embedding.enabled=true requires a non-empty embedding.api_key"
            )
        return self

    @model_validator(mode="after")
    def _enforce_supported_dimensions(self) -> "EmbeddingSettings":
        if self.dimensions != 1536:
            raise ValueError(
                "only 1536-dim embeddings are supported in v1; "
                "schema migration required for other sizes"
            )
        return self


class CompletionSettings(BaseModel):
    """LLM completion provider config for summary generation. Disabled by default."""

    enabled: bool = False
    preview_enabled: bool = True
    api_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    model: str = "gpt-5.4-mini"

    @model_validator(mode="after")
    def _require_api_key_when_enabled(self) -> "CompletionSettings":
        if self.enabled and not self.api_key.get_secret_value():
            raise ValueError(
                "completion.enabled=true requires a non-empty completion.api_key"
            )
        return self


# Phase 7d — hybrid retrieval (BM25 + RRF + optional rerank).
# Six retrieval-layer defaults.
_RERANK_PROVIDERS = ("local_cross_encoder", "cohere", "voyage", "jina", "disabled")


class RerankSettings(BaseModel):
    # Default provider is `disabled` — the base image ships without
    # `sentence-transformers` (~500 MB torch dep). Flip to
    # `local_cross_encoder` and install the `[reranker-local]` extra to
    # opt into cross-encoder rerank quality.
    enabled: bool = True
    threshold: int = Field(default=50, ge=0)
    provider: str = "disabled"
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    @model_validator(mode="after")
    def _validate_provider(self) -> "RerankSettings":
        if self.provider not in _RERANK_PROVIDERS:
            raise ValueError(
                f"rerank.provider must be one of {_RERANK_PROVIDERS}, got {self.provider!r}"
            )
        return self


class RetrievalSettings(BaseModel):
    rrf_k: int = Field(default=60, gt=0)
    candidate_cap: int = Field(default=300, gt=0)
    rerank: RerankSettings = Field(default_factory=RerankSettings)


class McpSettings(BaseModel):
    """DNS-rebinding protection for the mounted MCP transport.

    FastMCP can validate the `Host` and `Origin` headers on every MCP
    request and reject anything not on an allowlist. We default both
    lists to empty so the protection stays *off* — turning it on with
    no allowlist would 421/403 every request and silently break
    deployments. Operators opt in by listing their public hostname
    (e.g. ``cograph.internal``) and Origin (e.g. ``https://cograph.internal``);
    once non-empty, the middleware activates automatically.
    """

    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COGRAPH_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Cograph"
    environment: Environment = Environment.DEVELOPMENT
    version: str = "0.1.0"
    api_prefix: str = "/api"

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    git: GitSettings = Field(default_factory=GitSettings)
    archive_upload: ArchiveUploadSettings = Field(default_factory=ArchiveUploadSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    completion: CompletionSettings = Field(default_factory=CompletionSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)

    @model_validator(mode="after")
    def _enforce_production_auth_secret(self) -> "Settings":
        if self.environment is not Environment.PRODUCTION:
            return self
        jwt_secret = self.auth.jwt_secret.get_secret_value()
        unsafe_defaults = {"", "dev-secret-change-me", "change-me-in-production"}
        if jwt_secret in unsafe_defaults or len(jwt_secret) < 32:
            raise ValueError(
                "production requires auth.jwt_secret to be set to a non-default "
                "secret of at least 32 characters"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        yaml_path = Path(os.environ.get("COGRAPH_CONFIG_FILE", "config.yaml"))
        sources = [init_settings, env_settings, dotenv_settings]
        if yaml_path.exists():
            sources.append(
                YamlConfigSettingsSource(
                    settings_cls,
                    yaml_file=yaml_path,
                )
            )
        sources.append(file_secret_settings)
        return tuple(sources)

    @property
    def is_development(self) -> bool:
        return self.environment is Environment.DEVELOPMENT

    @property
    def effective_secure_cookies(self) -> bool:
        """
        Returns True if cookies should use the Secure attribute.

        Always True in production (even if auth.secure_cookies was not explicitly
        set), so that deployers don't have to remember to set it.  In development
        or testing the default remains False to allow plain-HTTP localhost.
        """
        return self.auth.secure_cookies or self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
