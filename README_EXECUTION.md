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

### Trois sources, trois tables

| Source | Ce qu'elle décrit | Change | Table bronze |
|---|---|---|---|
| Vélo'v, flux GBFS `station_status` | **Comment va** la station : vélos disponibles, places libres, en service ou non | Plusieurs fois par heure | `bronze_station_status` (un "relevé" par ligne) |
| Vélo'v, flux GBFS `station_information` | **Qui est** la station : nom, position GPS, capacité | Très rarement | `bronze_station_information` (une "fiche" par ligne) |
| Open-Meteo, météo actuelle au centre de Lyon | Température, précipitations | Toutes les 15 minutes | `bronze_weather` (une mesure par ligne) |

Le modèle a besoin de la température et de la pluie, que Vélo'v ne fournit pas. L'énoncé ne
précisant pas de source météo, on utilise Open-Meteo : service gratuit, sans clé d'API.
Une panne du service météo n'empêche pas la collecte des stations (le script le signale et
continue).

### La commande

```bash
python -m velov.ingest                             # collecte les deux flux dans data/velov.duckdb
python -m velov.ingest --db autre_fichier.duckdb   # même chose dans une autre base
```

La base par défaut est toujours `data/velov.duckdb` à la racine du projet, quel que soit le
dossier depuis lequel on lance la commande (le chemin est calculé depuis l'emplacement du
script). Le script affiche, pour chaque source, le nombre de lignes reçues et le nombre de
lignes réellement ajoutées. Lancé deux fois de suite, le second passage ajoute 0 ligne
(ou presque) :

```
INFO station_status : 466 lignes reçues, 466 nouvelles ajoutées dans .../data/velov.duckdb
INFO station_information : 466 lignes reçues, 466 nouvelles ajoutées dans .../data/velov.duckdb
INFO station_status : 466 lignes reçues, 0 nouvelles ajoutées dans .../data/velov.duckdb
INFO station_information : 466 lignes reçues, 0 nouvelles ajoutées dans .../data/velov.duckdb
INFO weather : 1 lignes reçues, 0 nouvelles ajoutées dans .../data/velov.duckdb
```

### Ce qui se passe à chaque exécution

```
1. Heure du passage      loaded_at = maintenant (UTC), une seule fois pour tout le passage
        |
2. Sommaire              GET gbfs.json  ->  liste des flux disponibles
        |
3. Adresses des flux     on y cherche les url de "station_status" et "station_information"
        |
4. Données               GET des deux flux  ->  environ 466 relevés et 466 fiches
                         GET Open-Meteo     ->  la mesure météo actuelle
        |
5. Mise en forme         chaque station -> une ligne de table (+ loaded_at)
        |
6. Base DuckDB           crée les tables si elles n'existent pas
                         relevés : un relevé déjà connu est ignoré
                         fiches  : une fiche identique à la dernière version est ignorée
                         météo   : une mesure déjà connue (même heure) est ignorée
        |
7. Bilan                 pour chaque flux : "N lignes reçues, M nouvelles ajoutées"
```

Le script fait quatre requêtes HTTP (l'équivalent de quatre `curl`), transforme les réponses en
lignes de tableau et les **ajoute** dans le bronze. Il ne modifie ni ne supprime jamais une ligne.

### Construire l'historique

Une exécution donne une seule "photo" de l'état des stations. Pour obtenir un historique,
le script doit être relancé régulièrement (toutes les 5 minutes par exemple) par un
planificateur, comme `cron` sur la VM. À chaque passage, seules les nouveautés ajoutent une
ligne : la base grossit petit à petit, sans doublon.

### Comment fonctionne le script (`src/velov/ingest.py`)

1. **`fetch_json`** télécharge une adresse web et transforme la réponse JSON en
   dictionnaire Python. C'est l'équivalent de `curl`, avec un délai maximal de 10 secondes.
2. **`find_feed_url`** lit le sommaire du flux (`gbfs.json`) et y cherche l'adresse d'un flux,
   au lieu de l'écrire en dur : si l'adresse change, le script suit.
3. **`to_rows`**, **`to_information_rows`** et **`to_weather_rows`** transforment chaque
   élément reçu en une ligne de table, dans l'ordre des colonnes. Un champ absent devient
   une valeur vide.
4. **`ingest`** ouvre la base, crée les tables si besoin, insère sans doublon et compte les
   lignes ajoutées.

### Les relevés : `bronze_station_status`

Les champs du flux avec leurs noms et leurs types d'origine (`last_reported` en secondes
Unix, `station_id` en texte, la liste des types de vélos en texte JSON), plus `loaded_at`,
la date et l'heure (UTC) du chargement.

**Pas de doublon.** Un relevé est identifié par le couple **(`station_id`, `last_reported`)** :
`last_reported` est l'heure du dernier relevé **de la station**, fournie par Vélo'v. Tant que
la station n'a rien envoyé de neuf, chaque téléchargement renvoie le même couple. Ce couple
est la clé primaire, et l'insertion utilise `ON CONFLICT DO NOTHING` : un relevé déjà connu
est ignoré, sans erreur. On ignore plutôt que de remplacer, pour que le bronze reste
immuable et que `loaded_at` garde la date du **premier** chargement.

### Les fiches : `bronze_station_information`

Les champs utiles extraits en colonnes (`station_id`, `name`, `lat`, `lon`, `capacity`), la
fiche complète telle que reçue (`raw`, texte JSON) et `loaded_at`. La table garde **une ligne
par version** de chaque fiche : clé primaire **(`station_id`, `loaded_at`)**.

**Le choix : comparer avec la dernière version connue.** Contrairement aux relevés, une fiche
ne contient aucune heure propre à la station : rien n'indique qu'elle a changé. Les solutions
envisagées :

| Méthode | Fiche inchangée | Fiche modifiée | Verdict |
|---|---|---|---|
| Clé `station_id` seule | Ignorée | **Ignorée aussi** : la nouvelle capacité serait perdue | Écartée |
| Clé `(station_id, loaded_at)` sans comparaison | **Réinsérée** à chaque passage | Insérée | Écartée : des milliers de doublons par jour |
| Clé `(station_id, last_updated)`, l'heure de génération du fichier par Vélo'v | **Réinsérée** dès que le fichier est régénéré | Insérée | Écartée : même problème |
| Empreinte (hash) du contenu dans la clé | Ignorée | Insérée | Écartée : une station qui revient à une ancienne configuration (17, puis 20, puis 17 bornes) ne serait pas détectée, et le mécanisme est moins lisible |
| **Comparaison avec la dernière version connue** | Ignorée | Insérée | **Retenue** |

Concrètement, à chaque passage :

1. les fiches reçues sont posées dans une table temporaire (`incoming_station_information`) ;
2. une requête SQL insère chaque fiche reçue **sauf** s'il existe déjà, pour sa station, une
   dernière version identique sur tous les champs (`name`, `lat`, `lon`, `capacity` et `raw`,
   qui couvre aussi les champs non extraits en colonnes) ;
3. une fiche nouvelle ou modifiée est **ajoutée** avec son `loaded_at` ; l'ancienne version
   n'est jamais écrasée.

La comparaison utilise `IS NOT DISTINCT FROM` plutôt que `=` : en SQL, `NULL = NULL` ne vaut
pas "vrai" mais "inconnu", et une station sans nom serait sinon réinsérée à chaque passage.

Le bronze garde ainsi l'historique complet des fiches ; c'est le silver
(`silver_station_information`) qui ne montre que la version la plus récente de chaque station.
Le `loaded_at` de cette version est sa date de dernière modification.

### La météo : `bronze_weather`

L'heure de la mesure (`observed_time`, en GMT, telle que fournie), la température
(`temperature_2m`), les précipitations (`precipitation`), la réponse complète (`raw`) et
`loaded_at`. Comme pour les relevés, l'heure de la mesure est une clé naturelle : Open-Meteo
ne publie une nouvelle mesure que toutes les 15 minutes, et une mesure déjà enregistrée est
ignorée (`ON CONFLICT DO NOTHING`).

### Consulter la base

Le client DuckDB s'installe une fois (`brew install duckdb` sur Mac), puis :

```bash
duckdb data/velov.duckdb       # ouvre une invite SQL (quitter : .quit)
```

```sql
SELECT count(*) FROM bronze_station_status;
SELECT * FROM bronze_station_status ORDER BY loaded_at DESC LIMIT 5;
SELECT station_id, name, capacity, loaded_at FROM bronze_station_information ORDER BY station_id, loaded_at;
```

Un fichier DuckDB ne peut être ouvert que par un seul programme à la fois en écriture :
quittez le client avant de lancer `python -m velov.ingest` ou `dbt`, sinon la commande
échoue avec une erreur de verrou (`Could not set lock on file`).

## 9. Transformer et tester les données (TP3 : dbt, silver)

dbt exécute des transformations SQL dans DuckDB, dans le bon ordre, puis les teste. Le projet
dbt est dans le dossier `dbt/`, et **les commandes dbt se lancent depuis ce dossier** (le
fichier `dbt/profiles.yml` y indique où se trouve la base : `../data/velov.duckdb`).

```bash
cd dbt
dbt debug     # vérifie la configuration et la connexion à la base
dbt build     # construit les modèles silver, puis lance tous les tests
```

### Les modèles silver

| Modèle | Lit | Fait |
|---|---|---|
| `silver_station_status` | `bronze_station_status` | Renomme les colonnes, convertit `last_reported` en date (`reported_at`), sépare les vélos mécaniques et électriques, retire les lignes sans `station_id` |
| `silver_station_information` | `bronze_station_information` | Ne garde que la version la plus récente de chaque fiche : une ligne par station |
| `silver_weather` | `bronze_weather` | Convertit l'heure de mesure en date UTC (`observed_at`), renomme la température, résume la pluie en vrai / faux (`is_raining` : précipitations > 0) |

### Le modèle gold : `station_hourly`, sous contrat

C'est la table d'entraînement : **une ligne par station et par heure**, avec exactement les
colonnes d'entrée du modèle (`station_id`, `timestamp`, `capacity`, `bikes_available`,
`temperature`, `is_raining`).

Comment elle est construite (`dbt/models/gold/station_hourly.sql`) :

1. chaque relevé est rangé dans l'heure qui le **suit** : un relevé de 8 h 40 sert pour la
   ligne de 9 h ; pour chaque station et chaque heure, on garde le dernier relevé ;
2. même chose pour la météo : la dernière mesure avant l'heure pile ;
3. on ajoute la capacité de la station (sa fiche la plus récente) ;
4. on ne garde que les lignes complètes et cohérentes : station installée, météo connue,
   pas plus de vélos que de bornes.

**Pourquoi "l'heure qui suit" (exigence EX-11).** La ligne de 9 h doit décrire ce qu'on savait
**à 9 h pile**, donc uniquement des informations antérieures. Prendre un relevé de 9 h 40
pour la ligne de 9 h ferait apprendre au modèle avec une information du futur : il serait
excellent à l'entraînement et décevant en production.

**Pourquoi exclure les stations désinstallées ici, et pas en silver.** C'est un choix métier
propre à l'entraînement (une station désinstallée n'a pas de disponibilité à prédire). Le
silver garde tout, pour le reporting.

**Le contrat (exigence EX-12).** `dbt/models/gold/station_hourly.yaml` déclare chaque colonne,
son type et l'obligation d'être renseignée, avec `contract: enforced: true`. Avant de
construire la table, dbt compare ce que produit la requête au contrat : au moindre écart
(colonne manquante, en trop, ou d'un autre type), il refuse de la construire, et l'ancienne
table reste en place. Exemple réel, avec `capacity` produite en texte au lieu d'un entier :

```
This model has an enforced contract that failed.
| column_name | definition_type | contract_type | mismatch_reason    |
| capacity    | VARCHAR         | INTEGER       | data type mismatch |
```

**Rejouable (exigence EX-10).** La table gold est entièrement recalculée à partir du bronze à
chaque `dbt build`, et le bronze ne change jamais : rejouer le pipeline sur une même période
donne la même table.

### Les tests

| Test | Où | Vérifie | Seuil |
|---|---|---|---|
| `not_null` sur les clés | `models/silver/silver.yaml` | `station_id`, `reported_at`, `loaded_at` jamais vides | Erreur dès 1 ligne |
| `not_null` sur les mesures | `models/silver/silver.yaml` | Vélos, places, méca, élec, `is_installed` renseignés | Avertissement de 1 à 10 lignes, erreur au-delà |
| `not_null` et `unique` | `models/silver/silver_station_information.yaml` | Une seule fiche par station ; capacité renseignée | Erreur dès 1 ligne (identifiant) ; 1 à 10 (capacité) |
| `relationships` | `models/silver/silver.yaml` | Chaque station des relevés existe dans les fiches | Avertissement de 1 à 10 lignes, erreur au-delà |
| Capacité | `tests/assert_bikes_within_capacity.sql` | Jamais plus de vélos disponibles que de bornes | Avertissement de 1 à 10 lignes, erreur au-delà |
| `not_null` et `unique` météo | `models/silver/silver_weather.yaml` | Une mesure par heure de mesure ; température renseignée | Erreur dès 1 ligne (heure) ; 1 à 10 (température) |
| Unicité du gold | `tests/assert_station_hourly_unique.sql` | Une seule ligne par station et par heure dans la table d'entraînement | Erreur dès 1 ligne |
| Contrat du gold | `models/gold/station_hourly.yaml` | Colonnes, types, valeurs non vides | Bloque la construction de la table |

Un test dbt est une requête qui cherche les lignes fautives : s'il n'en trouve aucune, il
passe. Le seuil (`warn_if`, `error_if`) tolère quelques défauts, normaux en données réelles,
et bloque le pipeline au-delà.

## 10. Entraîner sur les vraies données (TP3 : contrat Pandera)

`train.py` peut lire la table gold au lieu du CSV simulé : il suffit de lui donner la base
DuckDB.

```bash
python -m velov.train --data data/velov.duckdb                    # lit la table station_hourly
python -m velov.train --data data/velov.duckdb --test-days 2      # tant que l'historique est court
python -m velov.train --data data/velov_history.csv               # données simulées, comme avant
```

Par défaut, les 14 derniers jours servent au test. Tant que la collecte n'a pas tourné
plusieurs semaines, l'entraînement s'arrête avec `Split vide : augmenter l'historique ou
réduire test_days` : réduisez `--test-days`, ou laissez la collecte tourner.

### Le contrat Pandera (`src/velov/contracts.py`)

Avant d'entraîner, quelle que soit la source, les données sont vérifiées :

| Règle | Pourquoi |
|---|---|
| Exactement les 6 colonnes attendues, rien de plus | Une colonne en trop ou manquante signale un changement en amont |
| `station_id` entier, au moins 1 | Identifiant valide |
| `timestamp` avec fuseau horaire | Un instant sans fuseau est ambigu (8 h à Lyon ou 8 h UTC ?) |
| `capacity` entre 1 et 100 | Mêmes bornes que le contrat de l'API |
| `bikes_available` positif et au plus égal à `capacity` | Idem |
| `temperature` entre -30 et 50 °C | Idem ; une valeur hors bornes trahit une erreur d'unité ou de source |
| `is_raining` booléen | Type attendu par les features |
| Une seule ligne par (`station_id`, `timestamp`) | Un doublon fausserait la cible |

Si une règle est violée, l'entraînement s'arrête **avant** de produire un modèle, et Pandera
liste toutes les violations d'un coup (`lazy=True`). Les règles reprennent celles du contrat
Pydantic de l'API : le modèle est entraîné sur des données qu'il aurait acceptées en
production.

**Contrat dbt ou contrat Pandera ?** Les deux se complètent. Le contrat dbt vérifie la
**structure** (noms, types) au moment où la table est **écrite**. Pandera vérifie le
**contenu** (plages de valeurs, cohérence entre colonnes, doublons) au moment où elle est
**lue** pour entraîner.

### Ce qui change dans les métadonnées du modèle

`models/metadata.json` indique la source (`data_source`) et une empreinte des données
(`data_sha256`). Pour une base DuckDB, cette empreinte est calculée sur le **contenu** de la
table (les lignes, triées), et non sur le fichier : un fichier DuckDB change dès qu'il est
réécrit, même si les données sont identiques.

### Les heures manquantes

Avec de vraies données, une heure peut manquer pour une station (collecte arrêtée, station
muette). La cible "vélos dans une heure" est la ligne suivante de la station : elle n'est
gardée que si cette ligne est **exactement une heure plus tard**. Sinon la paire est écartée,
au lieu d'apprendre une cible fausse (`src/velov/features.py`, testé dans
`tests/test_features.py`).

### Les tests

```bash
pytest                       # tous les tests, dont ceux du contrat
pytest tests/test_contract.py
```

`tests/test_contract.py` vérifie que le contrat accepte des données correctes et refuse : plus
de vélos que de bornes, un instant sans fuseau, un doublon, une colonne en trop, une
température hors bornes.

DuckDB et Pandera ne sont importés que pendant l'entraînement : l'API, qui importe
`velov.train`, n'en a pas besoin, et l'image Docker de serving reste inchangée.

## 11. Suivre les entraînements dans MLflow (TP4 : Must 2)

### Pourquoi

Jusqu'ici, un entraînement laissait deux fichiers dans `models/`, écrasés au suivant. Avec
MLflow, chaque entraînement devient un **run** conservé sur le serveur de la VM, avec tout ce
qu'il faut pour savoir d'où vient le modèle et le comparer aux autres.

### Ce qui part dans MLflow (`src/velov/tracking.py`)

| Catégorie | Contenu | À quoi ça sert |
|---|---|---|
| Paramètres | `model`, `seed`, `max_iter`, `learning_rate`, `test_days` | Refaire le même modèle |
| Métriques | `mae_model`, `mae_model_peak_hours`, `mae_baseline_persistence`, `n_train`, `n_test` | Comparer les runs |
| Tags | `git_commit`, `git_dirty`, `data_sha256`, `data_source`, `data_start`/`data_end`, `test_start`/`test_end` | Savoir quel code et quelles données ont produit le modèle |
| Modèle | dossier `model/` (format MLflow, avec signature et exemple d'entrée) | Le recharger avec `mlflow.sklearn.load_model` |
| Paquet | dossier `package/` (`model.joblib` + `metadata.json`) | Le paquet exact que charge l'API |

- `mae_model_peak_hours` : la MAE calculée seulement de 7 h à 9 h et de 17 h à 19 h (heure de
  Lyon). La MAE globale est flattée par les heures de nuit, où presque rien ne bouge.
- `git_dirty` vaut `true` si des fichiers suivis par git étaient modifiés sans être commités :
  le commit indiqué ne suffit alors pas à retrouver le code exact. Pour un run de référence,
  commiter d'abord.
- `test_start` / `test_end` : deux runs ne sont comparables que s'ils ont été évalués sur la
  même fenêtre de test (règle de promotion du Must 3).
- La seed, les hyperparamètres et la période des données sont aussi dans `metadata.json`.

L'adresse du serveur n'est écrite nulle part dans le code : MLflow lit la variable
`MLFLOW_TRACKING_URI`. `velov.tracking` n'est importé que si `--mlflow` est demandé : l'API
et son image Docker n'ont pas besoin de MLflow.

### Les commandes

```bash
export MLFLOW_TRACKING_URI=http://192.168.1.232:5000   # le serveur de la VM

python -m velov.train --seed 42 --mlflow
python -m velov.train --seed 42 --mlflow
python -m velov.train --seed 7 --mlflow
```

Pour essayer sans le serveur : `export MLFLOW_TRACKING_URI=sqlite:///mlflow.db` (fichier
local, ignoré par git), puis `mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5001`.

`--register` inscrit en plus le modèle au registre (`velov-availability`, une nouvelle
version à chaque fois) : il servira à la promotion du Must 3.

### Reproductibilité : même seed, même modèle

Données simulées (`python -m velov.data`, 90 jours, 20 stations), 14 jours de test :

| Run | Seed | MAE | MAE heures de pointe | Empreinte de `model.joblib` |
|---|---|---|---|---|
| 1 | 42 | 1.339 | 1.415 | identique au run 2 |
| 2 | 42 | 1.339 | 1.415 | identique au run 1 |
| 3 | 7 | 1.345 | 1.427 | différente |

- Avec la même seed et les mêmes données, on obtient le même modèle, à l'octet près.
- Changer seulement la seed fait varier la MAE de **0,006 vélo, soit environ 0,4 %**. C'est le
  bruit du hasard, pas une amélioration. Un nouveau modèle qui ne gagne que 0,4 % n'est donc
  pas meilleur : c'est pourquoi la règle de promotion (Must 3) exige un gain d'au moins 2 %.
- L'empreinte dépend aussi des versions des librairies : elle peut différer d'une machine à
  l'autre, d'où les versions notées dans `metadata.json` (`runtime`).

## 12. L'intégration continue (TP4 : Must 4)

### Pourquoi

Jusqu'ici, on lançait `ruff` et `pytest` à la main, quand on y pensait. La CI (intégration
continue) les lance **toute seule, à chaque `git push`**, sur un ordinateur de GitHub. Si
quelque chose casse, le commit apparaît en rouge sur GitHub, et on le sait tout de suite.

### Ce que vérifie `.github/workflows/ci.yml`

| Job | Ce qu'il fait | Ce qu'il protège |
|---|---|---|
| `tests` | `ruff check`, `ruff format --check`, puis `pytest` (API, features, contrat Pandera, qualité du modèle) | Le code et le modèle |
| `secrets` | `gitleaks` sur **tout** l'historique git | Aucun mot de passe ni clé dans le dépôt (EX-06) |
| `image` | Entraîne un petit modèle, construit l'image Docker, la démarre, attend `/ready`, puis fait une prédiction | L'image se construit et sert vraiment des prédictions |

`image` ne démarre que si `tests` et `secrets` sont au vert (`needs`).

### Le test du modèle : `tests/test_model_quality.py`

Un modèle peut se dégrader sans qu'aucun test de code n'échoue : l'API démarre, `/ready`
répond 200, mais les prédictions sont mauvaises. Ce fichier entraîne un petit modèle
(30 jours simulés, 5 stations, test sur 7 jours, environ 1 seconde) et vérifie que :

- le modèle bat la baseline de persistance (EX-05) ;
- la MAE aux heures de pointe est calculée et plausible ;
- deux entraînements avec la même seed donnent le même fichier, octet pour octet.

Essai : avec `LEARNING_RATE = 3.0` dans `train.py`, la MAE passe à 17,9 vélos contre 2,0 pour
la persistance, et le test échoue. Tous les autres tests restent au vert.

### La sécurité de la CI

- **Actions épinglées par SHA** : `actions/checkout@3d3c42e…  # v7.0.1` plutôt que
  `@v7`. Un tag peut être déplacé vers du code malveillant, pas un SHA.
- **`permissions: contents: read`** : le workflow peut lire le dépôt, pas le modifier.
- **gitleaks vérifié** : le binaire est téléchargé, puis comparé à son empreinte SHA-256
  officielle avant d'être exécuté.
- **`.gitleaksignore`** : une seule exception, justifiée dans le fichier : un faux mot de
  passe d'exemple (`unMotDePasseLong123`) dans un ancien commit de `docs/tp4_mlflow.md`.
  L'exemple est maintenant `...`, mais l'ancien commit reste dans l'historique.

### Lancer les mêmes vérifications en local

```bash
ruff check . && ruff format --check . && pytest
```

Si `ruff format --check` échoue, `ruff format .` corrige la mise en forme tout seul.

### Voir le résultat

Sur GitHub, onglet **Actions** du dépôt : une ligne par push, verte ou rouge. Cliquer dessus
montre les trois jobs et le détail de chaque étape.

## 13. Promouvoir un modèle (TP4 : Must 3)

### Pourquoi

Le registre MLflow garde toutes les versions du modèle `velov-availability`. Il faut décider
laquelle l'API doit servir, et pouvoir revenir en arrière. On ne déplace pas des fichiers : on
déplace des **alias**, des étiquettes posées sur une version.

| Alias | Qui le pose | Signification |
|---|---|---|
| `@challenger` | `velov.train --mlflow --register` | La dernière version inscrite, candidate |
| `@champion` | `velov.promote --apply` | La version à servir |
| `@previous-champion` | `velov.promote --apply` | L'ancien champion, pour revenir en arrière |

Les anciens « stages » (`Staging`, `Production`) sont dépréciés depuis MLflow 2.9 : on ne
les utilise pas.

### La règle (`src/velov/promote.py`, fonction `decide`)

Écrite avant de regarder les chiffres, vérifiée dans cet ordre :

1. **Même fenêtre** : challenger et champion sont réévalués par le script, sur les mêmes
   derniers jours des données actuelles. Comparer au score enregistré à l'entraînement du
   champion fausserait tout : ses données de test étaient plus anciennes.
2. **Battre la persistance** (EX-05).
3. **Battre le champion d'au moins 2 %**. Changer seulement la seed fait varier la MAE de
   0,4 % (section 11) : un gain plus petit que 2 % peut venir du hasard.

### Les commandes

```bash
export MLFLOW_TRACKING_URI=http://192.168.1.232:5000

python -m velov.train --seed 42 --mlflow --register   # version N, alias @challenger
python -m velov.promote                                # affiche la décision, ne change rien
python -m velov.promote --apply                        # applique la décision
```

Sans `--apply`, rien ne change : un membre du binôme relit la décision, puis l'applique
(validation humaine). Avec `--apply`, la décision est aussi notée dans un tag `promotion` sur
la version du challenger : promue ou refusée, pourquoi, quand, et par qui (EX-14).

Exemple réel (données simulées, seed 42 puis seed 7) :

```
Fenêtre de test : 2026-08-15T21:00:00+00:00 -> 2026-08-29T20:00:00+00:00, 6720 lignes
challenger v2   : MAE 1.345
champion   v1   : MAE 1.339
persistance     : MAE 1.879
Décision : REFUSER (gain insuffisant : MAE 1.345 > 1.313 (champion - 2%))
```

### Revenir en arrière

Si le nouveau champion pose problème, on remet l'alias sur l'ancien, depuis Python :

```python
import mlflow
client = mlflow.MlflowClient()
ancien = client.get_model_version_by_alias("velov-availability", "previous-champion").version
client.set_registered_model_alias("velov-availability", "champion", ancien)
```

### Les tests (`tests/test_promote.py`)

- La règle seule, cas par cas : premier champion, persistance non battue, gain de 2 % tout
  juste suffisant, gain de 0,7 % refusé, fenêtres différentes.
- Le scénario complet sur un vrai registre MLflow (un fichier SQLite temporaire) : un premier
  champion, un modèle nettement meilleur promu (l'ancien devient `@previous-champion`), puis
  le même modèle avec une autre seed, refusé. Ces tests tournent aussi dans la CI.

## En cas de problème

```bash
docker compose logs api  # affiche les messages de l'API (erreurs de démarrage, modèle absent...)
docker compose logs db   # affiche les messages de PostgreSQL
```

Si `python -m velov.ingest` échoue avec une erreur de colonnes (`table ... has N columns but M
values were supplied`), la table bronze a été créée par une ancienne version du script :
supprimez-la (`DROP TABLE nom_de_la_table;` dans le client DuckDB) et relancez la collecte.

Si `python -m velov...` répond `No module named 'velov'` ou `No module named 'duckdb'`,
le terminal n'utilise pas l'environnement du projet. Vérifiez avec `which python` : le
chemin doit se terminer par `.venv/bin/python`. Avec Anaconda, désactivez d'abord son
environnement (`conda deactivate`), puis réactivez celui du projet
(`source .venv/bin/activate`).
