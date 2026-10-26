with source as (
    select * from {{ source('bronze', 'bronze_station_status') }}
)

select
    try_cast(station_id as integer) as station_id,
    num_bikes_available as bikes_available,
    num_bikes_disabled as bikes_disabled,
    num_docks_available as docks_available ,
    num_docks_disabled as docks_disabled,
    is_installed,
    is_renting,
    is_returning,
    -- celui en dessous je l'ai fait avec IA
    cast(list_sum(list_transform(
        list_filter(
            from_json(vehicle_types_available, '[{"vehicle_type_id":"VARCHAR","count":"INTEGER"}]'),
            lambda x: x.vehicle_type_id = 'mechanical'
        ),
        lambda x: x.count
    )) as INTEGER) as mechanical_bikes_available,
        cast(list_sum(list_transform(
        list_filter(
            from_json(vehicle_types_available, '[{"vehicle_type_id":"VARCHAR","count":"INTEGER"}]'),
            lambda x: x.vehicle_type_id = 'electrical'
        ),
        lambda x: x.count
    )) as INTEGER) as electric_bikes_available,
    to_timestamp(last_reported) as reported_at,
    loaded_at
from source
where try_cast(station_id as integer) is not null