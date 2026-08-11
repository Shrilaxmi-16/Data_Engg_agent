"""
Baseline B: Single-LLM, Single-Agent (No Decomposition)
Pre-registered per Step 7/10 spec: one undifferentiated LLM call handles
schema understanding + SQL generation + error correction, using the same
model (qwen2.5:14b) SHIELD's SQL Generation Agent will default to.
"""
import json
import re
import time
import requests
import psycopg2

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "shield_db",
    "user": "shield",
    "password": "shield_dev_pw",
}

TASK_DESCRIPTION = (
    "Aggregate total order value and order count by customer nation, "
    "using the tpch.orders and tpch.customer tables (join on custkey). "
    "Return nation_key, total_order_value, order_count, ordered by nation_key."
)

SCHEMA_CONTEXT = """
Available tables:
tpch.orders(o_orderkey, o_custkey, o_orderstatus, o_totalprice, o_orderdate, o_orderpriority, o_clerk, o_shippriority, o_comment)
tpch.customer(c_custkey, c_name, c_address, c_nationkey, c_phone, c_acctbal, c_mktsegment, c_comment)
"""

MAX_RETRIES = 3


def call_llm(prompt: str) -> str:
    """Single undifferentiated LLM call -- no agent decomposition."""
    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]


def extract_sql(llm_output: str) -> str:
    """Extract SQL from LLM response, handling markdown code fences."""
    match = re.search(r"```sql\s*(.*?)\s*```", llm_output, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)\s*```", llm_output, re.DOTALL)
    if match:
        return match.group(1).strip()
    return llm_output.strip()


def execute_sql(sql: str):
    """Attempt execution; return (success, result_or_error)."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                rows = cur.fetchall()
                conn.commit()
                return True, rows
            conn.commit()
            return True, None
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def run_baseline_b():
    log = {"attempts": [], "final_status": None, "final_sql": None, "row_count": None}

    prompt = f"""You are a data engineer. Given this database schema:
{SCHEMA_CONTEXT}

Task: {TASK_DESCRIPTION}

Write ONLY the PostgreSQL query, wrapped in ```sql``` markdown fences. No explanation."""

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"--- Attempt {attempt} ---")
        start = time.time()
        llm_output = call_llm(prompt)
        elapsed = time.time() - start
        sql = extract_sql(llm_output)
        print(f"Generated SQL:\n{sql}\n")

        success, result = execute_sql(sql)
        log["attempts"].append({
            "attempt": attempt,
            "sql": sql,
            "llm_latency_sec": round(elapsed, 2),
            "success": success,
            "error": None if success else result,
        })

        if success:
            log["final_status"] = "success"
            log["final_sql"] = sql
            log["row_count"] = len(result) if result else 0
            print(f"SUCCESS on attempt {attempt}. Rows returned: {log['row_count']}")
            break
        else:
            print(f"FAILED: {result}")
            # Single-agent self-correction: feed the error back into the SAME undifferentiated prompt
            prompt = f"""You are a data engineer. Given this database schema:
{SCHEMA_CONTEXT}

Task: {TASK_DESCRIPTION}

Your previous SQL attempt failed with this error:
{result}

Previous SQL:
{sql}

Write a CORRECTED PostgreSQL query, wrapped in ```sql``` markdown fences. No explanation."""
    else:
        log["final_status"] = "failed_after_max_retries"

    with open("baseline_b_run_log.json", "w") as f:
        json.dump(log, f, indent=2)

    print(f"\nFinal status: {log['final_status']}")
    return log


if __name__ == "__main__":
    run_baseline_b()
