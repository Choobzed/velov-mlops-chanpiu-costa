# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Ce que fait le service, en deux phrases :
- Schéma d'architecture (lien) :
- Dépendances (base de données, stockage du modèle...) :

## 2. Démarrer / arrêter
```bash
# démarrer
# arrêter
# redémarrer un seul service
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| | | |

### Vérifier le proxy HTTPS (TP6)

Depuis le TP6, l'API n'est plus joignable directement : tout passe par le proxy Nginx
(`docker/nginx/velov.conf`), en HTTPS sur le port 443, avec un certificat auto-signé.
Le client doit présenter ce certificat (`--cacert certs/velov.crt`) ; la clé `certs/velov.key`
ne quitte jamais la machine qui sert l'API.

`VM` vaut l'adresse de la VM (`192.168.1.232`), ou `127.0.0.1` pour un essai local (certificat
créé avec `./scripts/make_cert.sh 127.0.0.1`).

| Appel | Résultat attendu | Ce que ça prouve |
|---|---|---|
| `curl -s -o /dev/null -w "%{http_code}\n" http://$VM/health` | `301` | Le HTTP en clair est redirigé vers HTTPS |
| `curl -s https://$VM/health; echo $?` | `60` (code de sortie de curl) | Sans le certificat, curl refuse un serveur qu'il ne peut pas vérifier |
| `curl -s -o /dev/null -w "%{http_code}\n" --cacert certs/velov.crt https://$VM/health` | `200` | HTTPS fonctionne, le proxy joint l'API |
| `curl -s -o /dev/null -w "%{http_code}\n" --cacert certs/velov.crt https://$VM/metrics` | `404` | Les métriques ne sortent pas : seul Prometheus les lit, dans le réseau Docker |
| `docker compose stop api`, puis le même appel que pour `200` | `502` | Proxy debout, API absente : « Bad Gateway ». Relancer avec `docker compose start api` |
| `curl -s -m 3 http://$VM:8000/health; echo $?` | code non nul (`7` : connexion refusée) | Le port 8000 n'est plus publié : on ne contourne plus le proxy |

Lecture d'un `502` en incident : le proxy fonctionne mais n'atteint pas l'API (conteneur arrêté,
en redémarrage ou planté). Regarder `docker compose ps` et `docker compose logs api`.

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | | | |
| Le conteneur redémarre en boucle | | | |
| Latence élevée | API proche de son plafond (file d'attente), ou code devenu plus lent | Comparer `X-Process-Time-Ms` au temps vu par le client, et lire le CPU avec `docker stats` (voir section 7) | Voir section 7 |

## 5. Déployer une nouvelle version du modèle
1.
2.
3. Retour arrière (rollback) :

## 6. Contacts et escalade
-

## 7. Performance et capacité (TP5, exigence EX-16)

> Rédigé avec l'aide d'une IA (Claude, Anthropic) à partir de nos mesures Locust du
> 17 novembre 2026 (détail, protocole et fichiers bruts : README, section « Performance »).

### SLO

| Indicateur | Objectif | Mesuré sur |
|---|---|---|
| Latence | 95 % des `POST /v1/predict` en moins de 200 ms (p95 < 200 ms) | La route `/v1/predict` seule, côté client |
| Erreurs | Moins de 1 % de réponses en erreur | Idem |

### Capacité mesurée

Mesure locale (Apple M3, Docker Desktop 29.6.1), API bornée par `docker update --cpus`,
scénario `loadtest/locustfile.py` (temps de réflexion de 0,5 à 1,5 s par usager).

| Configuration | Capacité sous le SLO | Plafond | Mémoire de l'API |
|---|---|---|---|
| 1 worker, 1 cœur | environ 87 req/s (100 usagers) | environ 95 req/s | environ 160 Mio |
| 2 workers, 2 cœurs | environ 109 req/s (125 usagers) | environ 123 req/s | environ 340 Mio |

Le deuxième worker n'apporte que 25 % de capacité (pas le double) pour deux fois plus de CPU
et de mémoire. On ne compte donc pas « 87 req/s par worker » pour dimensionner : il faut
mesurer chaque configuration.

Sur la VM de l'école, ces chiffres sont à remesurer : un autre processeur donne une autre
capacité (repère du cours : environ 41 req/s sur un cœur).

### Seuils d'exploitation

On n'exploite jamais au ras de la capacité : à 90 % du plafond, la latence varie du simple au
double d'une mesure à l'autre (p95 de 100 à 150 ms à 100 usagers, mesuré deux fois).

| Niveau | Seuil (2 workers, 2 cœurs) | Action |
|---|---|---|
| Normal | Débit de `/v1/predict` sous 80 req/s, p95 sous 100 ms | Rien |
| Alerte | Débit au-dessus de 80 req/s (environ 75 % de la capacité), ou p95 au-dessus de 150 ms pendant 5 min | Prévenir le binôme ; préparer de la capacité (plus de CPU ou une deuxième instance de l'API derrière un répartiteur) |
| Incident | p95 au-dessus de 200 ms ou plus de 1 % d'erreurs pendant 5 min | SLO cassé : ajouter de la capacité, ou limiter le trafic en attendant |

Au-delà du plafond, le service ne fait pas que ralentir, il s'effondre : à 250 usagers sur
2 workers, le débit retombe à 93 req/s et le p95 monte à 2,3 s. Il vaut mieux refuser des
requêtes que de laisser la file grossir.

### Diagnostiquer une latence élevée

1. **Le temps passé dans l'API** : chaque réponse porte l'en-tête `X-Process-Time-Ms`.

   ```bash
   curl -s -o /dev/null -D - -X POST http://<hôte>:8000/v1/predict \
     -H "content-type: application/json" -d @loadtest/payload.json | grep -i x-process-time
   ```

2. **Le comparer à ce que vit le client** (temps total de `curl`, ou p95 de Locust).
   - En-tête bas (environ 10 ms) mais client lent : les requêtes **attendent leur tour**. L'API
     est au plafond. Vérifier avec `docker stats` : un CPU de l'API proche de 100 % par cœur
     alloué le confirme. Action : ajouter de la capacité ou réduire le trafic.
   - En-tête élevé : c'est **notre code** qui est devenu lent (nouveau modèle, base de données
     lente). Regarder le CPU de `db` dans `docker stats`, et rejouer
     `python scripts/bench_predict.py` pour voir quelle étape a changé.
3. **Vérifier que ce n'est pas l'outil de mesure** : pendant un test de charge, Locust doit
   rester sous 90 % de CPU, sinon c'est lui qui limite.

### Rappels utiles

- Temps de référence d'une prédiction (1 worker, au repos) : environ 10 ms dans l'API, dont
  environ 4,5 ms de calcul (`model.predict` : 73 %) et l'essentiel du reste pour l'écriture en
  base, qui ouvre une connexion PostgreSQL par requête.
- Remesurer la capacité après tout changement de modèle, de code de l'API ou de machine :
  `./loadtest/paliers.sh http://<hôte>:8000 <prefixe>`, puis `python loadtest/summary.py results/<prefixe>`.

## 8. Métriques et alertes (TP6)

> Rédigé avec l'aide d'une IA (Claude, Anthropic).

L'API publie ses métriques sur `/metrics` (instrumentateur Prometheus, `src/velov/api/main.py`).
Prometheus les lit toutes les 15 s **depuis le réseau Docker** (`api:8000`) ; le proxy bloque
`/metrics` depuis l'extérieur (404). Interface de Prometheus : `http://<hôte>:9090`.

### Les métriques utiles

| Métrique | Ce qu'elle compte |
|---|---|
| `http_requests_total{handler, method, status}` | Requêtes par route, méthode et famille de statut (`2xx`, `4xx`, `5xx`) |
| `http_request_duration_seconds_bucket{handler, method, le}` | Durées rangées en seaux : 0,05 / 0,1 / **0,2** / 0,5 / 1 / 2,5 s |
| `velov_model_info{model_version}` | Vaut 1 ; l'étiquette donne la version du modèle servi |
| `velov:latence_sli:ratio_rate5m` | SLI calculé par Prometheus : part des `/v1/predict` sous 200 ms sur 5 min |

Le seau à 0,2 s est déclaré dans `main.py` : ceux de l'instrumentateur par défaut (0,1 / 0,5 /
1 s) ne permettent pas de compter les requêtes sous le seuil du SLO.

### Les alertes (`docker/prometheus/alerts.yml`)

| Alerte | Condition | Gravité | Premières actions |
|---|---|---|---|
| `ApiInjoignable` | Prometheus ne lit plus `/metrics` (`up == 0`) depuis 1 min | page | `docker compose ps`, `docker compose logs api` ; vérifier que le conteneur tourne et que `/ready` répond |
| `LatenceHorsSLO` | SLI sous 0,95 pendant 5 min | ticket | Section 7 : comparer `X-Process-Time-Ms` et la latence client ; `docker stats` pour le CPU |
| `Erreurs5xx` | Plus de 1 % des `/v1/predict` en 5xx pendant 5 min | page | `docker compose logs api` ; un 503 vient du modèle absent ou de la base injoignable (`/ready`) |

Une alerte passe par trois états, visibles sur la page **Alerts** de Prometheus :
**inactive** (condition fausse), **pending** (condition vraie, mais pas encore depuis la durée du
`for`), **firing** (déclenchée). Le `for` évite d'alerter sur un pic isolé. Sans trafic, le SLI n'a
pas de valeur (0/0) et `LatenceHorsSLO` reste inactive : c'est `ApiInjoignable` qui couvre une
API arrêtée.

### Vérifier

```bash
docker compose exec prometheus promtool check rules /etc/prometheus/alerts.yml   # syntaxe
docker compose exec prometheus promtool test rules /etc/prometheus/alerts_test.yml # comportement
docker compose restart prometheus                                                # recharger les règles
```

`alerts_test.yml` fabrique des séries (90 % puis 99 % des requêtes sous 200 ms, 2 % puis 0,5 % de
5xx, API arrêtée) et vérifie quelles alertes se déclenchent, et quand.

Page **Targets** : `velov-api` doit être **UP**. Si elle est **DOWN** avec une erreur 404, l'image
de l'API n'a pas été reconstruite avec l'instrumentateur (`docker compose up -d --build api`) ;
avec « connection refused », l'API ne tourne pas.
