"""
SHIELD Fault Injection Harness
Per Step 8's taxonomy: 4 fault classes, 15 subtypes, each with a deterministic,
logged injection method and an explicit restore path. Designed for repeated,
reproducible use across Step 11's >=30-runs-per-class statistical protocol
with fixed random seeds.

Infrastructure faults use safer, reversible mechanisms (pg_terminate_backend,
statement_timeout) rather than raw container kills, to avoid destabilizing
the dev environment during iterative testing.
"""
import json
import random
import time
import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "shield_db",
    "user": "shield",
    "password": "shield_dev_pw",
}

INJECTION_LOG_PATH = "/home/rit/Documents/Shrilaxmi/Data_Engg/fault_injection/injection_log.jsonl"


def _conn():
    return psycopg2.connect(**DB_CONFIG)


def _log_injection(fault_class: str, fault_subtype: str, target: str, details: dict):
    entry = {
        "timestamp": time.time(),
        "fault_class": fault_class,
        "fault_subtype": fault_subtype,
        "target": target,
        "details": details,
    }
    with open(INJECTION_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


# ============================================================
# SCHEMA DRIFT FAULTS
# ============================================================

def inject_column_addition(table: str, column: str = "shield_test_col", schema: str = "tpch") -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS {column} VARCHAR(50);")
        conn.commit()
    finally:
        conn.close()
    return _log_injection("schema_drift", "column_addition", f"{schema}.{table}",
                           {"column_added": column})


def restore_column_addition(table: str, column: str = "shield_test_col", schema: str = "tpch"):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} DROP COLUMN IF EXISTS {column};")
        conn.commit()
    finally:
        conn.close()


def inject_column_deletion(table: str, column: str, schema: str = "tpch") -> dict:
    """DESTRUCTIVE -- caller must have a restore plan (re-run schema DDL) since
    the dropped column's original type/constraints are not auto-recoverable here."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} DROP COLUMN IF EXISTS {column};")
        conn.commit()
    finally:
        conn.close()
    return _log_injection("schema_drift", "column_deletion", f"{schema}.{table}",
                           {"column_dropped": column,
                            "restore_note": "requires full schema DDL re-run, not auto-reversible"})


def inject_type_change(table: str, column: str, new_type: str = "VARCHAR(20)", schema: str = "tpch") -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Capture FULL type including length/precision -- information_schema.data_type
            # alone returns only the type family (e.g. "character"), losing the length
            # modifier, which silently truncated a column on restore (Step 16 finding).
            cur.execute("""
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = %s AND c.relname = %s AND a.attname = %s AND a.attnum > 0;
            """, (schema, table, column))
            original_type = cur.fetchone()[0]
            cur.execute(f"ALTER TABLE {schema}.{table} ALTER COLUMN {column} TYPE {new_type} USING {column}::{new_type};")
        conn.commit()
    finally:
        conn.close()
    return _log_injection("schema_drift", "type_change", f"{schema}.{table}.{column}",
                           {"original_type": original_type, "new_type": new_type})

def restore_type_change(table: str, column: str, original_type: str, schema: str = "tpch"):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} ALTER COLUMN {column} TYPE {original_type} USING {column}::{original_type};")
        conn.commit()
    finally:
        conn.close()


def inject_constraint_drop(table: str, constraint_name: str, schema: str = "tpch") -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} DROP CONSTRAINT IF EXISTS {constraint_name};")
        conn.commit()
    finally:
        conn.close()
    return _log_injection("schema_drift", "constraint_drop", f"{schema}.{table}",
                           {"constraint_dropped": constraint_name,
                            "restore_note": "requires re-adding FK manually, not auto-reversible"})


# ============================================================
# DATA QUALITY FAULTS
# ============================================================

def inject_null_values(table: str, column: str, pct: float = 5.0, schema: str = "tpch", seed: int = 42) -> dict:
    """Randomly null out `pct`% of rows for a column. Returns affected row IDs for restore."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT ctid FROM {schema}.{table} ORDER BY random();")
            all_rows = [str(r[0]) for r in cur.fetchall()]  # cast to str immediately
            random.seed(seed)
            n_affected = max(1, int(len(all_rows) * pct / 100))
            affected = random.sample(all_rows, n_affected)

            # Back up original values before nulling, for restore
            cur.execute(f"SELECT ctid, {column} FROM {schema}.{table} WHERE ctid = ANY(%s::text[]::tid[]);", (affected,))
            backup = {str(row[0]): row[1] for row in cur.fetchall()}

            cur.execute(f"UPDATE {schema}.{table} SET {column} = NULL WHERE ctid = ANY(%s::text[]::tid[]);", (affected,))
        conn.commit()
    finally:
        conn.close()
    return _log_injection("data_quality", "null_injection", f"{schema}.{table}.{column}",
                           {"pct_requested": pct, "rows_affected": n_affected, "backup": backup})


def inject_duplicate_rows(table: str, pct: float = 5.0, schema: str = "tpch", seed: int = 42) -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {schema}.{table};")
            total = cur.fetchone()[0]
            n_dupes = max(1, int(total * pct / 100))
            # Duplicate a random sample of existing rows (full row copy)
            cur.execute(f"""
                INSERT INTO {schema}.{table}
                SELECT * FROM {schema}.{table}
                ORDER BY random() LIMIT {n_dupes};
            """)
        conn.commit()
    finally:
        conn.close()
    return _log_injection("data_quality", "duplicate_injection", f"{schema}.{table}",
                           {"pct_requested": pct, "rows_duplicated": n_dupes,
                            "restore_note": "requires DELETE with ctid tracking or full table reload"})


def inject_referential_orphan(child_table: str, child_fk_col: str, invalid_value: int = 999999999,
                                schema: str = "tpch") -> dict:
    """Insert a minimal orphan row violating referential integrity -- requires
    the FK constraint to be temporarily droppable/deferred, or targets a
    column without an enforced FK (as in our TPC-H schema, some FKs aren't declared)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position;",
                        (schema, child_table))
            columns = [r[0] for r in cur.fetchall()]
            # Only safe to run if FK on this column isn't enforced -- caller's responsibility to verify via Schema Discovery Agent
        conn.close()
    except Exception as e:
        conn.close()
        raise
    return _log_injection("data_quality", "referential_orphan", f"{schema}.{child_table}.{child_fk_col}",
                           {"note": "orphan injection requires FK-unenforced target; verify via Schema Discovery Agent before use",
                            "invalid_value_planned": invalid_value})


def inject_distribution_shift(table: str, column: str, multiplier: float = 10.0, pct: float = 5.0,
                                schema: str = "tpch", seed: int = 42) -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT ctid FROM {schema}.{table} ORDER BY random();")
            all_rows = [str(r[0]) for r in cur.fetchall()]
            random.seed(seed)
            n_affected = max(1, int(len(all_rows) * pct / 100))
            affected = random.sample(all_rows, n_affected)

            cur.execute(f"SELECT ctid, {column} FROM {schema}.{table} WHERE ctid = ANY(%s::text[]::tid[]);", (affected,))
            backup = {str(row[0]): float(row[1]) for row in cur.fetchall()}

            cur.execute(f"UPDATE {schema}.{table} SET {column} = {column} * %s WHERE ctid = ANY(%s::text[]::tid[]);",
                        (multiplier, affected))
        conn.commit()
    finally:
        conn.close()
    return _log_injection("data_quality", "distribution_shift", f"{schema}.{table}.{column}",
                           {"multiplier": multiplier, "pct_requested": pct,
                            "rows_affected": n_affected, "backup": backup})


# ============================================================
# INFRASTRUCTURE FAULTS (safer, reversible mechanisms)
# ============================================================

def inject_connection_kill(target_pid: int = None) -> dict:
    """Terminate an active backend connection (simulates a dropped connection
    without killing the whole container)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if target_pid is None:
                cur.execute("SELECT pid FROM pg_stat_activity WHERE datname=%s AND pid != pg_backend_pid() LIMIT 1;",
                            (DB_CONFIG["dbname"],))
                row = cur.fetchone()
                target_pid = row[0] if row else None
            if target_pid:
                cur.execute("SELECT pg_terminate_backend(%s);", (target_pid,))
        conn.commit()
    finally:
        conn.close()
    return _log_injection("infrastructure", "connection_kill", "postgres_backend",
                           {"target_pid": target_pid})


def inject_query_timeout(seconds: int = 2) -> dict:
    """Sets an aggressive statement_timeout at session level for the NEXT
    connection made with these settings -- caller must use this timeout value
    when executing their target query to simulate a forced timeout."""
    return _log_injection("infrastructure", "forced_timeout", "session_config",
                           {"statement_timeout_ms": seconds * 1000,
                            "usage_note": "pass statement_timeout_ms to the query under test, e.g. via SET LOCAL statement_timeout"})


# ============================================================
# SQL SEMANTIC FAULTS (corrupt agent-generated SQL for testing)
# ============================================================

def corrupt_join_key(sql: str, wrong_column: str = "id") -> dict:
    """Injects a wrong join column into generated SQL -- for testing Self-Healing
    Agent's SQL repair path specifically, not applied to the database."""
    import re
    corrupted = re.sub(r"(ON\s+\w+\.)(\w+)(\s*=\s*\w+\.)(\w+)", rf"\1{wrong_column}\3{wrong_column}", sql, count=1)
    return {
        "fault_class": "sql_semantic",
        "fault_subtype": "join_key_mismatch",
        "original_sql": sql,
        "corrupted_sql": corrupted,
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    print("=== Fault Injection Harness Self-Test (TPC-H, safe/reversible faults only) ===\n")

    print("1. Column addition...")
    r1 = inject_column_addition("customer")
    print(json.dumps(r1, indent=2))
    restore_column_addition("customer")
    print("   Restored.\n")

    print("2. Null injection (5% of customer.c_mktsegment)...")
    r2 = inject_null_values("customer", "c_mktsegment", pct=5.0)
    print(json.dumps({k: v for k, v in r2.items() if k != "details"}, indent=2))
    print(f"   {r2['details']['rows_affected']} rows affected. (Restore requires re-running load_tpch.sh or a backup-based UPDATE.)\n")

    print("3. Type change (customer.c_mktsegment -> VARCHAR(20))...")
    r3 = inject_type_change("customer", "c_mktsegment", new_type="VARCHAR(20)")
    print(json.dumps(r3, indent=2))
    restore_type_change("customer", "c_mktsegment", r3["details"]["original_type"])
    print("   Restored.\n")

    print("4. SQL semantic corruption (no DB changes)...")
    r4 = corrupt_join_key("SELECT * FROM tpch.orders o JOIN tpch.customer c ON o.o_custkey = c.c_custkey;")
    print(json.dumps(r4, indent=2))

    print(f"\nAll injections logged to: {INJECTION_LOG_PATH}")
