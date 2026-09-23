# syntax=docker/dockerfile:1

# Build context is ./backend — see docker-compose.yml.
ARG PYTHON_VERSION=3.14

# ---------------------------------------------------------------------------
# base — shared by both stages so they cannot drift apart.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ---------------------------------------------------------------------------
# builder — compiles wheels into a venv that is copied wholesale into the
# runtime image, so no compiler or header package ships to production.
# ---------------------------------------------------------------------------
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copied alone so the layer is cached until requirements actually change.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

# Unprivileged user: a container that only serves HTTP has no need for root.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app app

COPY --chown=app:app . /app

# Created before dropping privileges, because the process writes to all three
# at runtime (LOG_DIR in particular is opened on the first 500).
RUN mkdir -p /app/logs /app/staticfiles /app/media \
    && chown -R app:app /app/logs /app/staticfiles /app/media \
    && chmod +x /app/docker/entrypoint.sh

USER app

EXPOSE 8000

# Hits the liveness probe through the real WSGI stack, so a wedged worker or a
# broken ALLOWED_HOSTS is caught rather than reported healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Shell form via sh -c so GUNICORN_* stay tunable without rebuilding.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application \
--bind 0.0.0.0:8000 \
--workers ${GUNICORN_WORKERS:-3} \
--threads ${GUNICORN_THREADS:-2} \
--timeout ${GUNICORN_TIMEOUT:-60} \
--graceful-timeout 30 \
--max-requests 1000 --max-requests-jitter 100 \
--access-logfile - --error-logfile - \
--capture-output"]
