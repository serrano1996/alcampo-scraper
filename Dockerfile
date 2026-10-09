# syntax=docker/dockerfile:1

# Spec 005. Both stages use the same base on purpose: the venv's python is a
# symlink to the base image's interpreter, so it must exist in the runtime too.
# 3.11 is the minimum declared in pyproject.toml (spec-D1).

# ---- builder: production dependencies only, into a self-contained venv ----
FROM python:3.11-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# Exact versions checked by hash (spec 014 RF-3), in their own layer: it is
# rebuilt only when the lock changes, not on every code change. The build
# backend (setuptools) is hashed too: otherwise building the package below
# would fetch it unhashed into an isolated env (spec 015 RF-6).
COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock -r requirements-build.lock
COPY pyproject.toml ./
COPY app/ ./app/
# The project alone (`--no-deps`), built with the setuptools just installed
# (`--no-build-isolation`): nothing is resolved nor fetched again (plan-D2). Plain
# `.`, no [dev] extra: pytest, ruff, respx and fakeredis never get in. The
# wheel already contains app/, so the code travels inside the venv (plan-D1).
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

# ---- runtime: only the venv, run by an unprivileged user ----
FROM python:3.11-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Numeric uid so orchestrators enforcing runAsNonRoot can check it (plan-D2).
# The venv stays owned by root: the process cannot modify its own code.
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER 10001
EXPOSE 8000

# Python + httpx (already installed) instead of curl (RNF-2). 127.0.0.1, not
# localhost: localhost may resolve to ::1 and uvicorn listens on IPv4 only.
# trust_env=False: a deploy-time HTTP_PROXY must not intercept the probe (plan-D4).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx, sys; sys.exit(httpx.get('http://127.0.0.1:8000/health', timeout=3, trust_env=False).status_code != 200)"]

# Exec form: uvicorn is PID 1 and gets SIGTERM for a clean lifespan shutdown.
# No --reload (plan-D8). No access log: the request middleware already logs
# every request with its request id (spec-D3); override `command` to re-enable.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
