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
| Commit mesuré | `ec29ca8` pour le premier essai, `d0047de` pour les paliers (même code de l'API) |
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

### Étape 2 : courbe de saturation, 1 worker sur 1 cœur

```bash
./loadtest/paliers.sh http://localhost:8000 w1      # échauffement 1 min, puis 5 paliers de 2 min
python loadtest/summary.py results/w1 --markdown
```

Mesure du 17 novembre 2026 à 12 h 11 (heure de Paris), commit `d0047de`. Fichiers bruts dans
`results/w1/` (dont `protocole.txt`, écrit par `paliers.sh`).

| Usagers | Requêtes | Erreurs % | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 1 047 | 0,0 | 8,8 | 24 | 38 | 49 | ok |
| 25 | 2 644 | 0,0 | 22,1 | 16 | 38 | 53 | ok |
| 50 | 5 242 | 0,0 | 43,9 | 14 | 32 | 93 | ok |
| 100 | 10 383 | 0,0 | 87,0 | 15 | 150 | 260 | ok |
| 150 | 11 161 | 0,0 | 93,6 | 480 | 760 | 930 | **KO** |

Le p99 est peu fiable sous 10 000 requêtes (paliers 10, 25 et 50) : trop peu de requêtes lentes
pour l'estimer.

![Courbe de saturation, 1 worker sur 1 cœur](docs/perf/saturation_w1.png)

Lecture :
- **Jusqu'à 100 usagers, l'API suit** : le débit monte avec les usagers, presque exactement
  comme le prévoit la loi de Little (100 usagers / environ 1 s par cycle, soit au plus 90 req/s de
  prédictions), et le p95 reste sous le SLO.
- **À 150 usagers, le débit plafonne** : +50 % d'usagers ne donnent que +8 % de débit
  (87 à 93,6 req/s ; le palier 125 de la seconde mesure atteint 98,3 req/s). Les requêtes en trop attendent leur tour : le p50 passe de 15 à 480 ms et le
  p95 de 150 à 760 ms. C'est le coude de la courbe.
- **Le p50 baisse quand la charge monte** (24 ms à 10 usagers, 14 ms à 50) : à faible charge,
  macOS laisse le cœur à basse fréquence ou sur un cœur économe de l'Apple M3 ; sous charge, il
  passe à pleine vitesse. C'est la limite de mesure notée dans le protocole.
- **Comparaison avec le repère du cours** (environ 41 req/s sous le SLO sur un cœur) : notre
  capacité est environ deux fois plus élevée, ce qui correspond à un cœur d'Apple M3 plus rapide
  que celui de la machine de préparation. L'ordre de grandeur est cohérent.
- La courbe ajoute le palier 125 de la seconde mesure (w1b) pour mieux situer le coude.

#### Seconde mesure autour du coude (w1b)

```bash
PALIERS="100 125 150" ./loadtest/paliers.sh http://localhost:8000 w1b
```

| Usagers | Requêtes | Erreurs % | Débit (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | SLO p95 |
|---|---|---|---|---|---|---|---|
| 100 | 10 387 | 0,0 | 87,1 | 15 | 100 | 240 | ok |
| 125 | 11 715 | 0,0 | 98,3 | 110 | 410 | 580 | **KO** |
| 150 | 11 130 | 0,0 | 93,3 | 470 | 750 | 970 | **KO** |

Reproductibilité, première mesure contre seconde :
- **Débit** : 87,0 contre 87,1 req/s à 100 usagers, 93,6 contre 93,3 à 150. Écart inférieur à
  1 % : la mesure est stable, le poste ne faisait rien d'autre.
- **p95 à 100 usagers** : 150 contre 100 ms. L'écart dépasse 20 %, mais ce n'est pas du bruit
  de mesure : ce palier est au bord du coude (l'API y tourne à environ 90 % de son plafond), là
  où la file d'attente se forme ou non selon de petites variations. Près de la saturation, la
  latence devient très sensible ; le débit, lui, ne bouge pas. À 150 usagers, loin dans la
  saturation, le p95 est de nouveau stable (760 contre 750 ms).

CPU pendant le palier 150 (`docker stats --no-stream` et `ps aux`) :

| Processus | CPU | Lecture |
|---|---|---|
| API (`velov-mlops-student-api-1`) | 102,7 % | Son unique cœur est plein : c'est elle le goulot |
| PostgreSQL (`db-1`) | 22,8 % | Écrit chaque prédiction ; non borné, il n'est pas limitant |
| Locust | 8,6 % | Très loin des 90 % : le générateur de charge n'est pas limitant |
| Mémoire de l'API | 157,5 Mio | Pour 1 worker (à comparer à 2 workers, étape 4) |

#### Conclusion de l'étape 2

- **Capacité sous le SLO : environ 87 req/s**, atteinte à 100 usagers (p95 entre 100 et 150 ms).
- **Le SLO casse entre 100 et 125 usagers** : p95 à 410 ms dès 125 usagers.
- **Plafond de l'API : environ 95 req/s** (93 à 98 selon les paliers). Au-delà, ajouter des
  usagers n'augmente plus le débit, seulement l'attente.
- **Le facteur limitant est le CPU de l'API** (102,7 % de son cœur), ni Locust (8,6 %), ni la
  base. Pour aller plus loin, il faut plus de CPU pour l'API (étape 4) ou moins de calcul par
  requête (étape 3).
- **En exploitation**, on ne vise pas 87 req/s : à 90 % du plafond, la latence varie du simple
  au double d'une mesure à l'autre. Une marge (alerte vers 70 req/s) est reprise dans le runbook.

### Étape 3 : où part le temps

**Temps dans l'API contre temps vu par le client** : 20 requêtes une par une, API au repos
(1 worker, 1 cœur), en-tête `X-Process-Time-Ms` contre temps total mesuré par `curl`.

```bash
for i in $(seq 20); do
  curl -s -o /dev/null -D /tmp/h -w "%{time_total}" -X POST http://localhost:8000/v1/predict \
    -H "content-type: application/json" -d @loadtest/payload.json > /tmp/t
  echo "api $(grep -i x-process /tmp/h | tr -d '\r' | cut -d' ' -f2) ms   client $(cat /tmp/t) s"
done
```

| | Médiane | Remarque |
|---|---|---|
| Temps dans l'API (`X-Process-Time-Ms`) | 10,3 ms | 9 à 13 ms la plupart du temps, pics à 17-30 ms |
| Temps vu par le client (`curl`) | 12,0 ms | |
| Différence, requête par requête : réseau local et serveur HTTP | environ 1,3 ms (médiane) | Le temps se passe dans notre code |

La première requête après le démarrage a pris 66 ms (tout se met en route) : c'est pourquoi
les mesures commencent par un échauffement non compté. Les pics s'expliquent par les deux sortes
de cœurs de l'Apple M3 (voir le protocole).

**Temps par étape de `_predict()`**, sans serveur ni réseau, avec `scripts/bench_predict.py`
(mêmes fonctions que l'API, `time.perf_counter`, 500 mesures après 20 d'échauffement,
modèle chargé une fois hors mesure). Exécuté directement sur le Mac (arm64, Python 3.12.12,
scikit-learn 1.9.1, pandas 3.0.2) :

```bash
python scripts/bench_predict.py --n 500 --batch 1 100 1000
```

| Étape | p50 (ms) | p95 (ms) | Part du p50 |
|---|---|---|---|
| 1. Construire le DataFrame | 0,223 | 0,759 | 4,9 % |
| 2. `add_features` | 0,864 | 1,549 | 19,0 % |
| **3. `model.predict`** | **3,320** | **4,545** | **73,0 %** |
| 4. Construire la réponse | 0,004 | 0,025 | 0,1 % |
| Total du calcul | 4,548 | 6,097 | |

**`model.predict` selon la taille du lot** :

| Lignes prédites d'un coup | p50 (ms) | Temps par ligne |
|---|---|---|
| 1 | 3,733 | 3 733 µs |
| 100 | 3,852 | 38,5 µs |
| 1 000 | 6,091 | 6,1 µs |

#### Le goulot et sa preuve

- **Dans le calcul, le goulot est `model.predict` : 73 % du temps**, devant `add_features`
  (19 %). La construction de la réponse ne coûte rien.
- **`model.predict` est presque entièrement un coût fixe** : 3,7 ms pour 1 ligne, 3,9 ms pour
  100. À chaque appel, scikit-learn vérifie et convertit les données, quel que soit leur nombre.
  Prédire les lignes une par une, comme le fait `/v1/predict`, paie ce coût à chaque requête.
- **L'écriture en base pèse autant que le calcul.** L'API passe environ 10 ms par requête, le
  calcul seul environ 4,5 ms. L'essentiel de la différence vient de `save_prediction`
  (`src/velov/api/database.py`), qui **ouvre une nouvelle connexion PostgreSQL à chaque
  prédiction** (identification, et un processus PostgreSQL créé pour chaque connexion). Réserve :
  le script tourne sur le Mac et l'API dans Docker, l'écart n'est donc qu'un ordre de grandeur.
- **Pistes d'optimisation**, non réalisées ici (une seule modification par mesure) : prédire par
  lots (`/v1/predict/batch`, ou regrouper les requêtes) pour amortir le coût fixe, et garder des
  connexions ouvertes (pool de connexions `psycopg_pool`) au lieu d'en ouvrir une par requête.

### Étape 4 : une seule modification, 2 workers sur 2 cœurs

La seule différence avec l'étape 2 : `compose.workers.yaml` lance uvicorn avec `--workers 2`,
et la limite CPU passe à 2 cœurs. Même image, même code, même scénario, mêmes paliers
(plus 200 et 250, car 150 usagers ne suffisent pas à saturer 2 workers : d'après la loi de
Little, ils envoient au plus environ 135 req/s).

```bash
docker compose -f compose.yaml -f compose.workers.yaml up -d api
docker update --cpus 2 $(docker compose ps -q api)
docker compose top api        # le processus parent et 2 workers (spawn_main)
PALIERS="10 25 50 100 150 200 250" ./loadtest/paliers.sh http://localhost:8000 w2
PALIERS="125 200" ./loadtest/paliers.sh http://localhost:8000 w2b
```

Mesures du 17 novembre 2026, 14 h 26 et 14 h 53 (heure de Paris), commit `4e56cda`, pgAdmin
arrêté. Fichiers bruts dans `results/w2/` et `results/w2b/`.

| Usagers | Débit w1 (req/s) | p95 w1 (ms) | Débit w2 (req/s) | p95 w2 (ms) | SLO w2 |
|---|---|---|---|---|---|
| 10 | 8,8 | 38 | 8,9 | 38 | ok |
| 25 | 22,1 | 38 | 22,1 | 35 | ok |
| 50 | 43,9 | 32 | 44,3 | 29 | ok |
| 100 | 87,0 | 150 | 88,6 | 41 | ok |
| 125 | 98,3 (w1b) | 410 | 109,4 (w2b) | 82 | ok |
| 150 | 93,6 | 760 | 121,3 | 370 | **KO** |
| 200 | | | 122,6 | 830 | **KO** |
| 250 | | | 92,8 | 2 300 | **KO** |

Erreurs : 0 % à tous les paliers. Le palier 200 de w2b a été interrompu avant la fin
(7 158 requêtes) : la valeur retenue est celle de w2.

![Avant / après : 1 worker contre 2 workers](docs/perf/saturation_w1_w2.png)

Ressources pendant la saturation (palier 150 pour w1, palier 200 pour w2) :

| | 1 worker, 1 cœur | 2 workers, 2 cœurs | Rapport |
|---|---|---|---|
| CPU de l'API | 102,7 % | 214,9 % | x 2,1 |
| Mémoire de l'API | 157,5 Mio | 339 Mio | x 2,2 |
| CPU de PostgreSQL | 22,8 % | 38,3 % | |
| CPU de Locust | 8,6 % | 15,7 % | |
| **Capacité sous le SLO** | **environ 87 req/s** | **environ 109 req/s** | **x 1,25** |
| **Plafond** | **environ 95 req/s** | **environ 123 req/s** | **x 1,3** |

#### Conclusion

**Deux fois plus de CPU et deux fois plus de mémoire donnent 25 % de capacité en plus sous
le SLO (de 87 à 109 req/s) et 30 % de plafond en plus (de 95 à 123 req/s), pas le double.**

- **Ce qui s'améliore nettement : la latence sous charge moyenne.** À 100 usagers, le p95
  passe de 150 à 41 ms, et à 125 usagers de 410 à 82 ms : le SLO tient 25 usagers de plus.
- **Ce n'est ni Locust (15,7 %) ni PostgreSQL (38 %) qui limitent : l'API utilise bien ses
  2 cœurs (214,9 %).** Mais chaque requête y coûte plus de CPU : environ 10,8 ms de CPU par
  requête avec 1 worker (1,03 cœur pour 95 req/s), environ 17,5 ms avec 2 workers (2,15 cœurs
  pour 123 req/s).
- **Hypothèses, non vérifiées ici** : le second cœur prêté par macOS peut être un cœur économe
  de l'Apple M3, plus lent ; et chaque worker lance beaucoup de fils d'exécution (189 au total
  d'après `docker stats`, dont ceux de `model.predict`, parallélisé par OpenMP) qui se disputent
  2 cœurs. Pour trancher, une prochaine mesure changerait une seule chose : `OMP_NUM_THREADS=1`.
- **À 250 usagers, le débit s'effondre** (92,8 req/s, p95 à 2,3 s, en dégradation continue
  pendant le palier) : au-delà du plafond, ajouter des usagers fait baisser le service.
- **Le coût mémoire double** : chaque worker charge sa propre copie du modèle. Avec ce petit
  modèle (environ 160 Mio par worker), ce n'est pas un problème ; avec un gros modèle, ce serait
  la limite.

La capacité retenue et les seuils d'exploitation sont dans
[docs/runbook.md](docs/runbook.md), section « Performance et capacité ».

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
