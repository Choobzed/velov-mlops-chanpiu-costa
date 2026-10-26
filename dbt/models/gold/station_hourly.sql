-- Table d'entraînement : une ligne par station et par heure, avec exactement les colonnes
-- d'entrée du modèle (velov.features.RAW_COLUMNS). Son schéma est garanti par un contrat.
--
-- Règle anti-fuite (EX-11) : la ligne de l'heure H ne contient que des informations connues
-- AVANT H. On range chaque relevé dans l'heure qui SUIT (un relevé de 8 h 40 sert pour 9 h),
-- puis on garde le dernier relevé de chaque créneau : c'est l'état connu à H pile.

with releves as (
    select
        station_id,
        bikes_available,
        is_installed,
        date_trunc('hour', reported_at) + interval 1 hour as hour_ts,
        row_number() over (
            partition by station_id, date_trunc('hour', reported_at)
            order by reported_at desc
        ) as rang
    from {{ ref('silver_station_status') }}
),

dernier_releve as (
    select station_id, hour_ts, bikes_available
    from releves
    where rang = 1
      -- Choix métier pour l'entraînement : une station désinstallée n'a pas de disponibilité
      -- à prédire. Elle reste dans le silver pour le reporting.
      and is_installed
),

meteo as (
    select
        date_trunc('hour', observed_at) + interval 1 hour as hour_ts,
        temperature,
        is_raining,
        row_number() over (
            partition by date_trunc('hour', observed_at)
            order by observed_at desc
        ) as rang
    from {{ ref('silver_weather') }}
)

select
    cast(r.station_id as INTEGER) as station_id,
    cast(r.hour_ts as TIMESTAMPTZ) as "timestamp",
    cast(i.capacity as INTEGER) as capacity,
    cast(r.bikes_available as INTEGER) as bikes_available,
    cast(m.temperature as DOUBLE) as temperature,
    cast(m.is_raining as BOOLEAN) as is_raining
from dernier_releve as r
inner join {{ ref('silver_station_information') }} as i
    on r.station_id = i.station_id
inner join meteo as m
    on r.hour_ts = m.hour_ts and m.rang = 1
-- Seules les lignes complètes et cohérentes partent à l'entraînement. Les relevés écartés
-- restent visibles en silver, où les tests (relationships, capacité) les signalent.
where i.capacity is not null
  and r.bikes_available is not null
  and m.temperature is not null
  and r.bikes_available <= i.capacity
