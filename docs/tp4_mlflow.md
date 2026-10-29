# TP4, étape 1 : le serveur MLflow

> Rédigé avec l'aide d'une IA (Claude). La stack n'étant pas encore publiée sur le dépôt du
> cours, `compose.mlflow.yaml` et `docker/mlflow.Dockerfile` ont été écrits d'après la
> slide S4 · 15, puis testés sur une base PostgreSQL réelle.

## Ce que fait la stack

| Service / volume | Rôle |
|---|---|
| `mlflow-db` (PostgreSQL 16) | Stocke les runs, paramètres, métriques, tags et le registre (versions, alias) |
| `mlflow` (MLflow 3.16.1) | Serveur de suivi : API pour les scripts et interface web, port 5000 |
| volume `mlflow_db_data` | Les données de la base, conservées après un arrêt ou un redémarrage |
| volume `mlartifacts` | Les fichiers des runs (modèles, `model.joblib`, `metadata.json`), servis par le serveur |

Les scripts (`velov.train`) ne parlent qu'au serveur, par HTTP. Ils n'ont accès ni à la
base ni au disque : le serveur reçoit les fichiers et les range dans le volume
(`--serve-artifacts`). La stack est séparée de celle de l'API (projet `velov-mlflow`).

## Lancer la stack

1. Ajouter le mot de passe de la base dans le fichier `.env` (jamais commité) :

   ```
   MLFLOW_DB_PASSWORD=unMotDePasseLong123
   MLFLOW_PORT=5001
   ```

   Lettres et chiffres uniquement : le mot de passe est inséré dans une URL.
   `MLFLOW_PORT` est facultatif (5000 par défaut). Sur macOS, le port 5000 est souvent
   occupé par le récepteur AirPlay : 5001 évite le conflit.

2. Construire et démarrer :

   ```bash
   docker compose -f compose.mlflow.yaml up -d --build
   docker compose -f compose.mlflow.yaml ps
   ```

   `mlflow-db` doit être `healthy`, puis `mlflow` aussi (une trentaine de secondes).
   Sans `MLFLOW_DB_PASSWORD`, le lancement s'arrête avec un message explicite.

3. Ouvrir l'interface : `http://localhost:5001` (ou `http://<ip_de_la_vm>:5000` sur la VM).

4. Dire aux scripts où est le serveur, sans l'écrire dans le code :

   ```bash
   export MLFLOW_TRACKING_URI=http://localhost:5001
   ```

## Vérifier les critères de l'énoncé

| Critère | Comment le vérifier |
|---|---|
| `mlflow` et `mlflow-db` tournent, la base est healthy | `docker compose -f compose.mlflow.yaml ps` |
| L'interface s'ouvre depuis un autre poste | Sur la VM : `http://192.168.1.2xx:5000` depuis le poste du binôme |
| Le mot de passe n'est ni dans git, ni dans le compose | Le compose ne contient que `${MLFLOW_DB_PASSWORD}` ; `.env` est dans le `.gitignore` |
| Rien n'est perdu après un redémarrage | Créer un run, `docker compose -f compose.mlflow.yaml restart`, le run est toujours dans l'interface |

## Les choix, et pourquoi

- **Une image maison** : l'image officielle de MLflow n'a pas de pilote PostgreSQL.
- **psycopg 3, pas psycopg2** : la version de SQLAlchemy installée avec MLflow 3.16.1
  utilise psycopg 3 par défaut. Avec psycopg2, le serveur s'arrête au démarrage
  (`No module named 'psycopg'`), constaté pendant les tests.
- **Versions épinglées** : MLflow 3.16.1, comme dans `requirements-dev.txt`, pour que client
  et serveur aient la même version.
- **`--allowed-hosts`** : depuis MLflow 3.5, le serveur refuse (erreur 403, `Invalid Host
  header`) les requêtes dont l'en-tête `Host` n'est pas autorisé, pour bloquer les attaques
  par DNS rebinding. Sont acceptés : `localhost`, les adresses privées de l'école
  (`192.168.*`, `10.*`) et le nom du service `mlflow`.
- **`--cors-allowed-origins`** : depuis MLflow 3.5, le serveur refuse aussi (erreur 403,
  `Blocked cross-origin request`) les requêtes POST venues d'une page web d'une autre adresse
  que `localhost`. Or l'interface web elle-même, ouverte sur `http://192.168.1.232:5000`, est
  « une autre adresse » : sans cette option, la liste des runs affiche `INTERNAL_ERROR`
  (constaté sur la VM). Les adresses privées de l'école sont donc autorisées.
- **Télémétrie désactivée** (`MLFLOW_DISABLE_TELEMETRY`) : rien ne sort du réseau de l'école.
- **Utilisateur non-root**, comme l'image de l'API.
- **Limite connue** : le serveur n'a pas d'authentification. Toute personne sur le réseau de
  l'école peut lire ou supprimer les modèles (sujet de la session 8).

## Sans VM

Tant que la VM est injoignable, la stack tourne en local de la même façon (étapes ci-dessus).
L'énoncé propose aussi une solution sans Docker pour avancer : `export
MLFLOW_TRACKING_URI=sqlite:///mlflow.db` (un fichier local, `mlflow.db` est dans le
`.gitignore`).

Déployer sur la VM, quand elle sera joignable :

```bash
git push vm tp4
ssh groupeN@192.168.1.2xx
cd ~/velov-mlops && git pull origin tp4
nano .env      # MLFLOW_DB_PASSWORD=...
docker compose -f compose.mlflow.yaml up -d --build
```
