"""
Baseline C: Reproduced CHESS (scoped pilot)
Four stages: Information Retriever -> Schema Selector -> Candidate Generator -> Unit Tester
Per Step 7 spec -- pilot run on 25 BIRD-dev examples, qwen2.5:7b, to estimate
feasibility of full reproduction before committing to a larger validation run.
"""
import json
import re
import sqlite3
import time
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
BIRD_DB_DIR = "/home/rit/Documents/Shrilaxmi/Data_Engg/datasets/bird/dev_20240627/dev_databases"


def call_llm(prompt: str, temperature: float = 0.1) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": temperature}},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"]


def get_full_schema(db_id: str) -> str:
    """Stage 0 helper: extract full schema (used by Information Retriever)."""
    db_path = f"{BIRD_DB_DIR}/{db_id}/{db_id}.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
    tables = cur.fetchall()
    conn.close()
    return "\n\n".join([sql for _, sql in tables if sql])


def stage1_information_retriever(question: str, evidence: str, full_schema: str) -> str:
    """Identify which tables/columns are relevant to the question (schema linking)."""
    prompt = f"""Given this database schema:
{full_schema}

Question: {question}
Additional context: {evidence}

List ONLY the table names and column names relevant to answering this question.
Format: table_name.column_name, one per line. No explanation."""
    return call_llm(prompt)


def stage2_schema_selector(full_schema: str, relevant_columns: str) -> str:
    """Narrow the schema down to just the relevant subset for prompt efficiency."""
    prompt = f"""Full schema:
{full_schema}

Relevant columns identified:
{relevant_columns}

Output ONLY the CREATE TABLE statements for tables that contain these relevant columns.
No explanation."""
    return call_llm(prompt)


def stage3_candidate_generator(question: str, evidence: str, selected_schema: str) -> str:
    """Generate SQL candidate given the narrowed schema."""
    prompt = f"""Database schema:
{selected_schema}

Question: {question}
Additional context: {evidence}

Write ONLY a SQLite-compatible SQL query wrapped in ```sql``` fences to answer this question. No explanation."""
    output = call_llm(prompt)
    match = re.search(r"```sql\s*(.*?)\s*```", output, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)\s*```", output, re.DOTALL)
    if match:
        return match.group(1).strip()
    return output.strip()


def stage4_unit_tester(db_id: str, sql: str):
    """Execute the candidate SQL; return (success, result_or_error)."""
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


def run_chess_pilot():
    with open("pilot_sample.json") as f:
        examples = json.load(f)

    results = []

    for i, ex in enumerate(examples):
        print(f"\n=== Example {i+1}/{len(examples)}: {ex['db_id']} ===")
        print(f"Q: {ex['question'][:100]}")

        start = time.time()
        entry = {
            "question_id": ex["question_id"],
            "db_id": ex["db_id"],
            "question": ex["question"],
            "gold_sql": ex["SQL"],
            "difficulty": ex.get("difficulty"),
        }

        try:
            full_schema = get_full_schema(ex["db_id"])

            t1 = time.time()
            relevant_cols = stage1_information_retriever(ex["question"], ex.get("evidence", ""), full_schema)
            entry["stage1_latency"] = round(time.time() - t1, 2)

            t2 = time.time()
            selected_schema = stage2_schema_selector(full_schema, relevant_cols)
            entry["stage2_latency"] = round(time.time() - t2, 2)

            t3 = time.time()
            candidate_sql = stage3_candidate_generator(ex["question"], ex.get("evidence", ""), selected_schema)
            entry["stage3_latency"] = round(time.time() - t3, 2)
            entry["generated_sql"] = candidate_sql

            t4 = time.time()
            success, result = stage4_unit_tester(ex["db_id"], candidate_sql)
            entry["stage4_latency"] = round(time.time() - t4, 2)
            entry["execution_success"] = success
            entry["execution_result_or_error"] = str(result)[:500]  # truncate large results

            # Compare against gold SQL execution (execution accuracy, not string match)
            gold_success, gold_result = stage4_unit_tester(ex["db_id"], ex["SQL"])
            entry["gold_execution_success"] = gold_success
            entry["execution_match"] = (success and gold_success and result == gold_result)

        except Exception as e:
            entry["pipeline_error"] = str(e)
            entry["execution_success"] = False
            entry["execution_match"] = False

        entry["total_latency_sec"] = round(time.time() - start, 2)
        print(f"Total: {entry['total_latency_sec']}s | Success: {entry.get('execution_success')} | Match: {entry.get('execution_match')}")
        results.append(entry)

        # Save incrementally in case of interruption
        with open("chess_pilot_results_FINAL.json", "w") as f:
            json.dump(results, f, indent=2)

    # Summary
    total = len(results)
    exec_success = sum(1 for r in results if r.get("execution_success"))
    exec_match = sum(1 for r in results if r.get("execution_match"))
    avg_latency = sum(r["total_latency_sec"] for r in results) / total

    summary = {
        "total_examples": total,
        "execution_success_rate": round(exec_success / total, 3),
        "execution_match_accuracy": round(exec_match / total, 3),
        "avg_total_latency_sec": round(avg_latency, 2),
    }
    print("\n=== PILOT SUMMARY ===")
    print(json.dumps(summary, indent=2))

    with open("chess_pilot_summary_FINAL.json", "w") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    run_chess_pilot()
