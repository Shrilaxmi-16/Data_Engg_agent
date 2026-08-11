"""
Re-score the pilot using set-based comparison (order-insensitive) instead of
strict tuple equality, matching standard text-to-SQL execution-accuracy protocol.
Does NOT re-run the LLM -- reuses already-generated SQL and results.
"""
import json
import sqlite3

BIRD_DB_DIR = "/home/rit/Documents/Shrilaxmi/Data_Engg/datasets/bird/dev_20240627/dev_databases"


def execute(db_id, sql):
    db_path = f"{BIRD_DB_DIR}/{db_id}/{db_id}.sqlite"
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
        return True, rows
    except Exception as e:
        return False, str(e)


def set_match(generated_rows, gold_rows):
    """Order-insensitive set comparison. Still strict on column count/values --
    does not yet forgive extra/missing columns (see note below)."""
    try:
        return set(generated_rows) == set(gold_rows)
    except TypeError:
        return sorted(map(str, generated_rows)) == sorted(map(str, gold_rows))


with open("chess_pilot_results_FINAL.json") as f:
    results = json.load(f)

rescored = 0
for r in results:
    if not r.get("execution_success") or not r.get("gold_execution_success"):
        continue
    gen_success, gen_rows = execute(r["db_id"], r["generated_sql"])
    gold_success, gold_rows = execute(r["db_id"], r["gold_sql"])
    if gen_success and gold_success:
        r["execution_match_setwise"] = set_match(gen_rows, gold_rows)
        rescored += 1

with open("chess_pilot_results_rescored.json", "w") as f:
    json.dump(results, f, indent=2)

total = len(results)
strict_match = sum(1 for r in results if r.get("execution_match"))
setwise_match = sum(1 for r in results if r.get("execution_match_setwise"))

print(f"Total examples: {total}")
print(f"Strict tuple-match accuracy (original): {strict_match/total:.3f}")
print(f"Set-based match accuracy (corrected):   {setwise_match/total:.3f}")
