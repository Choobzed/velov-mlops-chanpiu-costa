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
