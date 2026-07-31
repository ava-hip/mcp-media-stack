# syntax=docker/dockerfile:1
#
# Image PUBLIQUE (GHCR) : elle ne contient AUCUN secret ni URL privée.
# Toutes les variables (SONARR_URL, *_API_KEY, QUI_URL, PROWLARR_URL, JELLYFIN_URL…)
# sont fournies UNIQUEMENT au runtime (env_file / environment / docker run -e).

# ── Build : uv résout les dépendances dans /app/.venv ────────────────────────
FROM python:3.11-slim AS builder

# uv n'est utile qu'ici : le stage runtime ne reçoit que le venv produit.
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Manifestes d'abord : ce layer reste en cache tant que pyproject/uv.lock ne bougent
# pas, donc un rebuild qui ne touche que le code saute toute l'install des deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Puis le code — seule chose qu'un changement applicatif invalide.
COPY src ./src
RUN uv sync --frozen --no-dev


# ── Runtime : python + le venv + le code, en non-root ────────────────────────
FROM python:3.11-slim AS runtime

# Pas de secret ici, uniquement la config de transport (surchargeable au runtime).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8080

RUN useradd --create-home --uid 10001 app

WORKDIR /app

# Même chemin que le builder : les scripts du venv contiennent des chemins absolus.
# uv installe le projet en editable, d'où la copie de src/ à côté.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src

USER app

EXPOSE 8080

CMD ["python", "-m", "media_mcp"]
