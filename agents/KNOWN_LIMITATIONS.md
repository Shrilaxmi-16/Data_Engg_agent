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
