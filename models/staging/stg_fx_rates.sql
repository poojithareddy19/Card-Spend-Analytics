select
    cast(rate_date as date) as rate_date,
    currency,
    cast(rate_to_gbp as double) as rate_to_gbp,
    source
from read_parquet('{{ var("lake_path") }}/fx_rates/fx_rates.parquet')
