"""
SHIELD Agent 3: Transformation Agent
Per Step 10 spec: recommends null-handling/dedup/standardization rules (LLM),
applies them deterministically. Operates on the result set of a query
(typically the SQL Generation Agent's output), not raw tables directly.
"""
import json
import re
import time
import requests
import psycopg2
import psycopg2.extras

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "shield_db",
    "user": "shield",
    "password": "shield_dev_pw",
}


def call_llm(prompt: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"]


def fetch_result_set(sql: str):
    """Execute SQL and return column names + rows as list of dicts."""
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
            columns = list(rows[0].keys()) if rows else []
            return columns, rows
    finally:
        conn.close()


def profile_data(columns: list, rows: list) -> dict:
    """Deterministic data profiling: null counts, duplicate counts per column."""
    profile = {"row_count": len(rows), "columns": {}}
    for col in columns:
        values = [r[col] for r in rows]
        null_count = sum(1 for v in values if v is None)
        distinct_count = len(set(v for v in values if v is not None))
        profile["columns"][col] = {
            "null_count": null_count,
            "null_pct": round(null_count / len(rows) * 100, 2) if rows else 0,
            "distinct_count": distinct_count,
        }
    # Full-row duplicate check
    seen = set()
    dup_count = 0
    for r in rows:
        key = tuple(sorted(r.items()))
        if key in seen:
            dup_count += 1
        seen.add(key)
    profile["duplicate_row_count"] = dup_count
    return profile


def recommend_transformations(profile: dict, task_context: str) -> str:
    """LLM role: recommend transformation rules based on the data profile."""
    prompt = f"""Given this data profile from a query result:
{json.dumps(profile, indent=2)}

Task context: {task_context}

Recommend data cleaning/transformation rules to apply, choosing ONLY from:
- DROP_NULLS(column): remove rows where column is null
- FILL_DEFAULT(column, value): replace nulls with a default value
- DEDUPLICATE: remove exact duplicate rows
- NONE: no transformation needed

Output ONLY a JSON list of rule objects, e.g.:
[{{"rule": "DROP_NULLS", "column": "total_order_value"}}, {{"rule": "DEDUPLICATE"}}]
If no transformation is needed, output: []
No explanation, only the JSON list."""
    return call_llm(prompt)


def parse_rules(llm_output: str) -> list:
    """Extract JSON rule list from LLM output, tolerant of markdown fences."""
    match = re.search(r"```json\s*(.*?)\s*```", llm_output, re.DOTALL)
    text = match.group(1) if match else llm_output
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    text = match.group(1) if match else text
    try:
        rules = json.loads(text)
        return rules if isinstance(rules, list) else []
    except json.JSONDecodeError:
        return []


def apply_rules(columns: list, rows: list, rules: list) -> tuple:
    """Deterministic rule application. Returns (transformed_rows, applied_log)."""
    applied_log = []
    result = list(rows)

    for rule in rules:
        rule_type = rule.get("rule")
        before_count = len(result)

        if rule_type == "DROP_NULLS":
            col = rule.get("column")
            if col in columns:
                result = [r for r in result if r.get(col) is not None]
                applied_log.append({
                    "rule": "DROP_NULLS", "column": col,
                    "rows_before": before_count, "rows_after": len(result),
                    "rows_removed": before_count - len(result),
                })

        elif rule_type == "FILL_DEFAULT":
            col = rule.get("column")
            default = rule.get("value")
            if col in columns:
                filled_count = 0
                for r in result:
                    if r.get(col) is None:
                        r[col] = default
                        filled_count += 1
                applied_log.append({
                    "rule": "FILL_DEFAULT", "column": col, "value": default,
                    "rows_filled": filled_count,
                })

        elif rule_type == "DEDUPLICATE":
            seen = set()
            deduped = []
            for r in result:
                key = tuple(sorted(r.items()))
                if key not in seen:
                    deduped.append(r)
                    seen.add(key)
            applied_log.append({
                "rule": "DEDUPLICATE",
                "rows_before": before_count, "rows_after": len(deduped),
                "rows_removed": before_count - len(deduped),
            })
            result = deduped

        elif rule_type == "NONE":
            applied_log.append({"rule": "NONE", "note": "no transformation applied"})

    return result, applied_log


def transform(sql: str, task_context: str) -> dict:
    """
    Main entry point. Takes SQL (typically from SQL Generation Agent),
    executes it, profiles the result, recommends and applies transformations.
    Returns status/confidence per Step 10's inter-agent contract.
    """
    log = {"sql": sql, "task_context": task_context}

    columns, rows = fetch_result_set(sql)
    log["original_row_count"] = len(rows)

    if not rows:
        return {
            "status": "success", "confidence": "high",
            "row_count": 0, "rules_applied": [],
            "log": {**log, "note": "empty result set, no transformation needed"},
        }

    profile = profile_data(columns, rows)
    log["data_profile"] = profile

    t0 = time.time()
    llm_output = recommend_transformations(profile, task_context)
    log["recommendation_latency"] = round(time.time() - t0, 2)
    log["llm_recommendation_raw"] = llm_output

    rules = parse_rules(llm_output)
    log["parsed_rules"] = rules

    transformed_rows, applied_log = apply_rules(columns, rows, rules)
    log["applied_transformations"] = applied_log
    log["final_row_count"] = len(transformed_rows)

    return {
        "status": "success",
        "confidence": "high" if rules else "medium",  # no rules recommended = agent found nothing to fix, still valid
        "row_count": len(transformed_rows),
        "rules_applied": [r.get("rule") for r in rules],
        "log": log,
    }


if __name__ == "__main__":
    import sys

    test_sql = sys.argv[1] if len(sys.argv) > 1 else """
        SELECT c.c_nationkey AS nation_key,
               SUM(o.o_totalprice) AS total_order_value,
               COUNT(o.o_orderkey) AS order_count
        FROM tpch.orders o
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        GROUP BY c.c_nationkey
        ORDER BY nation_key;
    """
    task_context = "Regional order value aggregation for analytics reporting."

    result = transform(test_sql, task_context)
    print(json.dumps({k: v for k, v in result.items() if k != "log"}, indent=2))
    print("\n--- Data Profile ---")
    print(json.dumps(result["log"].get("data_profile", {}), indent=2))

    with open("transformation_result.json", "w") as f:
        json.dump(result, f, indent=2)
