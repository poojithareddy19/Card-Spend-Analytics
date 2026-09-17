-- Two versions of the same customer must never be valid at the same instant, or the fact's as-of
-- join fans out and every measure doubles.
select customer_id, count(*) as overlapping
from (
    select
        a.customer_id,
        a.valid_from,
        lead(a.valid_from) over (partition by a.customer_id order by a.valid_from) as next_from,
        a.valid_to
    from {{ ref('dim_customer') }} a
)
where next_from is not null and next_from < valid_to
group by 1
