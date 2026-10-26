# Guide des commandes du projet Vélo'v

Ce guide reprend le projet depuis zéro : à quoi il sert, comment il est organisé, et toutes
les commandes pour générer les données, entraîner le modèle, faire une prédiction et la
stocker en base. Toutes les commandes se lancent depuis la racine du projet
(`velov-mlops-student`), sauf mention contraire.

## 1. Le projet en une minute

Objectif : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles dans une heure.

Le chemin d'une donnée, de bout en bout :

```
velov.data          velov.train                 API FastAPI               PostgreSQL
(génère un      ->  (entraîne le modèle)   ->   (charge le modèle,   ->   (table predictions :
 historique)         models/model.joblib         répond aux requêtes)      chaque prédiction
 data/*.csv          models/metadata.json        /ready, /v1/predict       est enregistrée)
```

Les fichiers importants :

| Fichier | Rôle |
|---|---|
| `src/velov/data.py` | Simulateur : fabrique un historique de stations (faux mais réaliste) |
| `src/velov/features.py` | Calcule les variables du modèle (heure, jour, taux d'occupation...). Partagé entre entraînement et API |
| `src/velov/train.py` | Entraîne le modèle, le compare à une baseline, écrit le modèle et ses métadonnées |
| `src/velov/api/schemas.py` | Le contrat Pydantic : ce que l'API accepte et renvoie |
| `src/velov/api/main.py` | L'API : `/health`, `/ready`, `/v1/predict` |
| `src/velov/api/database.py` | Crée la table `predictions` et y enregistre chaque prédiction |
| `Dockerfile` | La recette de l'image de l'API (deux étapes : builder puis runtime) |
| `compose.yaml` | Lance ensemble l'API, PostgreSQL et pgAdmin |
| `scripts/build.sh` | Construit l'image, taguée avec la version du modèle et le commit |

## 2. Préparer l'environnement (une fois par session de travail)

```bash
cd ~/Desktop/cours/ynov20262027/iacloud/velov-mlops-student
source .venv/bin/activate
```

Le préfixe `(.venv)` apparaît dans le terminal : les commandes `python` et `pip` utilisent
maintenant l'environnement du projet. Pour en sortir : `deactivate`.

Si le dossier `.venv` n'existe pas (clone neuf), on le crée et on installe tout :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # dépendances (API + outils de dev : pytest, ruff, mlflow)
pip install -e .                      # rend le package velov importable
```

## 3. Générer les données

```bash
python -m velov.data
```

Résultat : `data/velov_history.csv`, 90 jours d'historique pour 20 stations.
Options utiles : `--days 30`, `--stations 5`, `--seed 42` (même seed = mêmes données).

Pour jeter un œil au fichier :

```bash
head -5 data/velov_history.csv
```

## 4. Entraîner le modèle

```bash
python -m velov.train
```

Résultat dans `models/` :

- `model.joblib` : le modèle entraîné ;
- `metadata.json` : sa fiche d'identité (version, date, métriques, empreinte SHA-256).

Options : `--version 0.2.0` pour numéroter une nouvelle version, `--mlflow` pour journaliser
l'entraînement dans MLflow (vu en S4).

Lire les métriques :

```bash
python -c "import json; print(json.load(open('models/metadata.json'))['metrics'])"
```

`mae_model` doit être plus petit que `mae_baseline_persistence` : le modèle fait mieux que
"prédire qu'il y aura autant de vélos dans une heure qu'aujourd'hui".

## 5. Vérifier le code

```bash
pytest           # lance les tests (API, features)
ruff check .     # vérifie le style et les erreurs courantes
```

## 6. Lancer l'API sans Docker (mode développement)

```bash
uvicorn velov.api.main:app --reload
```

`--reload` relance l'API à chaque modification du code : pratique en dev, jamais en production.
L'API est sur `http://127.0.0.1:8000`, et sa documentation interactive sur
`http://127.0.0.1:8000/docs`. Sans Docker, PostgreSQL n'est pas configuré : la prédiction
fonctionne mais n'est pas enregistrée. On arrête avec `Ctrl + C`.

Dans un second terminal :

```bash
curl http://127.0.0.1:8000/health      # le processus répond : {"status":"ok"}
curl http://127.0.0.1:8000/ready       # le modèle est chargé : {"status":"ready",...}
```

Faire une prédiction :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Réponse attendue : `target_timestamp` vaut l'heure demandée plus une heure, en UTC, et
`predicted_bikes` est la prédiction. Un champ inconnu, un timestamp sans fuseau ou plus de
vélos que de places donnent une erreur 422 : c'est le contrat Pydantic qui refuse.

## 7. Construire l'image Docker

Le modèle doit exister dans `models/` avant le build (étape 4), car il est copié dans l'image.

```bash
./scripts/build.sh
```

Le script lit la version du modèle dans `metadata.json`, le commit git courant, et construit
l'image sous deux noms : `velov-api:<version>-<commit>` et `velov-api:latest`. Si du code
n'est pas commité, le tag se termine par `-dirty` : l'image ne correspond alors à aucun commit.

```bash
docker images velov-api                                   # les images et leur taille
docker inspect velov-api:latest --format '{{json .Config.Labels}}'   # version et commit
```

Lancer l'image seule, sans base de données :

```bash
docker run -d --name velov -p 8000:8000 velov-api:latest
docker ps                     # STATUS : "healthy" après environ 30 secondes
docker logs velov             # ce que l'API affiche
docker rm -f velov            # arrêter et supprimer le conteneur
```

## 8. Lancer la stack complète : API + PostgreSQL + pgAdmin

### Une fois : les mots de passe et le fichier .env

```bash
cp secrets/postgres_password.example secrets/postgres_password
cp secrets/pgadmin_password.example secrets/pgadmin_password
chmod 600 secrets/postgres_password secrets/pgadmin_password
```

Remplace le contenu des deux fichiers par tes propres mots de passe. Le fichier `.env` à la
racine contient `POSTGRES_DB=velov`, `POSTGRES_USER=velov`, `API_PORT=8000` et
`PGADMIN_PORT=5050`. Ni `.env` ni les fichiers de `secrets/` ne sont commités.

### Démarrer

```bash
./scripts/build.sh            # construit l'image velov-api:latest
docker compose up -d          # démarre les trois services en arrière-plan
docker compose ps             # état des services (db doit être "healthy")
docker compose logs -f api    # suivre les logs de l'API (Ctrl + C pour quitter)
```

Variante en une commande, qui reconstruit l'image sans passer par le script (tag `latest`,
sans version ni commit) : `docker compose up -d --build`.

### Faire une prédiction et la retrouver en base

Envoie la même requête `curl -X POST .../v1/predict` qu'à l'étape 6, puis :

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, station_id, observation_timestamp, target_timestamp, predicted_bikes, model_version FROM predictions ORDER BY id DESC LIMIT 5;"'
```

Avec une interface graphique : pgAdmin sur `http://localhost:5050` (email `admin@velov.com`,
mot de passe dans `secrets/pgadmin_password`), puis ajouter un serveur avec l'hôte `db`, le
port `5432`, la base `velov`, l'utilisateur `velov` et le mot de passe de
`secrets/postgres_password`.

### Vérifier que les données survivent à un redémarrage (persistance)

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM predictions;"'
docker compose down           # arrête et supprime les conteneurs, garde le volume
docker compose up -d
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM predictions;"'
```

Le nombre de lignes doit être le même avant et après : les données vivent dans le volume
`postgres_data`, pas dans le conteneur.

### Arrêter

```bash
docker compose down           # arrête, conserve les données
docker compose down -v        # arrête ET supprime les volumes : toutes les prédictions sont perdues
```

## 9. Dépannage rapide

| Symptôme | Cause probable | Commande pour vérifier |
|---|---|---|
| `/ready` répond 503 "Modèle indisponible" | `models/` vide ou modèle modifié (empreinte invalide) | `docker compose logs api` |
| `/ready` répond 503 "Base de données indisponible" | PostgreSQL pas démarré ou mauvais mot de passe | `docker compose ps`, `docker compose logs db` |
| Conteneur en `Exited` | Erreur au démarrage | `docker logs <nom>` (lire la dernière ligne) |
| `Conflict. The container name ... is already in use` | Un ancien conteneur porte déjà ce nom | `docker rm -f <nom>` |
| `port is already allocated` | Le port 8000 est déjà pris (un uvicorn local ?) | Arrêter l'autre service, ou changer `API_PORT` dans `.env` |
| `curl: Connection refused` | Personne n'écoute : API arrêtée | `docker compose ps` |
| `No module named 'velov'` en local | Package non installé dans le venv | `pip install -e .` |

## 10. Rendre son travail (git)

```bash
git status                    # ce qui a changé
git add .
git commit -m "Message clair"
git push                      # la première fois sur une branche : git push -u origin <branche>
git tag tp3 && git push origin tp3   # en fin de TP, si le tag est demandé
```

En mode "IA déclarée", indique dans le message de commit l'outil utilisé, ce que tu lui as
demandé, et ce que tu as vérifié ou corrigé.
