select
    merchant_id,
    merchant_name,
    mcc,
    category,
    country,
    is_online,
    cast(first_seen_date as date) as first_seen_on
from read_parquet('{{ var("lake_path") }}/merchants/merchants.parquet')
