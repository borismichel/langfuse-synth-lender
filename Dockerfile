# Reference Dockerfile for a demo kit.
# PLAN.md Appendix A (base) + section 9.6 (containers run NON-ROOT with no-new-privileges).
# The portal overrides the container CMD per pipeline step / live component; the image
# only needs the `synth` entrypoint installed with the playground extra.
FROM python:3.12-slim

# git: needed at build time to fetch the git-pinned langfuse-synth-core runtime dep
# (Ring 1, #31/#32). The repo is PUBLIC, so this is a plain HTTPS fetch — no build secret.
# Installed as its own early layer (independent of the source COPY) for cache reuse.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (uid/gid 10001). Section 9.6: job and live containers never run as root.
RUN groupadd --gid 10001 synth \
 && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth

WORKDIR /app
COPY . .
# Fetches the pinned public langfuse-synth-core over HTTPS during the build (it is a
# runtime dependency in pyproject.toml). The [playground] extra adds the live-UI deps;
# the [dev] extra (pytest + authoring golden gate) is deliberately NOT installed here.
RUN pip install --no-cache-dir -e '.[playground]'

# Artifact collection dir (PLAN.md section 5.1): kits must write DEMO_SCRIPT.md etc. here.
ENV SYNTH_OUT_DIR=/app/out
RUN mkdir -p /app/out /app/.synth_spool && chown -R synth:synth /app

USER synth

# No default CMD: the portal supplies `synth <step> --config {config}` (+ --set flags)
# at container-create time. Smoke test:
# docker run <image> synth plan -c config/cloud-demo.yaml
