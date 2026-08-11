"""
Baseline D: In-Context Self-Orchestration (SCOPED/INTERIM VERSION)
Per Step 7 spec, this should use SHIELD's actual seven-agent prompts --
those don't exist yet (built in Steps 14-17). This interim version uses
a three-role decomposition (schema understanding, SQL generation, validation)
matching Step 10's architecture spec, run as ONE sequential in-context prompt
chain (no external orchestrator/state machine), on the same task as A/B/C.

TO REVISIT: once Steps 14-17 produce SHIELD's real agent prompts, swap them
into this same single-context-chain pattern for the final Baseline D used
in Step 18's actual experimental campaign.
"""
import json
import re
import time
import requests
import psycopg2

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"  # matches Baseline B and SHIELD's default per Step 10

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


def call_llm(prompt: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"]


def extract_sql(text: str) -> str:
    match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def execute_sql(sql: str):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() if cur.description else None
            conn.commit()
            return True, rows
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def run_baseline_d():
    """
    Single sequential in-context chain: one accumulated prompt/context carries
    forward through all three 'roles' -- the model plays each role in turn
    within ONE conversation, no external state machine routing between them.
    """
    log = {"stages": [], "final_status": None}
    accumulated_context = f"""You are acting as a sequence of specialized roles to complete a data task.
Work through each role in order, using the outputs of prior roles as context.

=== SCHEMA CONTEXT ===
{SCHEMA_CONTEXT}

=== TASK ===
{TASK_DESCRIPTION}

"""

    # Role 1: Schema Understanding (in-context, same conversation)
    role1_prompt = accumulated_context + """
--- ROLE 1: SCHEMA UNDERSTANDING ---
Identify which tables and columns are relevant to this task, and describe
the join relationship needed. Be concise."""
    t0 = time.time()
    role1_output = call_llm(role1_prompt)
    log["stages"].append({"role": "schema_understanding", "latency_sec": round(time.time() - t0, 2), "output": role1_output})
    accumulated_context += f"\n--- ROLE 1 OUTPUT (Schema Understanding) ---\n{role1_output}\n"

    # Role 2: SQL Generation (in-context, carries Role 1's output forward)
    role2_prompt = accumulated_context + """
--- ROLE 2: SQL GENERATION ---
Based on the schema understanding above, write ONLY a PostgreSQL query
wrapped in ```sql``` fences to complete the task. No explanation."""
    t1 = time.time()
    role2_output = call_llm(role2_prompt)
    sql = extract_sql(role2_output)
    log["stages"].append({"role": "sql_generation", "latency_sec": round(time.time() - t1, 2), "sql": sql})
    accumulated_context += f"\n--- ROLE 2 OUTPUT (Generated SQL) ---\n{sql}\n"

    # Execute
    success, result = execute_sql(sql)
    log["execution_success"] = success

    if not success:
        # Role 3: Validation/Self-Correction (in-context, sees the error)
        role3_prompt = accumulated_context + f"""
--- ROLE 3: VALIDATION & CORRECTION ---
The SQL above failed with this error:
{result}

Write a CORRECTED PostgreSQL query wrapped in ```sql``` fences. No explanation."""
        t2 = time.time()
        role3_output = call_llm(role3_prompt)
        corrected_sql = extract_sql(role3_output)
        log["stages"].append({"role": "validation_correction", "latency_sec": round(time.time() - t2, 2), "sql": corrected_sql})

        success, result = execute_sql(corrected_sql)
        sql = corrected_sql
        log["execution_success"] = success

    log["final_sql"] = sql
    log["final_status"] = "success" if success else "failed"
    log["row_count"] = len(result) if success and result else 0
    log["error"] = None if success else result

    with open("baseline_d_run_log.json", "w") as f:
        json.dump(log, f, indent=2)

    print(f"Final status: {log['final_status']}")
    print(f"Row count: {log.get('row_count')}")
    return log


if __name__ == "__main__":
    run_baseline_d()
