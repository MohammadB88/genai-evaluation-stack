#!/usr/bin/env bash
# Phase 4 reference script — NOT part of the adopted Phase 1 core stack.
# See README_langfuse.md ("Option B — plain manifests") for the full explanation.
#
# Deploys a self-hosted Langfuse stack (web, worker, Postgres, ClickHouse,
# Redis/Valkey, MinIO) to OpenShift from plain manifests, without Helm.
# Intended for air-gapped / chart-mirroring-restricted clusters per
# CLAUDE.md's air-gap-by-default rule.
#
# Usage:
#   export NAMESPACE=genai-eval-langfuse        # optional, defaults below
#   export IMAGE_REGISTRY=my-internal-registry.example.com/langfuse  # required if air-gapped
#   ./install_langfuse_openshift.sh
#
# Follows this repo's env-var-first / fail-fast convention (CLAUDE.md):
# every value below reads from an env var first and the script aborts with
# a clear message if a required one is missing and the shell is non-interactive.

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Config (env-var-first, interactive fallback on a TTY, fail fast otherwise)
# ---------------------------------------------------------------------------

require_var() {
  local var_name="$1" prompt="$2"
  local current="${!var_name:-}"
  if [[ -n "$current" ]]; then
    return
  fi
  if [[ -t 0 ]]; then
    read -r -p "$prompt: " value
    export "$var_name=$value"
  else
    echo "ERROR: required env var $var_name is not set (non-interactive shell, cannot prompt)." >&2
    exit 1
  fi
}

NAMESPACE="${NAMESPACE:-genai-eval-langfuse}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"   # e.g. internal-registry.example.com/langfuse ; empty = use upstream images directly
STORAGE_CLASS="${STORAGE_CLASS:-}"     # empty = cluster default StorageClass
LANGFUSE_IMAGE_TAG="${LANGFUSE_IMAGE_TAG:-3}"          # pin, don't track :latest (CLAUDE.md)
POSTGRES_IMAGE_TAG="${POSTGRES_IMAGE_TAG:-16-alpine}"
CLICKHOUSE_IMAGE_TAG="${CLICKHOUSE_IMAGE_TAG:-24.3-alpine}"
REDIS_IMAGE_TAG="${REDIS_IMAGE_TAG:-7-alpine}"
MINIO_IMAGE_TAG="${MINIO_IMAGE_TAG:-RELEASE.2024-01-01T16-36-33Z}"

require_var NEXTAUTH_SECRET   "NEXTAUTH_SECRET (generate with: openssl rand -hex 32)"
require_var LANGFUSE_SALT     "SALT (generate with: openssl rand -hex 32)"
require_var ENCRYPTION_KEY    "ENCRYPTION_KEY (generate with: openssl rand -hex 32)"
require_var POSTGRES_PASSWORD "Postgres password (generate with: openssl rand -hex 24)"
require_var CLICKHOUSE_PASSWORD "ClickHouse password (generate with: openssl rand -hex 24)"
require_var MINIO_ROOT_PASSWORD "MinIO root password (generate with: openssl rand -hex 24, min 8 chars)"

command -v oc >/dev/null 2>&1 || { echo "ERROR: 'oc' CLI not found on PATH." >&2; exit 1; }

img() {
  # img <upstream-image-path>  ->  prints registry-qualified image ref
  local path="$1"
  if [[ -n "$IMAGE_REGISTRY" ]]; then
    echo "${IMAGE_REGISTRY%/}/${path##*/}"
  else
    echo "$path"
  fi
}

LANGFUSE_WEB_IMAGE="$(img "docker.io/langfuse/langfuse:${LANGFUSE_IMAGE_TAG}")"
LANGFUSE_WORKER_IMAGE="$(img "docker.io/langfuse/langfuse-worker:${LANGFUSE_IMAGE_TAG}")"
POSTGRES_IMAGE="$(img "docker.io/library/postgres:${POSTGRES_IMAGE_TAG}")"
CLICKHOUSE_IMAGE="$(img "docker.io/clickhouse/clickhouse-server:${CLICKHOUSE_IMAGE_TAG}")"
REDIS_IMAGE="$(img "docker.io/library/redis:${REDIS_IMAGE_TAG}")"
MINIO_IMAGE="$(img "quay.io/minio/minio:${MINIO_IMAGE_TAG}")"

echo "== Langfuse OpenShift install (Option B: plain manifests) =="
echo "Namespace:  $NAMESPACE"
echo "Images:"
echo "  web:        $LANGFUSE_WEB_IMAGE"
echo "  worker:     $LANGFUSE_WORKER_IMAGE"
echo "  postgres:   $POSTGRES_IMAGE"
echo "  clickhouse: $CLICKHOUSE_IMAGE"
echo "  redis:      $REDIS_IMAGE"
echo "  minio:      $MINIO_IMAGE"
[[ -z "$IMAGE_REGISTRY" ]] && echo "NOTE: IMAGE_REGISTRY not set — pulling from upstream registries directly (not air-gapped)."
echo

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
MANIFEST="$WORKDIR/langfuse.yaml"

STORAGE_CLASS_LINE=""
if [[ -n "$STORAGE_CLASS" ]]; then
  STORAGE_CLASS_LINE="  storageClassName: ${STORAGE_CLASS}"
fi

# ---------------------------------------------------------------------------
# 1. Namespace
# ---------------------------------------------------------------------------
oc get namespace "$NAMESPACE" >/dev/null 2>&1 || oc create namespace "$NAMESPACE"

# ---------------------------------------------------------------------------
# 2. Secrets (never inline these in a ConfigMap — CLAUDE.md / README_langfuse.md)
# ---------------------------------------------------------------------------
oc create secret generic langfuse-core \
  --namespace "$NAMESPACE" \
  --from-literal=NEXTAUTH_SECRET="$NEXTAUTH_SECRET" \
  --from-literal=SALT="$LANGFUSE_SALT" \
  --from-literal=ENCRYPTION_KEY="$ENCRYPTION_KEY" \
  --dry-run=client -o yaml | oc apply -f -

oc create secret generic langfuse-postgres \
  --namespace "$NAMESPACE" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --dry-run=client -o yaml | oc apply -f -

oc create secret generic langfuse-clickhouse \
  --namespace "$NAMESPACE" \
  --from-literal=CLICKHOUSE_PASSWORD="$CLICKHOUSE_PASSWORD" \
  --dry-run=client -o yaml | oc apply -f -

oc create secret generic langfuse-minio \
  --namespace "$NAMESPACE" \
  --from-literal=MINIO_ROOT_USER=langfuse \
  --from-literal=MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  --dry-run=client -o yaml | oc apply -f -

# ---------------------------------------------------------------------------
# 3. Manifest: StatefulSets (Postgres, ClickHouse, Redis, MinIO) + Deployments
#    (web, worker) + Services + PVCs.
#    No fixed runAsUser/fsGroup anywhere: OpenShift's restricted-v2 SCC
#    assigns an arbitrary UID with GID 0 per project, matching this repo's
#    "OpenShift-clean containers" convention in CLAUDE.md.
# ---------------------------------------------------------------------------
cat > "$MANIFEST" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: langfuse-postgres-data
  namespace: ${NAMESPACE}
spec:
  accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_LINE}
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: langfuse-postgres
  namespace: ${NAMESPACE}
spec:
  serviceName: langfuse-postgres
  replicas: 1
  selector:
    matchLabels: {app: langfuse-postgres}
  template:
    metadata:
      labels: {app: langfuse-postgres}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: postgres
          image: ${POSTGRES_IMAGE}
          ports: [{containerPort: 5432}]
          env:
            - {name: POSTGRES_USER, value: langfuse}
            - {name: POSTGRES_DB, value: langfuse}
            - name: POSTGRES_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-postgres, key: POSTGRES_PASSWORD}}
            - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql/data}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: langfuse-postgres-data}
---
apiVersion: v1
kind: Service
metadata:
  name: langfuse-postgres
  namespace: ${NAMESPACE}
spec:
  selector: {app: langfuse-postgres}
  ports: [{port: 5432, targetPort: 5432}]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: langfuse-clickhouse-data
  namespace: ${NAMESPACE}
spec:
  accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_LINE}
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: langfuse-clickhouse
  namespace: ${NAMESPACE}
spec:
  serviceName: langfuse-clickhouse
  replicas: 1
  selector:
    matchLabels: {app: langfuse-clickhouse}
  template:
    metadata:
      labels: {app: langfuse-clickhouse}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: clickhouse
          image: ${CLICKHOUSE_IMAGE}
          ports: [{containerPort: 8123}, {containerPort: 9000}]
          env:
            - {name: CLICKHOUSE_USER, value: langfuse}
            - {name: CLICKHOUSE_DB, value: default}
            - name: CLICKHOUSE_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-clickhouse, key: CLICKHOUSE_PASSWORD}}
          volumeMounts:
            - {name: data, mountPath: /var/lib/clickhouse}
          resources:
            requests: {memory: "2Gi", cpu: "500m"}
            limits: {memory: "4Gi", cpu: "2"}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: langfuse-clickhouse-data}
---
apiVersion: v1
kind: Service
metadata:
  name: langfuse-clickhouse
  namespace: ${NAMESPACE}
spec:
  selector: {app: langfuse-clickhouse}
  ports:
    - {name: http, port: 8123, targetPort: 8123}
    - {name: native, port: 9000, targetPort: 9000}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: langfuse-redis
  namespace: ${NAMESPACE}
spec:
  serviceName: langfuse-redis
  replicas: 1
  selector:
    matchLabels: {app: langfuse-redis}
  template:
    metadata:
      labels: {app: langfuse-redis}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: redis
          image: ${REDIS_IMAGE}
          ports: [{containerPort: 6379}]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
---
apiVersion: v1
kind: Service
metadata:
  name: langfuse-redis
  namespace: ${NAMESPACE}
spec:
  selector: {app: langfuse-redis}
  ports: [{port: 6379, targetPort: 6379}]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: langfuse-minio-data
  namespace: ${NAMESPACE}
spec:
  accessModes: ["ReadWriteOnce"]
${STORAGE_CLASS_LINE}
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: langfuse-minio
  namespace: ${NAMESPACE}
spec:
  serviceName: langfuse-minio
  replicas: 1
  selector:
    matchLabels: {app: langfuse-minio}
  template:
    metadata:
      labels: {app: langfuse-minio}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: minio
          image: ${MINIO_IMAGE}
          args: ["server", "/data", "--console-address", ":9001"]
          ports: [{containerPort: 9000}, {containerPort: 9001}]
          envFrom:
            - secretRef: {name: langfuse-minio}
          volumeMounts:
            - {name: data, mountPath: /data}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: langfuse-minio-data}
---
apiVersion: v1
kind: Service
metadata:
  name: langfuse-minio
  namespace: ${NAMESPACE}
spec:
  selector: {app: langfuse-minio}
  ports:
    - {name: api, port: 9000, targetPort: 9000}
    - {name: console, port: 9001, targetPort: 9001}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: langfuse-web
  namespace: ${NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels: {app: langfuse-web}
  template:
    metadata:
      labels: {app: langfuse-web}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: web
          image: ${LANGFUSE_WEB_IMAGE}
          ports: [{containerPort: 3000}]
          env:
            - {name: DATABASE_URL, value: "postgresql://langfuse:\$(POSTGRES_PASSWORD)@langfuse-postgres:5432/langfuse"}
            - name: POSTGRES_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-postgres, key: POSTGRES_PASSWORD}}
            - {name: CLICKHOUSE_URL, value: "http://langfuse-clickhouse:8123"}
            - {name: CLICKHOUSE_USER, value: langfuse}
            - name: CLICKHOUSE_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-clickhouse, key: CLICKHOUSE_PASSWORD}}
            - {name: REDIS_CONNECTION_STRING, value: "redis://langfuse-redis:6379"}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_BUCKET, value: langfuse}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT, value: "http://langfuse-minio:9000"}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_REGION, value: us-east-1}
            - name: LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID
              valueFrom: {secretKeyRef: {name: langfuse-minio, key: MINIO_ROOT_USER}}
            - name: LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY
              valueFrom: {secretKeyRef: {name: langfuse-minio, key: MINIO_ROOT_PASSWORD}}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE, value: "true"}
            - {name: NEXTAUTH_URL, value: "http://localhost:3000"}  # overwritten below once the Route exists
            - name: NEXTAUTH_SECRET
              valueFrom: {secretKeyRef: {name: langfuse-core, key: NEXTAUTH_SECRET}}
            - name: SALT
              valueFrom: {secretKeyRef: {name: langfuse-core, key: SALT}}
            - name: ENCRYPTION_KEY
              valueFrom: {secretKeyRef: {name: langfuse-core, key: ENCRYPTION_KEY}}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
---
apiVersion: v1
kind: Service
metadata:
  name: langfuse-web
  namespace: ${NAMESPACE}
spec:
  selector: {app: langfuse-web}
  ports: [{port: 3000, targetPort: 3000}]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: langfuse-worker
  namespace: ${NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels: {app: langfuse-worker}
  template:
    metadata:
      labels: {app: langfuse-worker}
    spec:
      securityContext:
        runAsNonRoot: true
      containers:
        - name: worker
          image: ${LANGFUSE_WORKER_IMAGE}
          env:
            - {name: DATABASE_URL, value: "postgresql://langfuse:\$(POSTGRES_PASSWORD)@langfuse-postgres:5432/langfuse"}
            - name: POSTGRES_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-postgres, key: POSTGRES_PASSWORD}}
            - {name: CLICKHOUSE_URL, value: "http://langfuse-clickhouse:8123"}
            - {name: CLICKHOUSE_USER, value: langfuse}
            - name: CLICKHOUSE_PASSWORD
              valueFrom: {secretKeyRef: {name: langfuse-clickhouse, key: CLICKHOUSE_PASSWORD}}
            - {name: REDIS_CONNECTION_STRING, value: "redis://langfuse-redis:6379"}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_BUCKET, value: langfuse}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT, value: "http://langfuse-minio:9000"}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_REGION, value: us-east-1}
            - name: LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID
              valueFrom: {secretKeyRef: {name: langfuse-minio, key: MINIO_ROOT_USER}}
            - name: LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY
              valueFrom: {secretKeyRef: {name: langfuse-minio, key: MINIO_ROOT_PASSWORD}}
            - {name: LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE, value: "true"}
            - name: SALT
              valueFrom: {secretKeyRef: {name: langfuse-core, key: SALT}}
            - name: ENCRYPTION_KEY
              valueFrom: {secretKeyRef: {name: langfuse-core, key: ENCRYPTION_KEY}}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
EOF

echo "Applying manifests to namespace ${NAMESPACE}..."
oc apply -f "$MANIFEST"

# ---------------------------------------------------------------------------
# 4. Route (per README_langfuse.md: use a Route, not a raw Ingress)
# ---------------------------------------------------------------------------
if ! oc get route langfuse-web -n "$NAMESPACE" >/dev/null 2>&1; then
  oc create route edge langfuse-web \
    --service=langfuse-web \
    --port=3000 \
    --namespace "$NAMESPACE"
fi

ROUTE_HOST="$(oc get route langfuse-web -n "$NAMESPACE" -o jsonpath='{.spec.host}')"

# NEXTAUTH_URL must match the externally reachable URL; patch it now that the
# Route exists (chicken-and-egg: the Route needs the Service, the web
# container ideally wants NEXTAUTH_URL at boot, so we patch post-hoc).
oc set env deployment/langfuse-web -n "$NAMESPACE" NEXTAUTH_URL="https://${ROUTE_HOST}"

echo
echo "== Done =="
echo "Waiting for rollout (this can take a few minutes on first pull)..."
oc rollout status statefulset/langfuse-postgres   -n "$NAMESPACE" --timeout=300s
oc rollout status statefulset/langfuse-clickhouse -n "$NAMESPACE" --timeout=300s
oc rollout status statefulset/langfuse-redis      -n "$NAMESPACE" --timeout=300s
oc rollout status statefulset/langfuse-minio      -n "$NAMESPACE" --timeout=300s
oc rollout status deployment/langfuse-web         -n "$NAMESPACE" --timeout=300s
oc rollout status deployment/langfuse-worker      -n "$NAMESPACE" --timeout=300s

echo
echo "Langfuse is reachable at: https://${ROUTE_HOST}"
echo "Next: create an account + project in the UI, then Settings -> API Keys"
echo "to get LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY for"
echo "phase4_langfuse_prototype.ipynb (set LANGFUSE_HOST=https://${ROUTE_HOST})."
