-- The fact feed, read straight off the partitioned lake.
-- `hive_partitioning = 1` is what recovers `posted_date` from the directory name, and it is also
-- what lets DuckDB skip whole files when a query filters on it. Day 5 measures that.
select
    transaction_id,
    card_id,
    account_id,
    merchant_id,
    txn_timestamp,
    cast(posted_date as date)                                   as posted_date,
    cast(txn_timestamp at time zone 'Europe/London' as date)    as auth_date,
    amount_minor,
    currency,
    mcc,
    auth_code,
    txn_type,
    channel,
    status,
    pos_entry_mode,
    is_recurring,
    feed_version
from read_parquet(
    '{{ var("lake_path") }}/card_transactions/*/*.parquet',
    hive_partitioning = 1
)
