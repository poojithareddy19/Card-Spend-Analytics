{#
    SCD Type 2 on the customer.

    The `check` strategy rather than `timestamp`: the onboarding platform sends a current-state
    snapshot with no change timestamp, which is the common case and the reason `check` exists. dbt
    compares the listed columns against the stored version and opens a new row when any of them move.

    Only three columns are watched. Watching every column would open a new version every time a
    customer corrected their phone number, and a fact table that resolves its SCD2 key at load would
    then carry a different key for no analytically meaningful reason.
#}
{% snapshot customers_snapshot %}
{{
    config(
        target_schema='snapshots',
        unique_key='customer_id',
        strategy='check',
        check_cols=['segment', 'kyc_status', 'kyc_risk_band'],
        invalidate_hard_deletes=True
    )
}}
select * from {{ ref('stg_customers') }}
{% endsnapshot %}
