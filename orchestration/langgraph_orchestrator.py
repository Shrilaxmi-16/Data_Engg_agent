"""
SHIELD Orchestrator: LangGraph Mode
Structurally mirrors sequential_orchestrator.py's seven-agent pipeline, but
uses LangGraph's StateGraph for explicit node/edge routing instead of plain
Python if/else control flow. This is the RQ2 comparison target: does
external graph-based orchestration outperform sequential in-context
control flow, given the SAME underlying agents and task.

Per the Related Work's counter-evidence (Dennis et al., 2026), this is not
assumed to win -- the comparison itself is the point.
"""
import json
import sys
import time
from typing import TypedDict, Optional

sys.path.insert(0, "/home/rit/Documents/Shrilaxmi/Data_Engg/agents")

from schema_discovery_agent import discover_schema
from sql_generation_agent import generate_sql
from transformation_agent import transform
from pipeline_generation_agent import generate_dag
from monitoring_agent import monitor
from self_healing_agent import heal
from optimization_agent import optimize

from langgraph.graph import StateGraph, END


class ShieldState(TypedDict):
    schema_name: str
    task_description: str
    target_table: str
    baseline_row_count: Optional[int]
    schema: Optional[dict]
    sql_result: Optional[dict]
    transform_result: Optional[dict]
    dag_generation: Optional[dict]
    monitoring_result: Optional[dict]
    healing_result: Optional[dict]
    optimization_result: Optional[dict]
    pipeline_status: Optional[str]
    timeline: list


def _timed(stage_name):
    """Decorator-like helper to record per-node latency into state['timeline']."""
    def wrapper(fn):
        def inner(state: ShieldState) -> ShieldState:
            t0 = time.time()
            result_state = fn(state)
            latency = round(time.time() - t0, 2)
            result_state["timeline"].append({"stage": stage_name, "latency_sec": latency})
            return result_state
        return inner
    return wrapper


@_timed("schema_discovery")
def node_schema_discovery(state: ShieldState) -> ShieldState:
    state["schema"] = discover_schema(state["schema_name"])
    return state


@_timed("sql_generation")
def node_sql_generation(state: ShieldState) -> ShieldState:
    state["sql_result"] = generate_sql(state["task_description"], state["schema"])
    return state


@_timed("transformation")
def node_transformation(state: ShieldState) -> ShieldState:
    state["transform_result"] = transform(state["sql_result"]["sql"], state["task_description"])
    return state


@_timed("pipeline_generation")
def node_pipeline_generation(state: ShieldState) -> ShieldState:
    try:
        dag_code = generate_dag(
            dag_id=f"shield_langgraph_{state['target_table']}",
            dag_description=f"SHIELD LangGraph-orchestrated: {state['task_description']}",
            sql_query=state["sql_result"]["sql"],
            target_table=state["target_table"],
        )
        dag_path = f"/home/rit/Documents/Shrilaxmi/Data_Engg/infra/airflow/dags/shield_langgraph_{state['target_table']}.py"
        with open(dag_path, "w") as f:
            f.write(dag_code)
        state["dag_generation"] = {"status": "success", "dag_path": dag_path}
    except Exception as e:
        state["dag_generation"] = {"status": "failure", "error": str(e)}
    return state


@_timed("monitoring")
def node_monitoring(state: ShieldState) -> ShieldState:
    sql_latency = next((s["latency_sec"] for s in state["timeline"] if s["stage"] == "sql_generation"), 0)
    state["monitoring_result"] = monitor(
        execution_result={
            "status": "success" if state["sql_result"]["status"] == "success" else "failure",
            "row_count": state["sql_result"].get("row_count", 0),
        },
        baseline_count=state["baseline_row_count"],
        latency_sec=sql_latency,
    )
    return state


@_timed("self_healing")
def node_self_healing(state: ShieldState) -> ShieldState:
    monitoring_result = state["monitoring_result"]
    fault_class = monitoring_result["fault_class"]
    heal_kwargs = {}

    if fault_class == "sql_semantic":
        heal_kwargs = {
            "failed_sql": state["sql_result"].get("sql", ""),
            "error_message": str(state["sql_result"].get("log", {}).get("attempts", [{}])[-1].get("error", "unknown")),
            "schema_context": json.dumps(state["schema"].get("tables", {}))[:1000],
        }
    elif fault_class == "data_quality":
        heal_kwargs = {"table": state["target_table"], "column": "unknown", "condition": "1=0"}
    elif fault_class == "infrastructure":
        heal_kwargs = {"dag_id": f"shield_langgraph_{state['target_table']}"}
    elif fault_class == "schema_drift":
        heal_kwargs = {"dag_id": f"shield_langgraph_{state['target_table']}",
                        "sql_query": state["sql_result"].get("sql", ""), "target_table": state["target_table"]}

    state["healing_result"] = heal(monitoring_result, **heal_kwargs) if heal_kwargs else heal(monitoring_result)
    return state


@_timed("optimization")
def node_optimization(state: ShieldState) -> ShieldState:
    final_sql = state["sql_result"]["sql"]
    if state.get("healing_result") and state["healing_result"]["outcome"].get("repaired_sql"):
        final_sql = state["healing_result"]["outcome"]["repaired_sql"]
    state["optimization_result"] = optimize(final_sql)
    state["pipeline_status"] = "completed"
    return state


def node_skip_healing(state: ShieldState) -> ShieldState:
    """No-op node for the healthy path -- graph edges require a node on both branches."""
    state["healing_result"] = None
    state["timeline"].append({"stage": "self_healing", "latency_sec": 0})
    return state


def route_after_monitoring(state: ShieldState) -> str:
    """
    THIS is the explicit graph-conditional-edge routing that structurally
    differs from the sequential orchestrator's plain if/else -- LangGraph
    manages this branch decision as part of the graph definition itself.
    """
    if state["monitoring_result"]["state"] != "healthy":
        return "self_healing"
    return "skip_healing"


def build_graph():
    graph = StateGraph(ShieldState)

    graph.add_node("schema_discovery", node_schema_discovery)
    graph.add_node("sql_generation", node_sql_generation)
    graph.add_node("transformation", node_transformation)
    graph.add_node("pipeline_generation", node_pipeline_generation)
    graph.add_node("monitoring", node_monitoring)
    graph.add_node("self_healing", node_self_healing)
    graph.add_node("skip_healing", node_skip_healing)
    graph.add_node("optimization", node_optimization)

    graph.set_entry_point("schema_discovery")
    graph.add_edge("schema_discovery", "sql_generation")
    graph.add_edge("sql_generation", "transformation")
    graph.add_edge("transformation", "pipeline_generation")
    graph.add_edge("pipeline_generation", "monitoring")

    graph.add_conditional_edges(
        "monitoring",
        route_after_monitoring,
        {"self_healing": "self_healing", "skip_healing": "skip_healing"},
    )

    graph.add_edge("self_healing", "optimization")
    graph.add_edge("skip_healing", "optimization")
    graph.add_edge("optimization", END)

    return graph.compile()


def run_pipeline(schema_name: str, task_description: str, target_table: str,
                  baseline_row_count: int = None) -> dict:
    app = build_graph()
    initial_state: ShieldState = {
        "schema_name": schema_name,
        "task_description": task_description,
        "target_table": target_table,
        "baseline_row_count": baseline_row_count,
        "schema": None, "sql_result": None, "transform_result": None,
        "dag_generation": None, "monitoring_result": None,
        "healing_result": None, "optimization_result": None,
        "pipeline_status": None, "timeline": [],
    }
    final_state = app.invoke(initial_state)
    total_latency = sum(s["latency_sec"] for s in final_state["timeline"])
    return {
        "orchestration_mode": "langgraph",
        "pipeline_status": final_state["pipeline_status"],
        "timeline": final_state["timeline"],
        "total_latency_sec": round(total_latency, 2),
        "state": final_state,
    }


if __name__ == "__main__":
    print("=== LangGraph Orchestrator: Full Seven-Agent Run (same TPC-H task) ===\n")

    result = run_pipeline(
        schema_name="tpch",
        task_description=(
            "Aggregate total order value and order count by customer nation, "
            "using the orders and customer tables. Return nation_key, "
            "total_order_value, order_count, ordered by nation_key."
        ),
        target_table="regional_orders_langgraph",
        baseline_row_count=25,
    )

    print(json.dumps({
        "orchestration_mode": result["orchestration_mode"],
        "pipeline_status": result["pipeline_status"],
        "timeline": result["timeline"],
        "total_latency_sec": result["total_latency_sec"],
    }, indent=2))

    with open("langgraph_orchestrator_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
