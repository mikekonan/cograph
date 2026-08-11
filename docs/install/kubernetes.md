# Kubernetes

The chart lives in the repository at `helm/cograph`. It is not published to a
chart registry — install from a checkout.

## Prerequisites the chart does not provide

The chart declares **no dependencies**. Both datastores are yours to run:

- **PostgreSQL 16 with `pgvector`.** The role Cograph connects as needs
  permission to `CREATE EXTENSION` — migrations create `pgcrypto`, `vector` and
  `pg_trgm` themselves.
- **Redis 7.** Job queue and distributed rate limiting.

You also need an OpenAI-compatible endpoint reachable from the cluster, and a
`ReadWriteMany` volume unless you use the sidecar mode described below.

## What gets deployed

| Object | Notes |
| --- | --- |
| Deployment `…-backend` | gunicorn with the uvicorn worker class. **Does not run migrations** — unlike Compose. |
| Deployment `…-worker` | The arq worker. Omitted when `worker.runAsSidecar=true`. |
| Deployment `…-web` | nginx serving the SPA and proxying to the backend. |
| Service `…-backend`, `…-web` | Both `ClusterIP`. |
| Job `…-migrate` | Helm hook `pre-install,pre-upgrade`, `restartPolicy: OnFailure`. Runs `alembic upgrade head`. A failing migration blocks the release. |
| Secret `…-env` | Rendered only when `secrets.existingSecret` is empty. |
| PVC `…-checkouts` | Rendered only when `checkouts.existingClaim` is empty. |
| Ingress | Only when `ingress.enabled`. Routes to the **web** service, not the backend. |

Both deployments carry a `checksum/secret` pod annotation, so changing the secret
triggers a rollout rather than leaving stale pods.

## Required secret keys

Five keys, whether you let the chart render the Secret or supply your own via
`secrets.existingSecret`:

```
COGRAPH_DATABASE__URL
COGRAPH_REDIS__URL
COGRAPH_AUTH__JWT_SECRET
COGRAPH_EMBEDDING__API_KEY
COGRAPH_COMPLETION__API_KEY
```

They are consumed with `envFrom.secretRef` by the backend, the worker and the
migrate job.

::: warning Production will refuse to boot on a weak JWT secret
`COGRAPH_AUTH__JWT_SECRET` must be at least 32 characters and must not be one of
the known development placeholders. Otherwise the process raises at startup and
you get a crash-loop, not a warning.
:::

## Minimum values for a real deployment

```yaml
# values.prod.yaml
images:
  backend:
    tag: v2026.06.18-abc1234   # pin a release tag, never `latest`
  web:
    tag: v2026.06.18-abc1234

app:
  environment: production
  auth:
    secureCookies: true
  embedding:
    enabled: true
    model: text-embedding-3-small
    dimensions: 1536
  completion:
    enabled: true

secrets:
  # Prefer existingSecret and manage these with your own secret tooling.
  existingSecret: cograph-env

checkouts:
  storageClassName: your-rwx-class
  accessModes: [ReadWriteMany]
  size: 40Gi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: cograph.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: cograph-tls
      hosts: [cograph.example.com]
```

```bash
helm upgrade --install cograph ./helm/cograph \
  --namespace cograph --create-namespace \
  --values values.prod.yaml
```

## Three things that bite operators

### 1 · The checkout volume needs ReadWriteMany

The backend and the worker both mount the same claim, because the backend
**writes** to it and the worker **reads** what it wrote: an uploaded zip archive
is streamed to `<checkouts_root>/<repo_id>.zip` by the API request, and the
worker extracts it during the sync. Git clones are written by the worker and read
back on later syncs.

In the default split-deployment topology that means genuine `ReadWriteMany`, which
many storage classes do not offer.

If your cluster only has `ReadWriteOnce`, use the sidecar:

```yaml
worker:
  runAsSidecar: true
backend:
  replicaCount: 1
```

The worker then runs as a second container inside the backend pod, sharing its
volume mount. `worker.replicaCount` is ignored in this mode, and the backend must
stay at one replica.

Note that the volume is not purely disposable: zip-sourced repositories keep
their only copy of the uploaded archive there. See
[backup](/operations#backup-and-restore).

### 2 · The worker's memory limit is too low for wiki generation

The worker runs up to four pipeline jobs concurrently. That figure was tuned down
from ten against an **8 GiB** container after an OOM kill — each page being
written holds its subgraph and retrieved chunks in memory.

The chart's default worker limit is `1Gi`. If you generate wikis, raise it:

```yaml
worker:
  resources:
    requests: { cpu: 500m, memory: 2Gi }
    limits:   { cpu: "2",  memory: 8Gi }
```

The concurrency figure itself is not exposed as a chart value or an environment
variable. If you need it lower, that is a code change.

### 3 · Single-replica rollouts on a CPU-tight cluster

With one replica and a rolling update, the new pod must schedule before the old
one terminates. On a cluster without headroom that deadlocks. The chart leaves
`strategy` empty so you can set:

```yaml
backend:
  strategy:
    type: Recreate
```

## Health and probes

| Component | Readiness | Liveness |
| --- | --- | --- |
| backend | `GET /health`, delay 10s, period 10s | `GET /health`, delay 20s, period 20s |
| web | `GET /health`, delay 5s, period 10s | `GET /`, delay 10s, period 20s |
| worker | none | none |

`/health` is served both at `/api/health` (for the frontend contract) and at bare
`/health` (for probes and uptime monitors). There are no startup probes; if your
database is slow to accept connections at cold start, raise the readiness delay.

## Known chart gaps

The chart is deliberately small and does not yet expose values for a number of
settings. Today the only way to set them is to add extra keys to
`secrets.existingSecret`, since it is consumed via `envFrom`:

- `COGRAPH_CORS__ALLOWED_ORIGINS`
- `COGRAPH_AUTH__EXTERNAL_URL` — needed for OIDC redirect-URI matching
- `COGRAPH_AUTH__PUBLIC_READ`, `COGRAPH_AUTH__REGISTRATION_ENABLED`
- `COGRAPH_MCP__ALLOWED_HOSTS`, `COGRAPH_MCP__ALLOWED_ORIGINS`
- `COGRAPH_AUTH__LLM_ENCRYPTION_SECRET`, `COGRAPH_AUTH__OIDC_ENCRYPTION_SECRET`
- everything under `COGRAPH_LOGGING__`, `COGRAPH_RETRIEVAL__`, `COGRAPH_QUERY_LOG__`

There is also no `extraEnv`, `nodeSelector`, `tolerations`, `affinity`,
`serviceAccount`, `podSecurityContext`, HPA or PDB hook. If you need those,
expect to patch the chart.

## After the first install

The chart brings up the software; it does not configure the runtime. Complete
steps 2 and 3 of the [quickstart](/quickstart) against your deployed URL — create
the first admin from the bootstrap token, then assign the LLM roles. The token is
written inside the backend container:

```bash
kubectl -n cograph exec deploy/cograph-backend -- cat /app/.cograph/bootstrap.token
```

::: tip Read-only root filesystem
If the container filesystem is read-only, the token cannot be written and the
startup log says so. Point `COGRAPH_BOOTSTRAP_TOKEN_FILE` at a writable path (an
`emptyDir`), or create the admin with the CLI via `kubectl exec`.
:::

## Upgrades

`helm upgrade` runs the migrate Job as a pre-upgrade hook, so schema changes land
before new pods start. Two upgrade actions have costs worth knowing about before
you trigger them — changing the embedding model forces a full re-embed, and a
wiki schema-version bump forces a full wiki rebuild. Both are covered in
[Operations](/operations#upgrades).
