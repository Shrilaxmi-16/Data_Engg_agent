"""
Baseline A: Rule-Based ETL (No LLM)
Pre-registered per Step 7 specification. Hardcoded SQL, no dynamic generation,
no diagnosis/repair logic beyond Airflow's native retry mechanism.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.operators.python import PythonOperator

DAG_ID = "baseline_a_rule_based_etl"
POSTGRES_CONN_ID = "shield_postgres"

HARDCODED_SQL = """
    SELECT
        c.c_nationkey AS nation_key,
        SUM(o.o_totalprice) AS total_order_value,
        COUNT(*) AS order_count
    FROM tpch.orders o
    JOIN tpch.customer c ON o.o_custkey = c.c_custkey
    GROUP BY c.c_nationkey
    ORDER BY c.c_nationkey;
"""

CREATE_TARGET_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS analytics.regional_orders (
        nation_key INTEGER PRIMARY KEY,
        total_order_value NUMERIC(18,2),
        order_count INTEGER,
        run_timestamp TIMESTAMP DEFAULT now()
    );
    TRUNCATE analytics.regional_orders;
"""

INSERT_SQL_TEMPLATE = """
    INSERT INTO analytics.regional_orders (nation_key, total_order_value, order_count)
    VALUES (%s, %s, %s);
"""


def run_etl(**context):
    """
    Fixed, hardcoded ETL logic. No LLM, no dynamic SQL generation.
    On failure (e.g., due to an injected schema-drift fault), this raises
    an exception, and Airflow's native retry mechanism is the ONLY recovery
    path -- by design, per Baseline A's specification.
    """
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    # Step 1: ensure target table exists
    hook.run(CREATE_TARGET_TABLE_SQL)

    # Step 2: extract + transform via hardcoded join/aggregate query
    records = hook.get_records(HARDCODED_SQL)

    if not records:
        raise ValueError("No records returned from source query -- treating as failure, no recovery logic exists in this baseline.")

    # Step 3: load into analytics schema
    conn = hook.get_conn()
    cursor = conn.cursor()
    for row in records:
        cursor.execute(INSERT_SQL_TEMPLATE, row)
    conn.commit()
    cursor.close()
    conn.close()

    print(f"Baseline A: loaded {len(records)} rows into analytics.regional_orders")


default_args = {
    "owner": "shield-project",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": False,  # fixed backoff per Step 7 spec, not adaptive
}

with DAG(
    dag_id=DAG_ID,
    description="Baseline A: Rule-based ETL, no LLM, no self-healing (Step 7 pre-registered spec)",
    default_args=default_args,
    schedule=None,  # manually triggered for controlled experiment runs
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["shield", "baseline-a"],
) as dag:

    run_etl_task = PythonOperator(
        task_id="run_rule_based_etl",
        python_callable=run_etl,
    )
