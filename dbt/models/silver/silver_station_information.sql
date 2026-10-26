-- Fiche actuelle de chaque station : nom, position, capacité.
-- Le bronze garde toutes les versions d'une fiche ; ici on ne garde que la plus récente.

with source as (
    select * from {{ source('bronze', 'bronze_station_information') }}
),

ranked as (
    select
        *,
        -- numérote les versions de chaque station, de la plus récente (1) à la plus ancienne
        row_number() over (partition by station_id order by loaded_at desc) as version_rank
    from source
)

select
    try_cast(station_id as integer) as station_id,
    name as station_name,
    lat as latitude,
    lon as longitude,
    capacity,
    loaded_at as information_loaded_at
from ranked
where version_rank = 1
  and try_cast(station_id as integer) is not null
