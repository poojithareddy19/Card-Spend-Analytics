{#
    SCD Type 2 on the account. Product code and status change; the overdraft limit changes far more
    often and is not watched, for the same reason the customer's phone number is not.
#}
{% snapshot accounts_snapshot %}
{{
    config(
        target_schema='snapshots',
        unique_key='account_id',
        strategy='check',
        check_cols=['product_code', 'status'],
        invalidate_hard_deletes=True
    )
}}
select * from {{ ref('stg_accounts') }}
{% endsnapshot %}
