# SHIELD SQL Generation Agent — Known Limitations

## Composite Foreign Key Join Handling (Discovered: Step 14)

**Issue:** The SQL Generation Agent unreliably constructs join conditions for
composite (multi-column) foreign keys, consistently omitting one of the
required columns even when the schema-linking prompt explicitly and forcefully
states both columns are required together.

**Reproduction case:** `sales.salesorderdetail` -> `sales.specialofferproduct`,
a composite FK on `(specialofferid, productid)`.

**Observed behavior across models:**
| Model | Behavior |
|---|---|
| qwen2.5:14b | Joins on only ONE column (specialofferid OR productid, inconsistent across runs). Query executes successfully but produces a fan-out join -- silently inflated/incorrect row counts (484 vs 506 rows across two runs, neither verified correct). |
| qwen2.5:7b | Same single-column join pattern. Additionally hallucinated a column (`sop.discountpct`) that does not exist on that table. Failed after 3 self-refinement attempts. |

**Attempted fix:** Restructured schema-linking prompt to explicitly group
composite FK columns together with directive language ("all N conditions
REQUIRED together, do not omit any"). Did NOT resolve the issue -- the model
still dropped one column, just a different one each time.

**Implication for SHIELD's design:**
- This is a genuine, reproducible model-capability limitation, not a
  schema-representation or prompt-clarity bug on our end (schema correctness
  was independently verified via `pg_constraint`-based introspection).
- Confirms and extends the schema-representation sensitivity finding reported
  by Nascimento et al. (cited in Related Work 3.3) to composite-key cases
  specifically.
- Critically, this failure is SILENT when the model is capable enough to
  produce syntactically valid, executing SQL (14b case) -- execution success
  alone is an insufficient correctness signal. This directly motivates the
  Monitoring Agent's need for result-sanity checks (row-count/aggregate
  plausibility bounds), not just execution-status checks, per the Step 9
  formal model's "degraded" state definition.

**Status:** Documented, not yet fixed. Candidate future fixes (not attempted
in Step 14, to avoid scope creep): few-shot examples showing correct composite
joins in the prompt; a dedicated post-generation validator that checks
multi-column FK usage against the schema and flags/rejects incomplete joins
before execution; testing with a stronger cloud model (RQ3 relevant).

**Relevance to RQ3 (cost/accuracy routing):** this failure mode did NOT
improve with model size (14b vs 7b both failed, in different ways) --
suggesting composite-key handling may not simply be a matter of buying a
bigger model, which is itself a useful negative result for the cost-routing
analysis.

## Transformation Agent: Semantically-Blind Null Handling (Discovered: Step 15)

**Issue:** The Transformation Agent's LLM-recommended null-handling rules
(DROP_NULLS specifically) do not account for whether a high null rate is
data quality noise vs. meaningful business semantics.

**Reproduction case:** `sales.customer.storeid` is null for 93.26% of rows
in AdventureWorks -- this is NOT dirty data; it correctly distinguishes
individual retail customers (null storeid) from wholesale/reseller customers
(non-null storeid). The agent recommended and applied DROP_NULLS on this
column, collapsing the result set from 19,820 to 1,336 rows -- silently
deleting the vast majority of legitimate customer records.

**Implication:** Reinforces the Step 14 composite-FK finding: execution
success/rule application succeeding is not sufficient evidence of a correct
outcome. Both findings motivate the same design requirement for the
Monitoring Agent (Step 15): transformations or generations that alter
row counts beyond a plausibility threshold (e.g., >X% reduction) must be
flagged for validation/escalation rather than applied silently.

**Status:** Documented. Candidate fix (not yet implemented, avoiding scope
creep): require the Transformation Agent to report null-percentage alongside
its recommendation and add an explicit prompt instruction to treat >20%
null rate as a signal to investigate semantic meaning first, defaulting to
NONE unless a business-context justification is provided.

## Pipeline Generation Agent: DDL/INSERT Bugs (Discovered and Fixed: Step 15)

**Issue 1:** LLM-generated `CREATE TABLE` statements lacked `IF NOT EXISTS`,
causing `DuplicateTable` errors on concurrent or repeated DAG runs.
**Fix:** Deterministic regex enforcement in the template layer -- the agent
now rewrites any `CREATE TABLE` to `CREATE TABLE IF NOT EXISTS` regardless
of what the LLM produced, rather than relying on prompt instruction alone.

**Issue 2:** LLM-generated `INSERT` statement included a placeholder for
`run_timestamp` (4 `%s`) despite the source query only selecting 3 columns,
causing a runtime `IndexError` when the DAG executed.
**Fix:** Two-layer defense -- (a) explicit prompt instruction not to include
`run_timestamp` in the INSERT column list, and (b) a deterministic runtime
validation gate in the generated DAG comparing `INSERT_SQL_TEMPLATE`'s
placeholder count against the actual extracted row's column count, failing
with a clear `SCHEMA MISMATCH` diagnostic rather than an opaque `IndexError`
if the LLM still gets it wrong.

**Pattern consistent with Step 14/15 findings:** LLM-generated artifacts
(SQL joins, transformation rules, now DDL/INSERT pairing) require deterministic
validation gates rather than trusting prompt compliance alone, even when the
prompt is explicit and directive. This is now a recurring, well-evidenced
design principle across three independent agents.

## Fault Injection Harness: Incomplete Type Capture on Restore (Discovered: Step 16)

**Issue:** `inject_type_change`'s original implementation captured only the
type family via `information_schema.columns.data_type` (e.g., "character"),
losing length/precision modifiers (e.g., the "(10)" in CHAR(10)). Restoring
using this incomplete type silently shrank `tpch.customer.c_mktsegment` from
CHAR(10) to the Postgres default CHAR(1), causing a downstream COPY failure
("value too long for type character(1)") when reloading TPC-H data.

**Fix:** Use `pg_attribute`/`format_type()` to capture the complete type
specification including length/precision, not `information_schema`'s
truncated `data_type` field.

**Meta-note:** This is itself a small, ironic instance of the same
"execution succeeded, silently wrong" pattern found in Step 14 (composite-FK)
and Step 15 (null-handling) -- the restore operation completed without error
but left the schema in a subtly incorrect state, only surfaced later by an
unrelated operation (data reload) failing. Reinforces the paper's broader
finding that success/failure status alone is insufficient validation
throughout this system, including in its own supporting tooling.

## Deferred: LLM Call Retry-Hardening (Noted: Step 17)

**Status:** NOT YET APPLIED. During Step 17's orchestrator testing, Ollama
went down mid-session (systemd service stopped), causing two failed
end-to-end runs with unhandled ConnectionError/RemoteDisconnected
exceptions. A retry wrapper was drafted for Pipeline Generation Agent's
call_llm() but not yet rolled out to all seven agents.

**Action required before Step 18:** apply consistent retry logic (with
bounded total timeout, not indefinite retry) to all seven agents'
call_llm() functions, since Step 18's experimental campaign involves many
hours of sequential LLM calls where transient Ollama unavailability is a
realistic, recurring risk -- not a one-off fluke.
