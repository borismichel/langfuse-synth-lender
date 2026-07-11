# Reference Dockerfile for a demo kit.
# PLAN.md Appendix A (base) + section 9.6 (containers run NON-ROOT with no-new-privileges).
# The portal overrides the container CMD per pipeline step / live component; the image
# only needs the `synth` entrypoint installed with the playground extra.
FROM python:3.12-slim

# Non-root user (uid/gid 10001). Section 9.6: job and live containers never run as root.
RUN groupadd --gid 10001 synth \
 && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e '.[playground]'

# Artifact collection dir (PLAN.md section 5.1): kits must write DEMO_SCRIPT.md etc. here.
ENV SYNTH_OUT_DIR=/app/out
RUN mkdir -p /app/out /app/.synth_spool && chown -R synth:synth /app

USER synth

# No default CMD: the portal supplies `synth <step> --config {config}` (+ --set flags)
# at container-create time. Smoke test:
# docker run <image> synth plan -c config/cloud-demo.yaml
