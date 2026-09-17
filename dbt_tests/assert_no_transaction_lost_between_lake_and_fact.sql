-- Reconciliation, the test that actually catches modelling mistakes.
--
-- Every distinct transaction in the lake must appear exactly once in the fact. A left join that
-- fanned out, or an inner join that quietly dropped rows, shows up here and nowhere else.
with lake as (
    select count(distinct transaction_id) as n from {{ ref('stg_card_transactions') }}
),
fact as (
    select count(*) as n from {{ ref('fct_card_transaction') }}
)
select lake.n as lake_rows, fact.n as fact_rows
from lake cross join fact
where lake.n != fact.n
