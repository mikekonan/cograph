/**
 * In-memory state for MSW handlers. Survives route changes within a session,
 * resets on page reload.
 *
 * A dev toolbar (on /design) flips `mockAuth.isAdmin` to preview
 * protected routes without a real backend.
 */

import type { GitCredentialView, GitHostView, WebhookDeliveryView } from "@/api/gitHosts";
import type { LinkedIdentity } from "@/api/identities";
import type { IdentityProvider } from "@/api/identityProviders";
import type { AssignmentView, EmbeddingStatusView, LLMRole } from "@/api/llmRuntime";
import type { ScimClientView, ScimEventView } from "@/api/scim";
import type { TokenView } from "@/api/tokens";
import type { LLMSecret, Repository } from "@/api/types";
import type { AdminUser } from "@/api/users";
import { seedGitCredentials, seedGitHosts, seedWebhookDeliveries } from "@/mocks/fixtures/gitHosts";
import { seedMyIdentities } from "@/mocks/fixtures/identities";
import { seedIdentityProviders } from "@/mocks/fixtures/identityProviders";
import { seedRepos } from "@/mocks/fixtures/repos";
import { seedScimClients, seedScimEvents } from "@/mocks/fixtures/scim";
import { seedSecrets } from "@/mocks/fixtures/secrets";
import { seedTokens } from "@/mocks/fixtures/tokens";
import { seedUsers } from "@/mocks/fixtures/users";

const DEFAULT_MOCK_AUTH = {
  isAdmin: false,
  email: "admin@example.com",
  name: "Mock Admin",
};

const DEFAULT_MOCK_RUNTIME = {
  publicRead: false,
};

function cloneSeedRepos(): Repository[] {
  return structuredClone(seedRepos);
}

function cloneSeedSecrets(): LLMSecret[] {
  return structuredClone(seedSecrets);
}

function cloneSeedUsers(): AdminUser[] {
  return structuredClone(seedUsers);
}

function cloneSeedTokens(): TokenView[] {
  return structuredClone(seedTokens);
}

function cloneSeedIdentityProviders(): IdentityProvider[] {
  return structuredClone(seedIdentityProviders);
}

function cloneSeedMyIdentities(): LinkedIdentity[] {
  return structuredClone(seedMyIdentities);
}

function cloneSeedScimClients(): ScimClientView[] {
  return structuredClone(seedScimClients);
}

function cloneSeedScimEvents(): ScimEventView[] {
  return structuredClone(seedScimEvents);
}

function cloneSeedGitHosts(): GitHostView[] {
  return structuredClone(seedGitHosts);
}

function cloneSeedGitCredentials(): GitCredentialView[] {
  return structuredClone(seedGitCredentials);
}

function cloneSeedWebhookDeliveries(): WebhookDeliveryView[] {
  return structuredClone(seedWebhookDeliveries);
}

function defaultLLMAssignments(): Partial<Record<LLMRole, AssignmentView>> {
  const seed = seedSecrets[0];
  if (!seed) return {};
  const secret = {
    id: seed.id,
    name: seed.name,
    api_url: seed.api_url,
  };
  const updatedAt = new Date().toISOString();
  return {
    embedding: {
      role: "embedding",
      secret,
      model_name: "text-embedding-3-small",
      reasoning_effort: null,
      embedding_dim: 1536,
      extra_params: {},
      updated_by: null,
      updated_at: updatedAt,
    },
    completion_writer: {
      role: "completion_writer",
      secret,
      model_name: "gpt-5.4-mini",
      reasoning_effort: null,
      embedding_dim: null,
      extra_params: {},
      updated_by: null,
      updated_at: updatedAt,
    },
  };
}

function defaultLLMEmbeddingState(): EmbeddingStatusView {
  const assignment = defaultLLMAssignments().embedding;
  return {
    assigned: assignment ?? null,
    current_secret_id: assignment?.secret.id ?? null,
    current_model_name: assignment?.model_name ?? null,
    current_dim: assignment?.embedding_dim ?? null,
    stale: false,
    last_reembed_started_at: null,
    last_reembed_completed_at: null,
  };
}

export const mockAuth = {
  ...DEFAULT_MOCK_AUTH,
};

export const mockRuntime = {
  ...DEFAULT_MOCK_RUNTIME,
};

export const mockDb: {
  repos: Repository[];
  secrets: LLMSecret[];
  users: AdminUser[];
  tokens: TokenView[];
  identityProviders: IdentityProvider[];
  myIdentities: LinkedIdentity[];
  scimClients: ScimClientView[];
  scimEvents: ScimEventView[];
  gitHosts: GitHostView[];
  gitCredentials: GitCredentialView[];
  webhookDeliveries: WebhookDeliveryView[];
  llmAssignments: Partial<Record<LLMRole, AssignmentView>>;
  llmEmbeddingState: EmbeddingStatusView;
} = {
  repos: cloneSeedRepos(),
  secrets: cloneSeedSecrets(),
  users: cloneSeedUsers(),
  tokens: cloneSeedTokens(),
  identityProviders: cloneSeedIdentityProviders(),
  myIdentities: cloneSeedMyIdentities(),
  scimClients: cloneSeedScimClients(),
  scimEvents: cloneSeedScimEvents(),
  gitHosts: cloneSeedGitHosts(),
  gitCredentials: cloneSeedGitCredentials(),
  webhookDeliveries: cloneSeedWebhookDeliveries(),
  llmAssignments: defaultLLMAssignments(),
  llmEmbeddingState: defaultLLMEmbeddingState(),
};

export function resetMockState() {
  Object.assign(mockAuth, DEFAULT_MOCK_AUTH);
  Object.assign(mockRuntime, DEFAULT_MOCK_RUNTIME);
  mockDb.repos = cloneSeedRepos();
  mockDb.secrets = cloneSeedSecrets();
  mockDb.users = cloneSeedUsers();
  mockDb.tokens = cloneSeedTokens();
  mockDb.identityProviders = cloneSeedIdentityProviders();
  mockDb.myIdentities = cloneSeedMyIdentities();
  mockDb.scimClients = cloneSeedScimClients();
  mockDb.scimEvents = cloneSeedScimEvents();
  mockDb.gitHosts = cloneSeedGitHosts();
  mockDb.gitCredentials = cloneSeedGitCredentials();
  mockDb.webhookDeliveries = cloneSeedWebhookDeliveries();
  mockDb.llmAssignments = defaultLLMAssignments();
  mockDb.llmEmbeddingState = defaultLLMEmbeddingState();
}

// CSRF doubles as a sentinel — mocks accept it when auth is on.
export const MOCK_CSRF = "mock-csrf-token";
