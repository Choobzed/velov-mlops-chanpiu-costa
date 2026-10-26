# Image de serving de l'API Vélo'v, construite en deux étapes (multi-stage).
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------------------
# Étape 1 : l'atelier. On installe les dépendances et le package velov dans un
# environnement virtuel. Rien de cette étape ne part en production, sauf le venv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dépendances d'abord : tant que requirements.txt ne change pas, Docker
# réutilise cette couche en cache, même si le code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Puis le code, installé comme un package dans le venv.
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

# ---------------------------------------------------------------------------
# Étape 2 : l'image qui part en production. Seuls le venv et le modèle sont copiés.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Version et commit, transmis par scripts/build.sh, lisibles avec docker inspect.
ARG VERSION=dev
ARG COMMIT=unknown
LABEL org.opencontainers.image.title="velov-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}"

ENV PATH="/opt/venv/bin:$PATH" \
    MODEL_DIR=/app/models \
    PYTHONUNBUFFERED=1

# Utilisateur sans privilèges : l'API ne tourne pas en root.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY models/ models/
USER appuser

EXPOSE 8000

# /ready et non /health : un conteneur qui ne peut pas prédire n'est pas sain.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"]

# Forme JSON : uvicorn reçoit directement le signal d'arrêt.
CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
