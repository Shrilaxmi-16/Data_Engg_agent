"""
SHIELD Agent 5: Monitoring Agent
Per Step 10 spec: tracks runtime metrics, classifies pipeline state as
healthy/degraded/failed (per Step 9's formal model), assigns a fault-class
label when degraded/failed.

Per Step 14/15 findings (composite-FK silent errors, semantically-blind
null-handling, DDL/INSERT mismatches): execution success is NOT sufficient
evidence of a healthy state. This agent implements result-plausibility
checks (row-count deltas, aggregate-value sanity bounds) as first-class
detection signals, not just execution-status checks.
"""
import json
import time
import psycopg2
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "shield_db",
    "user": "shield",
    "password": "shield_dev_pw",
}

# Thresholds -- deterministic, matching Step 11's evaluation protocol
ROW_COUNT_DELTA_WARN_PCT = 20    # matches Pipeline Generation Agent's gate (Step 15)
ROW_COUNT_DELTA_FAIL_PCT = 50    # beyond this, treat as failed, not just degraded
EXECUTION_LATENCY_WARN_SEC = 60  # informed by Baseline B's 87.71s single-call latency
EXECUTION_LATENCY_FAIL_SEC = 180


def call_llm(prompt: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["response"]


def check_execution_status(execution_result: dict) -> dict:
    """
    Deterministic: did the upstream step report success/failure.
    This is the WEAKEST signal -- per Step 14's composite-FK finding, a
    query can execute successfully while being semantically wrong.
    """
    status = execution_result.get("status", "unknown")
    return {
        "check": "execution_status",
        "passed": status == "success",
        "detail": f"reported status: {status}",
    }


def check_row_count_plausibility(baseline_count: int, observed_count: int) -> dict:
    """
    Deterministic: row-count delta against a known baseline/expectation.
    Directly operationalizes the Pipeline Generation Agent's sanity gate
    (Step 15) as a standalone, reusable Monitoring check.
    """
    if baseline_count == 0:
        delta_pct = 0 if observed_count == 0 else 100
    else:
        delta_pct = abs(baseline_count - observed_count) / baseline_count * 100

    if delta_pct > ROW_COUNT_DELTA_FAIL_PCT:
        severity = "failed"
    elif delta_pct > ROW_COUNT_DELTA_WARN_PCT:
        severity = "degraded"
    else:
        severity = "healthy"

    return {
        "check": "row_count_plausibility",
        "passed": severity == "healthy",
        "severity": severity,
        "delta_pct": round(delta_pct, 2),
        "detail": f"baseline={baseline_count}, observed={observed_count}, delta={delta_pct:.1f}%",
    }


def check_execution_latency(latency_sec: float) -> dict:
    """Deterministic: flag abnormally slow execution as a potential fault signal."""
    if latency_sec > EXECUTION_LATENCY_FAIL_SEC:
        severity = "failed"
    elif latency_sec > EXECUTION_LATENCY_WARN_SEC:
        severity = "degraded"
    else:
        severity = "healthy"

    return {
        "check": "execution_latency",
        "passed": severity == "healthy",
        "severity": severity,
        "latency_sec": latency_sec,
        "detail": f"{latency_sec:.1f}s (warn>{EXECUTION_LATENCY_WARN_SEC}s, fail>{EXECUTION_LATENCY_FAIL_SEC}s)",
    }


def check_null_rate_plausibility(column_null_pcts: dict, high_null_threshold: float = 50.0) -> dict:
    """
    Deterministic: flag columns with high null rates for review, WITHOUT
    assuming they should be dropped. Directly addresses the Step 15
    semantically-blind null-handling finding -- high null% is a signal to
    investigate meaning, not an automatic defect.
    """
    flagged = {col: pct for col, pct in column_null_pcts.items() if pct > high_null_threshold}
    return {
        "check": "null_rate_plausibility",
        "passed": len(flagged) == 0,
        "severity": "degraded" if flagged else "healthy",
        "flagged_columns": flagged,
        "detail": (
            f"{len(flagged)} column(s) exceed {high_null_threshold}% null rate -- "
            f"flagged for review, NOT auto-dropped (Step 15 finding)"
            if flagged else "no columns exceed null threshold"
        ),
    }


def classify_state(checks: list) -> str:
    """
    Aggregate individual check results into the Step 9 formal model's
    state space: healthy, degraded, failed.
    """
    severities = [c.get("severity", "healthy" if c["passed"] else "degraded") for c in checks]
    if "failed" in severities:
        return "failed"
    if "degraded" in severities:
        return "degraded"
    return "healthy"


def identify_fault_class(checks: list) -> str:
    """
    Deterministic mapping from failed/degraded checks to the Step 8 fault
    taxonomy's four classes, for downstream Self-Healing Agent routing.
    """
    failing = [c for c in checks if not c["passed"]]
    if not failing:
        return "none"

    check_names = [c["check"] for c in failing]
    if "row_count_plausibility" in check_names or "null_rate_plausibility" in check_names:
        return "data_quality"
    if "execution_latency" in check_names:
        return "infrastructure"
    if "execution_status" in check_names:
        return "sql_semantic"
    return "unknown"


def monitor(execution_result: dict, baseline_count: int = None, latency_sec: float = None,
            column_null_pcts: dict = None) -> dict:
    """
    Main entry point. Aggregates all available checks into a state
    classification, per Step 9's formal model.
    """
    checks = [check_execution_status(execution_result)]

    if baseline_count is not None:
        observed_count = execution_result.get("row_count", 0) or 0
        checks.append(check_row_count_plausibility(baseline_count, observed_count))

    if latency_sec is not None:
        checks.append(check_execution_latency(latency_sec))

    if column_null_pcts:
        checks.append(check_null_rate_plausibility(column_null_pcts))

    state = classify_state(checks)
    fault_class = identify_fault_class(checks) if state != "healthy" else "none"

    result = {
        "state": state,
        "fault_class": fault_class,
        "checks": checks,
        "timestamp": time.time(),
    }

    if state != "healthy":
        summary_prompt = f"""A data pipeline monitoring check found this issue:
State: {state}
Fault class: {fault_class}
Failed checks: {json.dumps([c for c in checks if not c['passed']], indent=2)}

Write a brief (2-3 sentence) human-readable diagnostic summary for a data engineer."""
        result["llm_diagnostic_summary"] = call_llm(summary_prompt)

    return result


if __name__ == "__main__":
    print("=== Test 1: Healthy case (TPC-H regional orders, matches all baselines) ===")
    healthy_result = monitor(
        execution_result={"status": "success", "row_count": 25},
        baseline_count=25,
        latency_sec=2.0,
    )
    print(json.dumps(healthy_result, indent=2))

    print("\n=== Test 2: Degraded case (simulating Step 14's composite-FK fan-out bug) ===")
    degraded_result = monitor(
        execution_result={"status": "success", "row_count": 506},
        baseline_count=352,  # hypothetical correct count for the sales.specialofferproduct case
        latency_sec=15.0,
    )
    print(json.dumps(degraded_result, indent=2))

    print("\n=== Test 3: Failed case (simulating Step 15's semantic null-drop bug) ===")
    failed_result = monitor(
        execution_result={"status": "success", "row_count": 1336},
        baseline_count=19820,  # the correct AdventureWorks customer count
        latency_sec=5.0,
        column_null_pcts={"storeid": 93.26, "personid": 3.54},
    )
    print(json.dumps(failed_result, indent=2))

    with open("monitoring_test_results.json", "w") as f:
        json.dump({"test1_healthy": healthy_result, "test2_degraded": degraded_result, "test3_failed": failed_result}, f, indent=2)
