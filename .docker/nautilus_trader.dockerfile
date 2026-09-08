FROM rust:1.96.0-slim-bookworm@sha256:b5f842fac1e3b4ff718a652a8e0173b62d9403ec826ef4998880b9347db30684 AS rust-toolchain

# Pin to specific digest for supply-chain security (python:3.13-slim as of 2026-04-30)
FROM python@sha256:a0779d7c12fc20be6ec6b4ddc901a4fd7657b8a6bc9def9d3fde89ed5efe0a3d AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    PYO3_PYTHON="/usr/local/bin/python3" \
    PYSETUP_PATH="/opt/pysetup" \
    CARGO_HOME="/usr/local/cargo" \
    RUSTUP_HOME="/usr/local/rustup" \
    BUILD_MODE="release" \
    CC="clang"
ENV PATH="/root/.local/bin:/usr/local/cargo/bin:$PATH" \
    UV_SYSTEM_CERTS=1 \
    CARGO_HTTP_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    GIT_SSL_CAPATH=/etc/ssl/certs
WORKDIR $PYSETUP_PATH

FROM base AS builder

# Install build deps
RUN apt-get update && \
    apt-get install -y curl clang lld git make pkg-config capnproto libcapnp-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Rust
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

# Install UV
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 \
  /uv /uvx /root/.local/bin/

# Install corporate CA cert if provided (for TLS-intercepting proxies such as Zscaler).
# Pass via: --secret id=ca-cert,src=~/.docker/corporate-ca.pem
RUN --mount=type=secret,id=ca-cert,required=false \
    if [ -f /run/secrets/ca-cert ]; then \
        cp /run/secrets/ca-cert /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates; \
    fi

# Install package requirements
COPY uv.lock pyproject.toml build.py ./
RUN uv sync --no-install-package nautilus_trader

# Build nautilus_trader
# Pre-compile Rust crates with the same flags build.py uses so the uv build
# step gets a full Cargo cache hit and does no Rust compilation itself.
# CARGO_BUILD_JOBS caps parallelism — release codegen-units=1 uses 2-4 GB/job;
# 12 default jobs would exceed Docker's ~13 GB limit with a SIGKILL/OOM.
# Overridable via --build-arg for low-RAM hosts (see `make build-base-vps`) —
# default of 4 is unchanged for everyone who doesn't pass the arg.
COPY Cargo.toml ./
COPY Cargo.lock ./
COPY crates ./crates
COPY patches ./patches
ARG CARGO_BUILD_JOBS=4
ENV CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS}
RUN cargo build --lib \
  -p nautilus-backtest \
  -p nautilus-common \
  -p nautilus-core \
  -p nautilus-model \
  -p nautilus-persistence \
  -p nautilus-pyo3 \
  --release \
  --no-default-features \
  --features arrow,cython-compat,extension-module,ffi,high-precision,postgres,python,tracing-bridge

COPY nautilus_trader ./nautilus_trader
COPY README.md ./
RUN uv build --wheel
RUN uv pip install --system dist/*.whl
RUN find /usr/local/lib/python3.13/site-packages -name "*.pyc" -exec rm -f {} \;

# Final application image
FROM base AS application

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/ /usr/local/bin/
