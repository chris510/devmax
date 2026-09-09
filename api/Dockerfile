FROM python:3.12.14-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Keep the base interpreter aligned with requires-python. Falling back to a
# uv-managed interpreter under /root makes the non-root runtime unbootable.

COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY . .
RUN uv sync --frozen --no-dev --no-cache

# Runtime code and the virtual environment are read-only. A compromised request
# therefore does not get root inside the container or a writable application tree.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080
USER app:app
# Shell form so $PORT expands: Railway assigns the port at runtime and expects the
# app to bind it. Falls back to 8080 for a plain `docker run`.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
