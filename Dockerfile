# OpenShift-clean container for genai_eval.eval_runner (see CLAUDE.md:
# "OpenShift-clean containers"). No fixed UID — OpenShift assigns an
# arbitrary one at runtime and expects group 0 (root group) to own writable
# paths. WORKDIR is meant to be mounted as an emptyDir in the K8s Job.

FROM python:3.12-slim AS base

# Air-gap defaults (CLAUDE.md: "Air-gap by default"). Overridable at
# `docker run` / K8s Job time; these are safe defaults, not hardcoded secrets.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEEPEVAL_TELEMETRY_OPT_OUT=1 \
    DEEPEVAL_NO_INSPECT_PROMPT=1 \
    DEEPEVAL_DISABLE_DOTENV=1 \
    DEEPEVAL_RESULTS_FOLDER=/workspace/.deepeval \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

WORKDIR /workspace

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src/ src/
COPY datasets/ datasets/
RUN pip install --no-cache-dir --no-deps -e .

# No golden-dataset writes happen at runtime, but DeepEval/MLflow write
# .deepeval/, results/, and cache files under WORKDIR — group-0 + g=u lets
# any arbitrary OpenShift-assigned UID (always in group 0) write there.
RUN mkdir -p /workspace/results /workspace/.deepeval && \
    chgrp -R 0 /workspace && \
    chmod -R g=u /workspace

USER 1001

ENTRYPOINT ["python", "-m", "genai_eval.eval_runner"]
