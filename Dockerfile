# Multi-stage build for liric CLI tool
FROM python:3.14-slim AS builder

WORKDIR /app

# Upgrade pip and install build backend dependencies
RUN pip install --no-cache-dir --upgrade pip

# Copy project definition and source files
COPY pyproject.toml README.md ./
COPY liric/ ./liric/

# Create virtual environment and install production dependencies + liric
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir .

# Final minimal production image
FROM python:3.14-slim AS final

# Create non-root system user and group
RUN groupadd -r -g 1000 appuser && \
    useradd -r -g appuser -u 1000 -m -d /home/appuser -s /sbin/nologin appuser

# Create mount points and config symlink for default paths
RUN mkdir -p /music /config /home/appuser/.config && \
    ln -s /config /home/appuser/.config/liric && \
    chown -R appuser:appuser /music /config /home/appuser

# Copy virtual environment from builder stage
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
WORKDIR /config

VOLUME ["/music", "/config"]

ENTRYPOINT ["liric"]
CMD ["--help"]
