-- Météo à Lyon, nettoyée : heure de mesure convertie en date UTC, noms clairs,
-- et la pluie résumée en vrai / faux comme l'attend le modèle.

with source as (
    select * from {{ source('bronze', 'bronze_weather') }}
)

select
    -- Open-Meteo donne l'heure sans fuseau, en GMT : on la déclare explicitement en UTC.
    timezone('UTC', strptime(observed_time, '%Y-%m-%dT%H:%M')) as observed_at,
    temperature_2m as temperature,
    precipitation,
    precipitation > 0 as is_raining,
    loaded_at
from source
where observed_time is not null
