# Image du serveur MLflow (TP4, étape 1).
#
# Pourquoi une image maison : l'image officielle de MLflow n'embarque pas de pilote
# PostgreSQL. Sans lui, le serveur ne peut pas utiliser la base mlflow-db.
# On part donc d'un Python léger et on installe MLflow et le pilote, versions épinglées.
# MLflow est à la même version que dans requirements-dev.txt : le client (velov.train)
# et le serveur parlent la même version de l'API.
#
# Le pilote est psycopg 3 (paquet "psycopg"), pas l'ancien psycopg2 : la version de
# SQLAlchemy installée avec MLflow 3.16.1 l'utilise par défaut pour les URL postgresql://.
# Avec psycopg2 seul, le serveur s'arrête au démarrage (No module named 'psycopg').
# Même version que celle de l'API (requirements.txt).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Télémétrie de MLflow désactivée : rien n'est envoyé hors du réseau de l'école.
    MLFLOW_DISABLE_TELEMETRY=true \
    DO_NOT_TRACK=true

RUN pip install --no-cache-dir mlflow==3.16.1 "psycopg[binary]==3.2.9"

# Utilisateur sans privilèges, comme pour l'image de l'API (TP2).
# Le dossier des artefacts lui appartient : le volume Docker monté dessus
# reprend ces droits à sa création.
RUN useradd --create-home --uid 10001 mlflow \
    && mkdir /mlartifacts \
    && chown mlflow:mlflow /mlartifacts
USER mlflow
WORKDIR /home/mlflow

EXPOSE 5000
