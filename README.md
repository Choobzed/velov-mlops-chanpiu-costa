# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

Pour lancer le projet pas à pas, voir [README_EXECUTION.md](README_EXECUTION.md).

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

## Performance (TP5)

> Mesures réalisées par le binôme. Rédaction de cette section et du protocole avec l'aide d'une
> IA (Claude, Anthropic) ; les chiffres viennent de nos propres exécutions de Locust.

### Protocole de mesure

La VM de l'école étant indisponible, **la mesure est locale** : l'API et Locust tournent sur le
même poste. Pour qu'ils ne se disputent pas le processeur, l'API est bornée à **un cœur** par
Docker, et Locust utilise les cœurs restants.

| Élément | Valeur |
|---|---|
| Type de mesure | Locale (API et Locust sur le même poste) |
| Poste | MacBook Air, 8 cœurs (`os.cpu_count()`), Apple M3 |
| Docker | Docker Desktop, moteur 29.6.1 |
| Limite CPU de l'API | 1 cœur (`docker update --cpus 1`) ; PostgreSQL démarré avec l'API |
| Image de l'API | `velov-api:latest` (python:3.12-slim, scikit-learn 1.9.1), modèle `0.1.0` |
| Générateur de charge | Locust 2.47.0, scénario `loadtest/locustfile.py` (réflexion de 0,5 à 1,5 s) |
| Commit mesuré | `ec29ca8` (branche `tp5`) |
| Date | 17 novembre 2026, 11 h 58 (heure de Paris) |
| Conditions | Poste sur secteur, navigateur et IDE lourds fermés ; aucun serveur MLflow local (il tourne sur la VM) |
| Route mesurée | `POST /v1/predict` seule (la ligne `Aggregated` de Locust mélange `/health`) |

Limite connue : l'Apple M3 mélange 4 cœurs rapides (performance) et 4 cœurs économes
(efficacité), et c'est macOS qui choisit lesquels donner à la machine virtuelle de Docker. Le
cœur de l'API n'est donc pas toujours le même, ce qui peut faire varier les mesures : le palier du
coude est mesuré deux fois (un écart de plus de 20 % invaliderait la mesure).

Vérifications avant la mesure : `/ready` répond `200 OK`, avec l'en-tête
`x-process-time-ms: 11.95` (temps passé dans l'API pour cette requête).

### Premier essai : 10 usagers, 1 minute

```bash
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 10 -r 10 -t 1m --only-summary
```

| Route | Requêtes | Erreurs | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---|---|---|---|---|---|---|---|
| `POST /v1/predict` | 530 | 0 (0 %) | 9,07 | 25 | 40 | 110 | 150 |

Lecture :
- **0 % d'erreurs** : le scénario respecte le contrat de l'API.
- **Le débit est cohérent avec la loi de Little** : 10 usagers / (1 s de réflexion moyenne +
  0,026 s de réponse) donnent au plus 9,7 req/s, et Locust mesure 9,85 req/s toutes routes
  confondues. À 10 usagers, c'est la réflexion des usagers qui limite le débit, pas l'API.
- **p95 de 40 ms**, loin du SLO de 200 ms : l'API n'est pas encore chargée. La saturation se
  cherche avec les paliers de l'étape 2.

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
