select
    card_id,
    account_id,
    card_type,
    network,
    cast(issued_date as date) as issued_on,
    cast(expiry_date as date) as expires_on,
    status
from read_parquet('{{ var("lake_path") }}/cards/cards.parquet')
