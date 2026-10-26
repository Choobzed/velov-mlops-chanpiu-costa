# Exécuter le projet Vélo'v

Ce document explique comment lancer le projet sur son poste, commande par commande.
Pour la description du projet lui-même, voir le [README](README.md). Pour une version
détaillée avec le dépannage, voir [docs/guide_commandes.md](docs/guide_commandes.md).

> Rédigé avec l'aide d'une IA (Claude), pour s'assurer de la clarté des
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

## En cas de problème

```bash
docker compose logs api  # affiche les messages de l'API (erreurs de démarrage, modèle absent...)
docker compose logs db   # affiche les messages de PostgreSQL
```
