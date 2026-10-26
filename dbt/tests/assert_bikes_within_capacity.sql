-- Test de capacité : une station ne peut pas avoir plus de vélos disponibles que de bornes.
-- C'est la même règle que le contrat Pydantic de l'API (bikes_available <= capacity).
-- Un test dbt échoue s'il renvoie des lignes : on sélectionne donc les relevés incohérents.
-- Seuil : jusqu'à 10 relevés incohérents, simple avertissement ; au-delà, erreur.

{{ config(warn_if=">0", error_if=">10") }}

select
    s.station_id,
    s.reported_at,
    s.bikes_available,
    i.capacity
from {{ ref('silver_station_status') }} as s
inner join {{ ref('silver_station_information') }} as i
    on s.station_id = i.station_id
where s.bikes_available > i.capacity
