# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

## Progression par session

Chaque étape du cours correspond à un tag git. Absent ou bloqué : repartez du tag de fin de la session précédente.

| Tag | Contenu |
|---|---|
| `s1-start` | Données simulées, features, entraînement. API à compléter (TP1) |
| `s1-end` | Modèle v0 servi par FastAPI, tests pytest |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git. Docker sera nécessaire en S2.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne et écrit models/model.joblib + models/metadata.json
python -m velov.train --mlflow       # idem + journalisation MLflow (puis : mlflow ui)
pytest                               # tests
uvicorn velov.api.main:app --reload  # API sur http://127.0.0.1:8000/docs (--reload : en dev uniquement)
```

Exemple d'appel :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Le `timestamp` doit porter un fuseau (`+02:00`, `Z`...) : sans fuseau, l'API répond 422.
Les instants sont renvoyés et journalisés en UTC (`"target_timestamp": "2026-10-06T07:00:00Z"`).

## Démarrer l'API et PostgreSQL avec Docker Compose

Générez le modèle une fois avant de construire l'image (voir les commandes de démarrage ci-dessus), puis configurez le mot de passe local :

```bash
cp secrets/postgres_password.example secrets/postgres_password
cp secrets/pgadmin_password.example secrets/pgadmin_password
# Remplacez le contenu de secrets/postgres_password par un mot de passe local.
# Remplacez aussi le contenu de secrets/pgadmin_password.
chmod 600 secrets/postgres_password secrets/pgadmin_password
docker compose up --build
```

L'API est disponible sur `http://localhost:8000/docs` et pgAdmin sur
`http://localhost:5050` (email `admin@velov.com`, mot de passe dans
`secrets/pgadmin_password`). Dans pgAdmin, ajoutez un serveur avec le nom
`Vélo'v`, l'hôte `db`, le port `5432`, la base `velov`, l'utilisateur `velov` et le
mot de passe contenu dans `secrets/postgres_password`.

Les prédictions sont stockées dans la table PostgreSQL `predictions`; pour les consulter
depuis le terminal :

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, station_id, observation_timestamp, target_timestamp, predicted_bikes, model_version FROM predictions ORDER BY id DESC;"'
```

Pour arrêter les services tout en conservant les données, exécutez `docker compose down`.
Les paramètres sont lus depuis `.env` et le mot de passe depuis un fichier secret ignoré par git;
ne commitez ni `.env`, ni `.env.local`, ni les fichiers sans suffixe `.example` dans `secrets/`.
Sur un clone neuf, créez `.env` avec `POSTGRES_DB=velov`, `POSTGRES_USER=velov`,
`API_PORT=8000` et `PGADMIN_PORT=5050`. Changez les identifiants d'exemple
avant tout déploiement non local.

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier.


## Structure

```
src/velov/
  data.py          simulateur de données (remplacé par l'open data en S3, même schéma)
  features.py      features partagées entraînement / API (anti training-serving skew)
  train.py         entraînement, baseline, artefact + metadata.json, option MLflow
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
tests/             pytest (features, API)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
