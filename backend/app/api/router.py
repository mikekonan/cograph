from fastapi import APIRouter

from backend.app.api.admin import router as admin_router
from backend.app.api.admin_git_hosts import router as admin_git_hosts_router
from backend.app.api.admin_groups import router as admin_groups_router
from backend.app.api.admin_identity_providers import router as admin_idp_router
from backend.app.api.admin_llm_runtime import router as admin_llm_runtime_router
from backend.app.api.admin_scim_clients import router as admin_scim_clients_router
from backend.app.api.admin_secrets import router as admin_secrets_router
from backend.app.api.admin_users import router as admin_users_router
from backend.app.api.auth import router as auth_router
from backend.app.api.auth_oidc import router as auth_oidc_router
from backend.app.api.mcp_admin import router as mcp_admin_router
from backend.app.api.md_collections import router as md_collections_router
from backend.app.api.docs import router as docs_router
from backend.app.api.graph import router as graph_router
from backend.app.api.health import router as health_router
from backend.app.api.jobs import router as jobs_router
from backend.app.api.me_identities import router as me_identities_router
from backend.app.api.personal_access_tokens import (
    router as personal_access_tokens_router,
)
from backend.app.api.query_logs import router as query_logs_router
from backend.app.api.retrieval import router as retrieval_router
from backend.app.api.repo_documents import router as repo_documents_router
from backend.app.api.repos import router as repos_router
from backend.app.api.route import router as route_router
from backend.app.api.webhook_github import router as webhook_github_router
from backend.app.api.wiki import router as wiki_router

api_router = APIRouter(prefix="/api")
api_router.include_router(admin_router)
api_router.include_router(admin_git_hosts_router)
api_router.include_router(admin_groups_router)
api_router.include_router(admin_idp_router)
api_router.include_router(admin_llm_runtime_router)
api_router.include_router(admin_scim_clients_router)
api_router.include_router(admin_secrets_router)
api_router.include_router(admin_users_router)
api_router.include_router(auth_router)
api_router.include_router(auth_oidc_router)
api_router.include_router(docs_router)
api_router.include_router(graph_router)
api_router.include_router(jobs_router)
api_router.include_router(me_identities_router)
api_router.include_router(personal_access_tokens_router)
api_router.include_router(query_logs_router)
api_router.include_router(mcp_admin_router)
api_router.include_router(md_collections_router)
api_router.include_router(retrieval_router)
api_router.include_router(repo_documents_router)
api_router.include_router(repos_router)
api_router.include_router(route_router)
api_router.include_router(webhook_github_router)
api_router.include_router(wiki_router)
# Mount health under /api as well so GET /api/health works per FE_CONTRACT §7.
# The handler lives in health_router at path "/health", so it resolves to
# "/api/health" when included here.
api_router.include_router(health_router)

# root_router keeps /health alive for uptime monitors / k8s liveness probes
# that probe the root path (no /api prefix).
root_router = APIRouter()
root_router.include_router(health_router)
