import { adminHandlers } from "@/mocks/handlers/admin";
import { authHandlers } from "@/mocks/handlers/auth";
import { docsHandlers } from "@/mocks/handlers/docs";
import { gitHostsHandlers } from "@/mocks/handlers/gitHosts";
import { graphHandlers } from "@/mocks/handlers/graph";
import { healthHandlers } from "@/mocks/handlers/health";
import { identitiesHandlers } from "@/mocks/handlers/identities";
import { identityProvidersHandlers } from "@/mocks/handlers/identityProviders";
import { jobsHandlers } from "@/mocks/handlers/jobs";
import { llmRuntimeHandlers } from "@/mocks/handlers/llmRuntime";
import { mdCollectionsHandlers } from "@/mocks/handlers/mdCollections";
import { oidcHandlers } from "@/mocks/handlers/oidc";
import { repoHandlers } from "@/mocks/handlers/repos";
import { retrieveHandlers } from "@/mocks/handlers/retrieve";
import { scimHandlers } from "@/mocks/handlers/scim";
import { tokensHandlers } from "@/mocks/handlers/tokens";
import { usersHandlers } from "@/mocks/handlers/users";
import { wikiHandlers } from "@/mocks/handlers/wiki";

export const handlers = [
  ...authHandlers,
  ...adminHandlers,
  ...usersHandlers,
  ...healthHandlers,
  ...repoHandlers,
  ...wikiHandlers,
  ...docsHandlers,
  ...graphHandlers,
  ...jobsHandlers,
  ...tokensHandlers,
  ...retrieveHandlers,
  ...mdCollectionsHandlers,
  ...identityProvidersHandlers,
  ...identitiesHandlers,
  ...oidcHandlers,
  ...scimHandlers,
  ...gitHostsHandlers,
  ...llmRuntimeHandlers,
];
