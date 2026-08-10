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
