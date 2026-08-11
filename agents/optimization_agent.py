"""
SHIELD Agent 7: Optimization Agent
Per Step 10 spec: recommends execution-plan adjustments (join strategy,
partitioning, caching) based on execution statistics. Lowest LLM involvement
of all seven agents by design -- primarily deterministic analysis of
Postgres's own EXPLAIN ANALYZE output, with LLM used only for high-level
strategy narration, not execution-plan generation itself.
"""
import json
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

SLOW_QUERY_THRESHOLD_MS = 1000  # flag queries slower than this for optimization review


def call_llm(prompt: str, max_retries: int = 3, retry_delay: float = 5.0) -> str:
    import time
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                OLLAMA_URL,
                json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
                timeout=180,
            )
            response.raise_for_status()
            return response.json()["response"]
        except (Exception,) as e:
            import requests as _requests
            if isinstance(e, (_requests.exceptions.ConnectionError, _requests.exceptions.RequestException)):
                last_error = e
                if attempt < max_retries:
                    print(f"  [call_llm retry {attempt}/{max_retries}] {type(e).__name__}: {str(e)[:100]} -- retrying in {retry_delay}s")
                    time.sleep(retry_delay)
            else:
                raise
    raise ConnectionError(f"Ollama call failed after {max_retries} attempts: {last_error}")


def get_execution_plan(sql: str) -> dict:
    """Deterministic: run EXPLAIN ANALYZE and parse key statistics."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}")
            plan_json = cur.fetchone()[0][0]
        conn.rollback()  # EXPLAIN ANALYZE actually executes -- rollback any side effects
        return plan_json
    finally:
        conn.close()


def extract_plan_stats(plan_json: dict) -> dict:
    """Deterministic: pull key stats from the EXPLAIN plan tree."""
    root = plan_json["Plan"]

    def walk(node, node_types=None):
        node_types = node_types or []
        node_types.append(node["Node Type"])
        for child in node.get("Plans", []):
            walk(child, node_types)
        return node_types

    all_node_types = walk(root)

    return {
        "total_execution_time_ms": plan_json.get("Execution Time", 0),
        "total_planning_time_ms": plan_json.get("Planning Time", 0),
        "top_level_node": root["Node Type"],
        "estimated_rows": root.get("Plan Rows"),
        "actual_rows": root.get("Actual Rows"),
        "node_types_used": all_node_types,
        "has_seq_scan": "Seq Scan" in all_node_types,
        "has_nested_loop": "Nested Loop" in all_node_types,
        "has_hash_join": "Hash Join" in all_node_types,
        "has_sort": "Sort" in all_node_types,
    }


def recommend_optimizations(stats: dict, sql: str) -> str:
    """LLM role: high-level strategy narration based on deterministic stats.
    Does NOT generate the execution plan itself -- only interprets it."""
    prompt = f"""Given this query execution statistics:
{json.dumps(stats, indent=2)}

Query:
{sql}

Provide 2-3 brief, concrete optimization recommendations (e.g., adding an
index, restructuring a join, considering partitioning) based specifically
on the statistics shown. If the query already performs well
(execution time under {SLOW_QUERY_THRESHOLD_MS}ms, no problematic Seq Scans
on large tables), state that no optimization is needed. Be concise."""
    return call_llm(prompt)


def optimize(sql: str) -> dict:
    """
    Main entry point. Analyzes a query's actual execution plan and
    recommends optimizations only when statistics indicate a real issue.
    """
    plan_json = get_execution_plan(sql)
    stats = extract_plan_stats(plan_json)

    needs_review = (
        stats["total_execution_time_ms"] > SLOW_QUERY_THRESHOLD_MS
        or (stats["has_seq_scan"] and stats.get("actual_rows", 0) and stats["actual_rows"] > 10000)
    )

    result = {
        "status": "success",
        "confidence": "high",
        "stats": stats,
        "needs_review": needs_review,
    }

    if needs_review:
        result["recommendations"] = recommend_optimizations(stats, sql)
    else:
        result["recommendations"] = "No optimization needed -- execution time and plan structure within acceptable bounds."

    return result


if __name__ == "__main__":
    print("=== Test 1: TPC-H regional aggregation (same task as all baselines/agents) ===")
    sql1 = """
        SELECT c.c_nationkey AS nation_key,
               SUM(o.o_totalprice) AS total_order_value,
               COUNT(o.o_orderkey) AS order_count
        FROM tpch.orders o
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        GROUP BY c.c_nationkey
        ORDER BY nation_key;
    """
    result1 = optimize(sql1)
    print(json.dumps(result1, indent=2))

    print("\n=== Test 2: Larger join across lineitem (6M rows) -- likely needs review ===")
    sql2 = """
        SELECT l.l_returnflag, l.l_linestatus,
               SUM(l.l_quantity) AS sum_qty,
               SUM(l.l_extendedprice) AS sum_price,
               AVG(l.l_quantity) AS avg_qty
        FROM tpch.lineitem l
        JOIN tpch.orders o ON l.l_orderkey = o.o_orderkey
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        GROUP BY l.l_returnflag, l.l_linestatus;
    """
    result2 = optimize(sql2)
    print(json.dumps(result2, indent=2))

    with open("optimization_test_results.json", "w") as f:
        json.dump({"test1": result1, "test2": result2}, f, indent=2)
