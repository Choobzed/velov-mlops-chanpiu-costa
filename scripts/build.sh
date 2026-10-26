#!/usr/bin/env bash
# Construit l'image de l'API, taguée avec la version du modèle et le commit git.
# Usage : ./scripts/build.sh      (depuis la racine du projet)
set -euo pipefail

if [ ! -f models/metadata.json ]; then
  echo "models/metadata.json introuvable : lancez d'abord python -m velov.train" >&2
  exit 1
fi

VERSION=$(python3 -c "import json; print(json.load(open('models/metadata.json'))['model_version'])")
COMMIT=$(git rev-parse --short HEAD)
# Du code non commité : le tag le signale, l'image ne correspond à aucun commit.
if [ -n "$(git status --porcelain)" ]; then
  COMMIT="${COMMIT}-dirty"
fi
TAG="${VERSION}-${COMMIT}"

docker build \
  --build-arg VERSION="${VERSION}" \
  --build-arg COMMIT="${COMMIT}" \
  -t "velov-api:${TAG}" \
  -t velov-api:latest \
  .

echo "Image construite : velov-api:${TAG} (aussi taguée velov-api:latest)"
