-- Une seule ligne par station et par heure dans la table d'entraînement.
-- Un doublon fausserait la cible (le nombre de vélos de l'heure suivante).

select station_id, "timestamp", count(*) as nb_lignes
from {{ ref('station_hourly') }}
group by station_id, "timestamp"
having count(*) > 1
