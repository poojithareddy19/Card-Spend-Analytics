select
    account_id,
    customer_id,
    product_code,
    currency,
    status,
    cast(opened_date as date) as opened_on,
    cast(closed_date as date) as closed_on,
    overdraft_limit_minor
from read_parquet('{{ var("lake_path") }}/accounts/accounts.parquet')
