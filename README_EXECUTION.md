# Exécuter le projet Vélo'v

Ce document explique comment lancer le projet sur son poste, commande par commande.
Pour la description du projet lui-même, voir le [README](README.md). Pour une version
détaillée avec le dépannage, voir [docs/guide_commandes.md](docs/guide_commandes.md).

> Rédigé avec l'aide d'une IA (Claude, Anthropic), pour s'assurer de la clarté des
> explications. Les commandes ont été vérifiées sur le projet.

## Prérequis

- Python 3.11 ou plus récent (3.12 recommandé)
- Docker Desktop, lancé
- git

Toutes les commandes se lancent depuis la racine du projet.

## 1. Installer (une seule fois)

```bash
python -m venv .venv                  # crée un environnement Python isolé pour le projet
source .venv/bin/activate             # l'active (Windows : .venv\Scripts\activate)
pip install -r requirements-dev.txt   # installe les bibliothèques (API, tests, outils)
pip install -e .                      # rend le code du projet (package velov) importable
```

Lors des sessions suivantes, seule la commande `source .venv/bin/activate` est nécessaire.

## 2. Préparer les données et le modèle

```bash
python -m velov.data     # génère un historique simulé des stations : data/velov_history.csv
python -m velov.train    # entraîne le modèle : models/model.joblib et models/metadata.json
pytest                   # lance les tests pour vérifier que tout fonctionne
```

## 3. Configurer les mots de passe (une seule fois)

```bash
cp secrets/postgres_password.example secrets/postgres_password   # mot de passe PostgreSQL
cp secrets/pgadmin_password.example secrets/pgadmin_password     # mot de passe pgAdmin
chmod 600 secrets/postgres_password secrets/pgadmin_password     # lisibles par vous seul
```

Remplacez ensuite le contenu des deux fichiers par vos propres mots de passe, et créez un
fichier `.env` à la racine contenant :

```
POSTGRES_DB=velov
POSTGRES_USER=velov
API_PORT=8000
PGADMIN_PORT=5050
```

Ces fichiers ne sont jamais commités.

## 4. Lancer le projet

```bash
./scripts/build.sh       # construit l'image Docker de l'API, taguée version du modèle + commit
docker compose up -d     # démarre l'API, PostgreSQL et pgAdmin en arrière-plan
docker compose ps        # affiche l'état des services ; l'API passe "healthy" en ~30 s
```

## 5. Utiliser l'API

```bash
curl http://127.0.0.1:8000/health    # l'API répond-elle ?
curl http://127.0.0.1:8000/ready     # le modèle et la base sont-ils prêts ?
```

Demander une prédiction (nombre de vélos dans une heure à la station 3) :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

La documentation interactive de l'API est disponible sur http://127.0.0.1:8000/docs.

## 6. Retrouver les prédictions en base

Chaque prédiction est enregistrée dans la table `predictions` de PostgreSQL :

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT * FROM predictions ORDER BY id DESC LIMIT 5;"'
```

Interface graphique : pgAdmin sur http://localhost:5050 (email `admin@velov.com`, mot de
passe dans `secrets/pgadmin_password`).

## 7. Arrêter

```bash
docker compose down      # arrête les services, les prédictions sont conservées
docker compose down -v   # arrête et supprime aussi les données (irréversible)
```

## 8. Collecter les vraies données Vélo'v (TP3 : ingestion bronze)

### Pourquoi

La Métropole de Lyon publie l'état des stations Vélo'v en temps réel, au format standard
GBFS, mais uniquement l'état **actuel** : aucun historique. Pour entraîner le modèle, on
"photographie" donc le flux régulièrement et on garde chaque photo dans une base DuckDB.
C'est la couche **bronze** : les données brutes, telles qu'elles arrivent, jamais modifiées.

### La commande

```bash
python -m velov.ingest                          # télécharge le flux et l'ajoute à data/velov.duckdb
python -m velov.ingest --db autre_fichier.duckdb   # même chose dans une autre base
```

Le script affiche le nombre de stations reçues et le nombre d'observations réellement
ajoutées. Lancé deux fois de suite, le second passage ajoute 0 ligne (ou presque) : seules
les stations qui ont envoyé un nouveau relevé entre-temps sont ajoutées.

### Comment fonctionne le script (`src/velov/ingest.py`)

1. **`fetch_json`** télécharge une adresse web et transforme la réponse JSON en
   dictionnaire Python. C'est l'équivalent de `curl`, avec un délai maximal de 10 secondes.
2. **`find_feed_url`** lit le sommaire du flux (`gbfs.json`) et y cherche l'adresse du flux
   `station_status`, au lieu de l'écrire en dur : si l'adresse change, le script suit.
3. **`to_rows`** transforme chaque station en une ligne de la table, dans l'ordre des
   colonnes. Un champ absent devient une valeur vide, et la liste des types de vélos
   (mécanique, électrique) est gardée telle quelle, en texte JSON.
4. **`ingest`** crée la table si elle n'existe pas, insère les lignes et compte celles qui
   ont été ajoutées.

### La table `bronze_station_status`

Elle reprend les champs du flux avec leurs noms et leurs types d'origine (par exemple
`last_reported` en secondes Unix, `station_id` en texte), plus une colonne ajoutée :
`loaded_at`, la date et l'heure (UTC) du chargement, identique pour toutes les lignes d'un
même passage. Les transformations (renommage, conversion des dates, détail
mécanique / électrique) se feront dans la couche silver, avec dbt.

### Pourquoi il n'y a jamais de doublon (idempotence)

Une observation est identifiée par le couple **(`station_id`, `last_reported`)** : tant
qu'une station n'a pas envoyé de nouveau relevé, chaque téléchargement renvoie pour elle le
même couple. Ce couple est la clé primaire de la table, et l'insertion utilise
`ON CONFLICT DO NOTHING` : une observation déjà connue est ignorée, sans erreur. On ignore
plutôt que de remplacer, pour que le bronze reste immuable et que `loaded_at` garde la date
du **premier** chargement.

### Consulter la base

Le client DuckDB s'installe une fois (`brew install duckdb` sur Mac), puis :

```bash
duckdb data/velov.duckdb       # ouvre une invite SQL (quitter : .quit)
```

```sql
SELECT count(*) FROM bronze_station_status;
SELECT * FROM bronze_station_status ORDER BY loaded_at DESC LIMIT 5;
```

Un fichier DuckDB ne peut être ouvert que par un seul programme à la fois en écriture :
quittez le client avant de lancer `python -m velov.ingest`, sinon le script échoue avec
une erreur de verrou (`Could not set lock on file`).

## En cas de problème

```bash
docker compose logs api  # affiche les messages de l'API (erreurs de démarrage, modèle absent...)
docker compose logs db   # affiche les messages de PostgreSQL
```

Si `python -m velov...` répond `No module named 'velov'` ou `No module named 'duckdb'`,
le terminal n'utilise pas l'environnement du projet. Vérifiez avec `which python` : le
chemin doit se terminer par `.venv/bin/python`. Avec Anaconda, désactivez d'abord son
environnement (`conda deactivate`), puis réactivez celui du projet
(`source .venv/bin/activate`).
