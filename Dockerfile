FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project --no-dev
COPY liric/ liric/
RUN uv sync --locked --no-dev

FROM python:3.14-slim AS runner

# Create non-root system user and group
RUN groupadd -r -g 1000 appuser && \
    useradd -r -g appuser -u 1000 -m -d /home/appuser -s /sbin/nologin appuser

RUN mkdir -p /music /config /home/appuser/.config && \
    ln -s /config /home/appuser/.config/liric && \
    chown -R appuser:appuser /music /config /home/appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /app /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
WORKDIR /config

VOLUME ["/music", "/config"]

ENTRYPOINT ["liric"]
CMD ["--help"]
