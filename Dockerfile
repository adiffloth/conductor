# Phase 2 sandbox: Hermes Agent core only. No channels, no skills, no cloud
# provider — just enough to prove the container can reach oMLX and cannot see
# the host filesystem (Scenario 4 isolation, verified from day one).
FROM debian:bookworm-slim

# Non-root sandbox user. This container *is* the jail — Hermes's own terminal
# backend is set to `local`, scoped to this already-restricted filesystem, so
# no second (Hermes-managed docker/ssh) sandbox layer is needed on top.
RUN useradd -m -s /bin/bash hermes

# Deps the official Hermes install script needs: curl to fetch it, git to
# clone the repo, build-essential because `uv` may build Python from source,
# ca-certificates for TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

USER hermes
WORKDIR /home/hermes

RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive

ENV PATH="/home/hermes/.local/bin:${PATH}"

# Phase 2 config: local model only, terminal backend local. Copied after
# install so it isn't clobbered by anything the installer scaffolds under
# ~/.hermes (the installer clones the hermes-agent repo itself into
# ~/.hermes/hermes-agent, alongside this config file).
COPY --chown=hermes:hermes docker/hermes-config.yaml /home/hermes/.hermes/config.yaml

# Phase 6: household skill custom code (this repo). Deliberately kept
# outside ~/.hermes — that whole tree is the named volume, so anything
# placed there only reaches a *fresh* volume, not one that already exists.
# Living outside it means a plain image rebuild actually updates this code.
# Runs on Hermes's own venv interpreter (already has the google-api-python-
# client / mcp packages installed — see project_plan.md Phase 6).
COPY --chown=hermes:hermes household/ /home/hermes/household/

CMD ["/bin/bash"]
