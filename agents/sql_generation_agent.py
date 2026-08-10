"""
SHIELD Agent 2: SQL Generation Agent
Per Step 10 spec: schema-linking -> candidate generation -> self-refinement,
informed by CHESS's structure (Baseline C, Step 13). Consumes Schema Discovery
Agent's output (schema_discovery_agent.py) as its Knowledge input.
"""
import json
import re
import time
import requests
import psycopg2

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"  # SHIELD default per Step 10

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "shield_db",
    "user": "shield",
    "password": "shield_dev_pw",
}

MAX_REFINEMENT_ATTEMPTS = 3


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

def format_schema_for_prompt(schema_knowledge: dict) -> str:
    """Convert Schema Discovery Agent's output into a compact prompt-ready form.
    Composite FKs are grouped explicitly and flagged, since LLMs tend to silently
    drop columns from multi-column join conditions if listed as separate lines."""
    schema_name = schema_knowledge["schema_name"]
    lines = []
    for table, info in schema_knowledge["tables"].items():
        cols = ", ".join([f"{c['column']} {c['type']}" for c in info["columns"]])
        pk = ", ".join(info["primary_keys"])
        lines.append(f"{schema_name}.{table}({cols}) [PK: {pk}]")

    # Group FKs by (from_table, to_table) pair -- composite keys share this pair
    from collections import defaultdict
    grouped = defaultdict(list)
    for fk in schema_knowledge["foreign_keys"]:
        grouped[(fk["from_table"], fk["to_table"])].append((fk["from_column"], fk["to_column"]))

    fk_lines = []
    for (from_table, to_table), pairs in grouped.items():
        if len(pairs) > 1:
            join_conditions = " AND ".join([f"{from_table}.{fc} = {to_table}.{tc}" for fc, tc in pairs])
            fk_lines.append(
                f"COMPOSITE KEY (all {len(pairs)} conditions REQUIRED together, do not omit any): {join_conditions}"
            )
        else:
            fc, tc = pairs[0]
            fk_lines.append(f"{from_table}.{fc} -> {to_table}.{tc}")

    return (
        "Tables:\n" + "\n".join(lines) +
        "\n\nForeign keys (join conditions):\n" + ("\n".join(fk_lines) if fk_lines else "(none declared)")
    )

def stage1_schema_linking(task_description: str, schema_text: str) -> str:
    """Identify which tables/columns are relevant to the task."""
    prompt = f"""Given this database schema:
{schema_text}

Task: {task_description}

List ONLY the tables and columns relevant to this task, and the join path
needed between them (using the foreign keys shown). Be concise, no SQL yet."""
    return call_llm(prompt)


def stage2_candidate_generation(task_description: str, schema_text: str, linking_output: str) -> str:
    """Generate SQL candidate given the schema-linking analysis."""
    prompt = f"""Database schema:
{schema_text}

Schema-linking analysis:
{linking_output}

Task: {task_description}

Write ONLY a PostgreSQL query wrapped in ```sql``` fences. No explanation."""
    output = call_llm(prompt)
    return extract_sql(output)


def stage3_self_refinement(task_description: str, schema_text: str, failed_sql: str, error: str) -> str:
    """Given an execution error, produce a corrected candidate."""
    prompt = f"""Database schema:
{schema_text}

Task: {task_description}

This SQL failed:
{failed_sql}

Error:
{error}

Write a CORRECTED PostgreSQL query wrapped in ```sql``` fences. No explanation."""
    output = call_llm(prompt)
    return extract_sql(output)


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


def generate_sql(task_description: str, schema_knowledge: dict) -> dict:
    """
    Main entry point. Returns a result object with status, confidence,
    and full attempt history -- matching Step 10's inter-agent contract
    (status field + confidence field for downstream agents).
    """
    schema_text = format_schema_for_prompt(schema_knowledge)
    log = {"task": task_description, "attempts": []}

    t0 = time.time()
    linking_output = stage1_schema_linking(task_description, schema_text)
    log["schema_linking_latency"] = round(time.time() - t0, 2)
    log["schema_linking_output"] = linking_output

    t1 = time.time()
    sql = stage2_candidate_generation(task_description, schema_text, linking_output)
    log["candidate_generation_latency"] = round(time.time() - t1, 2)

    for attempt in range(1, MAX_REFINEMENT_ATTEMPTS + 1):
        success, result = execute_sql(sql)
        log["attempts"].append({
            "attempt": attempt,
            "sql": sql,
            "success": success,
            "error": None if success else result,
        })

        if success:
            return {
                "status": "success",
                "confidence": "high" if attempt == 1 else "medium",
                "sql": sql,
                "row_count": len(result) if result else 0,
                "attempts_needed": attempt,
                "log": log,
            }

        if attempt < MAX_REFINEMENT_ATTEMPTS:
            t_refine = time.time()
            sql = stage3_self_refinement(task_description, schema_text, sql, result)
            log["attempts"][-1]["refinement_latency"] = round(time.time() - t_refine, 2)

    return {
        "status": "failure",
        "confidence": "low",
        "sql": sql,
        "row_count": None,
        "attempts_needed": MAX_REFINEMENT_ATTEMPTS,
        "log": log,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from schema_discovery_agent import discover_schema

    schema_name = sys.argv[1] if len(sys.argv) > 1 else "tpch"
    task = sys.argv[2] if len(sys.argv) > 2 else (
        "Aggregate total order value and order count by customer nation, "
        "using the orders and customer tables. Return nation_key, "
        "total_order_value, order_count, ordered by nation_key."
    )

    print(f"Running Schema Discovery for '{schema_name}'...")
    schema_knowledge = discover_schema(schema_name)

    print(f"Running SQL Generation for task: {task}")
    result = generate_sql(task, schema_knowledge)

    print(json.dumps({k: v for k, v in result.items() if k != "log"}, indent=2))
    with open(f"sql_generation_result_{schema_name}.json", "w") as f:
        json.dump(result, f, indent=2)
