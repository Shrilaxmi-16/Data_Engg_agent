"""
SHIELD Orchestrator: Sequential (Non-Graph) Mode
Per Step 10/13's "in-context self-orchestration" spec, now implemented with
SHIELD's real seven agents (superseding Step 13's placeholder 3-role version,
which predated the actual agent implementations).

Control flow is plain, ordinary Python (sequential calls + if/else
conditionals) -- explicitly NOT using an external graph/state-machine
framework (that's LangGraph mode, Step 17 Part 2). This is the comparison
baseline for RQ2: does external graph-based orchestration actually add
value over straightforward sequential control flow, given the same
underlying agents?

Shared "Knowledge" state object (Step 10 spec) is a plain Python dict,
passed and mutated through each stage -- no graph-managed state.
"""
import json
import sys
import time

sys.path.insert(0, "/home/rit/Documents/Shrilaxmi/Data_Engg/agents")

from schema_discovery_agent import discover_schema
from sql_generation_agent import generate_sql
from transformation_agent import transform
from pipeline_generation_agent import generate_dag
from monitoring_agent import monitor
from self_healing_agent import heal
from optimization_agent import optimize


def run_pipeline(schema_name: str, task_description: str, target_table: str,
                  baseline_row_count: int = None) -> dict:
    """
    Main entry point. Sequential, non-graph orchestration through all seven
    agents. Knowledge object accumulates state as a plain dict, mirroring
    the shared state concept from Step 10 without any graph framework.
    """
    knowledge = {
        "schema_name": schema_name,
        "task_description": task_description,
        "target_table": target_table,
    }
    timeline = []

    # --- Stage 1: Schema Discovery ---
    t0 = time.time()
    knowledge["schema"] = discover_schema(schema_name)
    timeline.append({"stage": "schema_discovery", "latency_sec": round(time.time() - t0, 2)})

    # --- Stage 2: SQL Generation ---
    t0 = time.time()
    sql_result = generate_sql(task_description, knowledge["schema"])
    knowledge["sql_result"] = sql_result
    timeline.append({"stage": "sql_generation", "latency_sec": round(time.time() - t0, 2),
                      "status": sql_result["status"]})

    # Plain if/else control flow -- NOT graph-routed conditional edges.
    if sql_result["status"] != "success":
        knowledge["pipeline_status"] = "failed_at_sql_generation"
        return _finalize(knowledge, timeline)

    # --- Stage 3: Transformation ---
    t0 = time.time()
    transform_result = transform(sql_result["sql"], task_description)
    knowledge["transform_result"] = transform_result
    timeline.append({"stage": "transformation", "latency_sec": round(time.time() - t0, 2),
                      "status": transform_result["status"]})

    # --- Stage 4: Pipeline (DAG) Generation ---
    t0 = time.time()
    try:
        dag_code = generate_dag(
            dag_id=f"shield_orchestrated_{target_table}",
            dag_description=f"SHIELD sequential-orchestrated: {task_description}",
            sql_query=sql_result["sql"],
            target_table=target_table,
        )
        dag_path = f"/home/rit/Documents/Shrilaxmi/Data_Engg/infra/airflow/dags/shield_orchestrated_{target_table}.py"
        with open(dag_path, "w") as f:
            f.write(dag_code)
        knowledge["dag_generation"] = {"status": "success", "dag_path": dag_path}
    except Exception as e:
        knowledge["dag_generation"] = {"status": "failure", "error": str(e)}
    timeline.append({"stage": "pipeline_generation", "latency_sec": round(time.time() - t0, 2),
                      "status": knowledge["dag_generation"]["status"]})

    # --- Stage 5: Monitoring ---
    t0 = time.time()
    monitoring_result = monitor(
        execution_result={"status": "success" if sql_result["status"] == "success" else "failure",
                           "row_count": sql_result.get("row_count", 0)},
        baseline_count=baseline_row_count,
        latency_sec=timeline[1]["latency_sec"],  # SQL generation stage latency
    )
    knowledge["monitoring_result"] = monitoring_result
    timeline.append({"stage": "monitoring", "latency_sec": round(time.time() - t0, 2),
                      "state": monitoring_result["state"]})

    # --- Stage 6: Self-Healing (CONDITIONAL -- plain if, not a graph edge) ---
    if monitoring_result["state"] != "healthy":
        t0 = time.time()
        heal_kwargs = {}
        fault_class = monitoring_result["fault_class"]
        if fault_class == "sql_semantic":
            heal_kwargs = {
                "failed_sql": sql_result.get("sql", ""),
                "error_message": str(sql_result.get("log", {}).get("attempts", [{}])[-1].get("error", "unknown")),
                "schema_context": json.dumps(knowledge["schema"].get("tables", {}))[:1000],
            }
        elif fault_class == "data_quality":
            heal_kwargs = {"table": target_table, "column": "unknown", "condition": "1=0"}
        elif fault_class == "infrastructure":
            heal_kwargs = {"dag_id": f"shield_orchestrated_{target_table}"}
        elif fault_class == "schema_drift":
            heal_kwargs = {"dag_id": f"shield_orchestrated_{target_table}",
                            "sql_query": sql_result.get("sql", ""), "target_table": target_table}

        healing_result = heal(monitoring_result, **heal_kwargs) if heal_kwargs else \
            heal(monitoring_result)  # escalate path, no kwargs needed
        knowledge["healing_result"] = healing_result
        timeline.append({"stage": "self_healing", "latency_sec": round(time.time() - t0, 2),
                          "action": healing_result["action_selected"]})
    else:
        knowledge["healing_result"] = None
        timeline.append({"stage": "self_healing", "latency_sec": 0, "action": "none (healthy)"})

    # --- Stage 7: Optimization ---
    t0 = time.time()
    final_sql = sql_result["sql"]
    if knowledge.get("healing_result") and knowledge["healing_result"]["outcome"].get("repaired_sql"):
        final_sql = knowledge["healing_result"]["outcome"]["repaired_sql"]
    opt_result = optimize(final_sql)
    knowledge["optimization_result"] = opt_result
    timeline.append({"stage": "optimization", "latency_sec": round(time.time() - t0, 2),
                      "needs_review": opt_result["needs_review"]})

    knowledge["pipeline_status"] = "completed"
    return _finalize(knowledge, timeline)


def _finalize(knowledge: dict, timeline: dict) -> dict:
    total_latency = sum(s["latency_sec"] for s in timeline)
    return {
        "orchestration_mode": "sequential_non_graph",
        "pipeline_status": knowledge.get("pipeline_status"),
        "timeline": timeline,
        "total_latency_sec": round(total_latency, 2),
        "knowledge": knowledge,
    }


if __name__ == "__main__":
    print("=== Sequential Orchestrator: Full Seven-Agent Run (TPC-H, same task as all baselines) ===\n")

    result = run_pipeline(
        schema_name="tpch",
        task_description=(
            "Aggregate total order value and order count by customer nation, "
            "using the orders and customer tables. Return nation_key, "
            "total_order_value, order_count, ordered by nation_key."
        ),
        target_table="regional_orders_orchestrated",
        baseline_row_count=25,
    )

    print(json.dumps({
        "orchestration_mode": result["orchestration_mode"],
        "pipeline_status": result["pipeline_status"],
        "timeline": result["timeline"],
        "total_latency_sec": result["total_latency_sec"],
    }, indent=2))

    with open("sequential_orchestrator_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"\nFull result (including Knowledge object) saved to sequential_orchestrator_result.json")
