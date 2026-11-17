# Exigences du système velov-availability

Ce document est le contrat du projet fil rouge, de S1 à S9. Chaque session ajoute des briques ;
ces exigences, elles, ne changent pas. Elles servent de grille à chaque rendu de TP et à la soutenance.

Une exigence est **vérifiable** : pour chacune, il existe une commande, un test ou une revue
qui dit oui ou non.

| ID | Exigence | Comment on la vérifie | Vérifiable dès |
|---|---|---|---|
| EX-01 | Une entrée invalide renvoie une erreur 4xx, jamais un 500 | Tests de contrat : bornes, champ inconnu, champ manquant, timestamp sans fuseau → 422 | S1 |
| EX-02 | `/ready` reflète la capacité réelle à prédire | `/ready` répond 503 si le modèle est absent ou son empreinte invalide ; le HEALTHCHECK de l'image interroge `/ready` | S1, S2 |
| EX-03 | Chaque prédiction indique la version du modèle qui l'a produite | Champ `model_version` dans chaque réponse | S1 |
| EX-04 | Le service démarre depuis un clone propre en suivant la procédure documentée | Un autre binôme suit le README sans aide : en Python (S1), avec `docker compose up` (S2) | S1, S2 |
| EX-05 | Le modèle justifie sa complexité face à la baseline de persistance | MAE du modèle inférieure à celle de la persistance sur le test temporel, lue dans `metadata.json` ; seuil automatisé en CI en S4 | S1, S4 |
| EX-06 | Aucun secret dans le dépôt ni dans l'image | `.env` absent de git ; rien dans `docker history` ni `docker inspect` ; scan automatisé à partir de S4 | S2 |
| EX-07 | Les erreurs importantes sont observables dans les logs | Modèle non chargé, base indisponible : la cause est lisible dans les logs (terminal uvicorn en S1, `docker compose logs` en S2) | S1, S2 |
| EX-08 | Chaque prédiction est traçable | Entrées, sortie, version du modèle et horodatage UTC journalisés en base | S2 |
| EX-09 | Un autre binôme peut exploiter le service avec le README et le runbook | Revue croisée : démarrer, vérifier la santé, arrêter, diagnostiquer `/ready` en 503 | S2, S9 |
| EX-10 | Rejouer le pipeline sur une même période ne change pas la table gold | Deux runs successifs : même nombre de lignes, aucun doublon sur (station_id, timestamp) | S3 |
| EX-11 | Aucune feature n'utilise une information postérieure à l'instant de prédiction | Revue du SQL gold et de `features.py` ; justification dans la model card | S3, S4 |
| EX-12 | Une table gold qui viole le contrat n'est jamais publiée | Contrat dbt enforced, tests bloquants, validation Pandera avant l'entraînement | S3 |
| EX-13 | Le modèle servi est retrouvable jusqu'à son code et ses données | `GET /v1/model` donne la version du registre et le run ; le run porte le commit, l'empreinte des données, les paramètres et les métriques | S4 |
| EX-14 | Un modèle n'est promu que s'il bat le champion et la persistance sur la même fenêtre récente | Sortie de `velov.promote` ; tag `promotion` sur la version promue ; alias `previous-champion` pour le retour arrière | S4 |
| EX-15 | Aucun changement n'arrive sur `main` sans CI au vert | Workflow `ci` : lint, tests (dont EX-05), scan de secrets, build et `/ready` de l'image ; protection de branche sur `main` | S4 |
| EX-16 | L'API a un SLO de latence écrit, vérifié par un test de charge | SLO et capacité mesurée dans le runbook ; tableau des paliers Locust et courbe de saturation dans le README, avec machine et protocole | S5 |
| EX-17 | Une optimisation du modèle n'est servie qu'avec son triplet mesuré | Écart de prédiction ou précision, taille et latence avant et après, dans un ADR | S5 |
| EX-18 | Le remplacement du modèle suit une stratégie et des critères de retour arrière écrits d'avance | Section du runbook : stratégie, signaux, seuils, commande de retour arrière | S5 |

## Conventions associées

- **Horodatage** : les instants sont échangés et stockés en UTC, avec fuseau explicite (ISO 8601).
  Les features calendaires sont calculées en heure locale (Europe/Paris).
- **Versions** : une image est identifiée par la version du modèle et le SHA court du commit, jamais par `latest`.
- **Mesures de performance** : un chiffre de latence ou de débit ne compte pour EX-16 et EX-17 qu'avec son protocole
  (machine, cœurs, versions, commit, paliers, durée, échauffement). Le test de charge part d'une autre machine que l'API.

## Suivi

Chaque rendu indique, dans son README, les exigences couvertes et la preuve associée (test, commande, capture).
Les sessions suivantes ajoutent des moyens de vérification (CI, monitoring, scan), pas de nouvelles exigences
sans qu'elles soient inscrites ici.
