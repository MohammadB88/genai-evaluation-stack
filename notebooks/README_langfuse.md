# Installing Langfuse on OpenShift (Phase 4 reference — not adopted yet)

> **Status: reference notes only.** Per `CLAUDE.md`, Langfuse is a *trigger-based* addition —
> adopted only once "production tracing, annotation workflows, and prompt release management
> become real requirements" (Phase 4). That trigger has **not fired** in this repo yet; the only
> thing that exists today is the local Docker Compose exploration in
> [`phase4_langfuse_prototype.ipynb`](phase4_langfuse_prototype.ipynb). This document exists so
> that when Phase 4 *is* triggered, there's a starting point for running the same stack on
> OpenShift instead of a laptop. Nothing here is wired into CI, the eval runner, or any deployed
> environment.

## Why OpenShift needs more than `docker compose up`

Langfuse's self-hosted stack is multiple services: web UI, async worker, Postgres (metadata),
ClickHouse (trace analytics/OLAP store), Redis/Valkey (queueing), and S3-compatible blob storage
(MinIO, or an existing object store). On OpenShift there is no Compose engine, so each service
needs its own Deployment/StatefulSet + Service, plus OpenShift-specific concerns:
arbitrary/non-root UIDs, `Route`s instead of raw ports, and `SecurityContextConstraints` (SCC).

## Option A — Langfuse Helm chart (recommended starting point)

Langfuse publishes an official Helm chart that deploys all required components
(web, worker, Postgres, ClickHouse, Redis, and optionally MinIO) as a single release.

1. **Prerequisites**
   - `oc` CLI logged into the target cluster/project (`oc new-project genai-eval-langfuse` or similar).
   - `helm` v3 CLI.
   - Cluster access to pull images (see the air-gap note below if the cluster has none).
   - A generated `NEXTAUTH_SECRET`, `SALT`, and `ENCRYPTION_KEY` (Langfuse requires these; generate
     with `openssl rand -hex 32` for each — do not reuse the values from `phase4_langfuse_prototype.ipynb`'s
     local exploration).

2. **Add the chart repo**
   ```bash
   helm repo add langfuse https://langfuse.github.io/langfuse-k8s
   helm repo update
   ```

3. **Write a values override** (`langfuse-values.yaml`) — at minimum, pin image tags (don't track
   `latest` — this repo pins exact versions everywhere else, per `CLAUDE.md`), set the generated
   secrets, and size persistent storage for Postgres/ClickHouse/MinIO:
   ```yaml
   langfuse:
     nextauth:
       secret:
         value: "<openssl rand -hex 32>"
     salt:
       value: "<openssl rand -hex 32>"
     encryptionKey:
       value: "<openssl rand -hex 32>"

   postgresql:
     auth:
       password: "<generate>"

   clickhouse:
     auth:
       password: "<generate>"

   s3:
     # Point at an existing S3-compatible object store if the cluster has one
     # (e.g. ODF/Noobaa on OpenShift Data Foundation) instead of bundling MinIO.
     bucket: "langfuse"
   ```

4. **Install**
   ```bash
   helm install langfuse langfuse/langfuse -n genai-eval-langfuse -f langfuse-values.yaml
   ```

5. **Expose via an OpenShift Route** (the chart creates a `Service`, not a `Route`):
   ```bash
   oc create route edge langfuse-web \
     --service=langfuse-web \
     --port=3000 \
     -n genai-eval-langfuse
   ```
   TLS termination (`edge`) is the usual choice so the OpenShift router handles the cert.

## Option B — plain manifests (no Helm / air-gapped clusters without chart mirroring)

If Helm isn't available or the chart's sub-dependencies (Bitnami Postgres/ClickHouse/Redis
subcharts) can't be mirrored into the cluster's registry, deploy from plain YAML instead:

- `Deployment` for `langfuse/langfuse` (web) and `langfuse/langfuse-worker`.
- `StatefulSet`s for Postgres, ClickHouse, and Redis/Valkey (or point at existing managed
  instances if the cluster already runs them for other workloads — reuse before adding new
  stateful services).
- `Secret` for `NEXTAUTH_SECRET` / `SALT` / `ENCRYPTION_KEY` / DB credentials — never inline these
  in a `ConfigMap`.
- `Route` for the web service, as in Option A step 5.

This is more manual to maintain (chart upgrades won't carry this forward), so prefer Option A
unless there's a specific reason not to use Helm.

A reference script implementing this option end-to-end is provided:
[`install_langfuse_openshift.sh`](install_langfuse_openshift.sh). It creates the namespace,
generates/applies the required `Secret`s, deploys Postgres/ClickHouse/Redis/MinIO as
`StatefulSet`s and Langfuse web/worker as `Deployment`s, and creates the `Route`. It follows this
repo's env-var-first / fail-fast convention — required secrets (`NEXTAUTH_SECRET`, `SALT`,
`ENCRYPTION_KEY`, DB passwords) are read from env vars, prompted for interactively on a TTY if
missing, or the script aborts in non-interactive contexts:

```bash
export NAMESPACE=genai-eval-langfuse                              # optional
export IMAGE_REGISTRY=my-internal-registry.example.com/langfuse   # set for air-gapped clusters
export NEXTAUTH_SECRET=$(openssl rand -hex 32)
export LANGFUSE_SALT=$(openssl rand -hex 32)
export ENCRYPTION_KEY=$(openssl rand -hex 32)
export POSTGRES_PASSWORD=$(openssl rand -hex 24)
export CLICKHOUSE_PASSWORD=$(openssl rand -hex 24)
export MINIO_ROOT_PASSWORD=$(openssl rand -hex 24)

./install_langfuse_openshift.sh
```

Review the manifest section of the script before running against a shared cluster — image tags,
PVC sizes, and ClickHouse resource requests are set to reasonable defaults but are not tuned for
any specific cluster's quota.

## OpenShift-specific gotchas

- **Non-root, arbitrary UID.** OpenShift's default SCC (`restricted-v2`) runs containers with a
  random UID and GID `0`. Langfuse's images generally support this, but ClickHouse and some
  Bitnami subcharts assume a fixed UID by default — check the chart's `*.podSecurityContext` /
  `*.containerSecurityContext` values and set `runAsNonRoot: true` with no fixed `runAsUser`,
  matching this repo's existing convention (`CLAUDE.md`'s "OpenShift-clean containers" section:
  writable dirs owned by group `0`, `chmod g=u`, no fixed UID).
- **PVC access modes.** ClickHouse and Postgres StatefulSets need `ReadWriteOnce` PVCs — confirm
  the cluster's default `StorageClass` supports it (most do; just don't assume `ReadWriteMany`
  is needed here).
- **Routes vs. Ingress.** Use `oc create route` / a `Route` object, not a raw Kubernetes `Ingress`,
  for anything meant to be reachable from outside the cluster.
- **NetworkPolicy.** If the namespace has default-deny NetworkPolicies (common in
  multi-tenant OpenShift clusters), add explicit allow rules between the web/worker pods and
  Postgres/ClickHouse/Redis, and from the eval-runner's namespace to the Langfuse web Service for
  SDK calls.
- **Resource requests/limits.** ClickHouse in particular is memory-hungry under default chart
  settings; set explicit `resources.requests`/`limits` before this goes anywhere near a shared
  cluster quota.

## Air-gap note

Every image (`langfuse/langfuse`, `langfuse/langfuse-worker`, Postgres, ClickHouse, Redis/Valkey,
and MinIO if used) must be mirrored into the cluster's internal registry ahead of time — the same
constraint `CLAUDE.md` already applies to DeepEval/MLflow. `helm template` the chart first to get
the exact image list, mirror each with `oc image mirror` (or `skopeo copy`) into the internal
registry, then override `image.repository` for each component in `langfuse-values.yaml` to point
at the mirrored location. Per `phase4_langfuse_prototype.ipynb`, self-hosted Langfuse core does
not phone home by default — but verify that against the exact chart/image versions pinned here
before relying on it, the same way the notebook already flags.

## Connecting this repo's eval artifacts once deployed

Once a Langfuse instance is reachable (locally via the Docker Compose prototype, or on OpenShift
via the Route above), the SDK connection steps are identical — only the host changes:

```bash
export LANGFUSE_HOST="https://<the-route-created-above>"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."   # from Settings -> API Keys in the Langfuse UI
export LANGFUSE_SECRET_KEY="sk-lf-..."
```

Then Sections 4–6 of `phase4_langfuse_prototype.ipynb` (SDK connect, replay golden-set cases,
attach DeepEval scores) run unchanged against the OpenShift-hosted instance instead of
`localhost:3000`.

## Reminder: this does not change the binding architecture

Per `CLAUDE.md`: MLflow remains the system of record for offline/pre-release truth (experiments,
golden-dataset runs, CI gate results) regardless of where Langfuse is hosted. Langfuse only
becomes the system of record for *online/post-release* truth (live traces, feedback, annotation,
prompt release labels) once Phase 4 is actually triggered by real production traffic — deploying
it to OpenShift ahead of that trigger is infrastructure prep, not an architecture decision by
itself.
