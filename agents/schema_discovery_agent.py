"""
SHIELD Agent 1: Schema Discovery Agent
Per Step 10 spec: deterministic information_schema introspection (ground truth),
LLM used only for human-readable schema summaries fed to downstream agents.
"""
import json
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


def get_tables(schema_name: str, conn) -> list:
    """Deterministic: list all tables in a given schema."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """, (schema_name,))
        return [row[0] for row in cur.fetchall()]


def get_columns(schema_name: str, table_name: str, conn) -> list:
    """Deterministic: list columns, types, nullability for a table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position;
        """, (schema_name, table_name))
        return [
            {"column": r[0], "type": r[1], "nullable": r[2] == "YES", "default": r[3]}
            for r in cur.fetchall()
        ]


def get_primary_keys(schema_name: str, table_name: str, conn) -> list:
    """Deterministic: primary key columns for a table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
                AND tc.table_schema = %s AND tc.table_name = %s;
        """, (schema_name, table_name))
        return [row[0] for row in cur.fetchall()]

def get_foreign_keys(schema_name: str, conn) -> list:
    """
    Deterministic: all declared FK relationships within a schema.
    Uses pg_constraint + array ordinality to correctly pair composite-key
    columns (information_schema's key_column_usage/constraint_column_usage
    join produces a cartesian product for multi-column FKs).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                conrelid::regclass::text AS from_table,
                a.attname AS from_column,
                confrelid::regclass::text AS to_table,
                af.attname AS to_column
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
            JOIN LATERAL unnest(c.confkey) WITH ORDINALITY AS cf(attnum, ord) ON ck.ord = cf.ord
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ck.attnum
            JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = cf.attnum
            WHERE c.contype = 'f'
              AND c.connamespace = (SELECT oid FROM pg_namespace WHERE nspname = %s)
            ORDER BY conrelid::regclass::text, c.conname, ck.ord;
        """, (schema_name,))
        return [
            {"from_table": r[0].split(".")[-1], "from_column": r[1], "to_table": r[2].split(".")[-1], "to_column": r[3]}
            for r in cur.fetchall()
        ]

def discover_schema(schema_name: str) -> dict:
    """
    Main entry point. Returns the shared 'Knowledge' schema object
    consumed by all downstream agents.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        tables = get_tables(schema_name, conn)
        schema_repr = {"schema_name": schema_name, "tables": {}}

        for table in tables:
            schema_repr["tables"][table] = {
                "columns": get_columns(schema_name, table, conn),
                "primary_keys": get_primary_keys(schema_name, table, conn),
            }

        declared_fks = get_foreign_keys(schema_name, conn)
        schema_repr["foreign_keys"] = declared_fks

        # LLM role: generate a human-readable summary for downstream prompt context
        table_list_str = "\n".join([
            f"- {t}({', '.join([c['column'] for c in schema_repr['tables'][t]['columns']])})"
            for t in tables
        ])
        fk_str = "\n".join([
            f"- {fk['from_table']}.{fk['from_column']} -> {fk['to_table']}.{fk['to_column']}"
            for fk in declared_fks
        ]) or "(none declared)"

        summary_prompt = f"""Given this database schema:

Tables:
{table_list_str}

Declared foreign keys:
{fk_str}

Write a brief (3-5 sentence) human-readable summary of what this schema represents
and how the tables relate to each other. No SQL, just plain description."""

        schema_repr["llm_summary"] = call_llm(summary_prompt)
        return schema_repr

    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    schema_name = sys.argv[1] if len(sys.argv) > 1 else "tpch"
    result = discover_schema(schema_name)
    print(json.dumps(result, indent=2))
    with open(f"schema_discovery_{schema_name}.json", "w") as f:
        json.dump(result, f, indent=2)
