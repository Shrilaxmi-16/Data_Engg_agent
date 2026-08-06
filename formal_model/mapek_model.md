# SHIELD Formal Model: MAPE-K Grounded Self-Healing with Convergence Analysis

## Part 1 — State Space Definition

Let the pipeline state at discrete time step $t$ be $s_t \in S$, where:

$$S = \{\text{healthy}, \text{degraded}, \text{failed}\}$$

Observable mapping to each state:
- **healthy**: No active fault detected; output within tolerance on all monitored metrics (row-count sanity, aggregate-value sanity, execution latency within SLA).
- **degraded**: At least one fault detected (per the Step 8 taxonomy) but the pipeline is still producing output, possibly with excluded/flagged rows or a fallback plan; output remains within a defined tolerance band.
- **failed**: Pipeline halted, or output falls outside tolerance (e.g., aggregation values off by more than a threshold, or the DAG task errors out without a healing agent success).

Each fault class from Step 8 (schema drift, data quality, infrastructure, SQL semantic) can independently push $s_t$ from healthy → degraded → failed depending on severity and whether the healing policy intervenes successfully.

## Part 2 — Fault Process

Faults arrive from four classes $F = \{f_{schema}, f_{quality}, f_{infra}, f_{sql}\}$, each with class-specific arrival rate $\lambda_i$, $i \in F$.

**Working assumption (to be empirically validated, not proven):** each fault class arrives as an independent Poisson process with rate $\lambda_i$, giving an aggregate fault-arrival rate:

$$\lambda = \sum_{i \in F} \lambda_i, \quad \lambda < \lambda_{max}$$

where $\lambda_{max}$ is the maximum aggregate rate under which the healing policy is expected to keep the pipeline recoverable.

**Explicit flag:** the independence assumption across fault classes is a simplification. In practice, faults may be correlated (e.g., an infrastructure fault causing a cascading data-quality fault). This assumption will be listed in the paper's limitations section (Section 11) and is *not* tested by the current experimental design — a direction for future work, not a gap this thesis closes.

$\lambda$ itself will be **measured** from the actual fault-injection harness built in Step 17, not assumed a priori; Step 18/19 must confirm the harness's realized injection rate matches the intended design rate before this model's predictions can be checked against it.

## Part 3 — Healing Action Space and Policy

Action space, matching the "expected recovery action" column from the Step 8 fault taxonomy:

$$A = \{\text{repair-SQL}, \text{regenerate-DAG}, \text{restart-task}, \text{exclude-and-flag}, \text{escalate}\}$$

(Five actions rather than four — added `exclude-and-flag` to match the data-quality subtypes in Step 8, where the correct action is often to exclude bad rows and log them rather than regenerate code.)

Healing policy: $\pi: S \times F \rightarrow A$ — maps the current state and detected fault class to an action, implemented by the Self-Healing Agent (Step 16).

**Critical parameter — not assumed, to be measured:**

$$p_{heal}(i) = P(\text{action succeeds} \mid \text{fault class } i), \quad i \in F$$

$p_{heal}(i)$ per fault class is **unknown until Step 18's experimental campaign**. The convergence claim in Part 4 is conditional on $p_{heal}(i) > p_{min}$ for some threshold $p_{min}$ — this threshold will be checked against measured data post-experiment, not asserted in advance. If measured $p_{heal}$ values fall below what the convergence claim requires, this is a valid and reportable negative/qualified result, not a failure to hide.

## Part 4 — The Convergence Claim (Lyapunov-Style)

Define the potential function over pipeline state:

$$V(s_t) = \mathbb{E}[\text{time-to-recovery} \mid s_t] + \beta \cdot \mathbb{E}[\text{residual error} \mid s_t]$$

where $\beta > 0$ weights the tradeoff between recovery speed and residual output error, and residual error is measured relative to the ground-truth expected output (from Step 8's "expected recovery action" column, which implies an expected corrected-output shape per fault subtype).

**Claim:** Under bounded aggregate fault-arrival rate ($\lambda < \lambda_{max}$) and healing success probability above threshold ($p_{heal}(i) > p_{min}$ for all $i \in F$), $V(s_t)$ behaves as a supermartingale:

$$\mathbb{E}[V(s_{t+1}) \mid s_t] \leq V(s_t) - \epsilon, \quad \epsilon > 0 \text{ when } s_t \neq \text{healthy}$$

implying bounded expected time for the pipeline to return to the healthy state after a fault.

**Honest three-way classification of this claim (mandatory — do not blur):**

1. **Analytically provable, planned:** The supermartingale drift inequality's *general form* follows the proof structure in Ben Hafaiedh et al.'s distributed formal self-healing model and the discrete-control formulations surveyed in Rutten et al. and Arcaini et al. (all cited in Related Work Section 3.2). We will **adapt this proof structure** to our specific $S$, $A$, $F$ definitions above, rather than claim an original proof from scratch. This is the honest, defensible framing.

2. **Empirically validated only, not proven:** Whether $V(s_t)$ actually trends downward in practice, given the *measured* $p_{heal}(i)$ and $\lambda$ from real experimental runs (Step 18), is a **simulation-based validation of the drift condition** — we will plot $V(s_t)$ trajectories across fault-injection runs and check empirically whether the supermartingale property holds under realistic conditions. This is not a formal proof of the general case; it is evidence for the specific system as built and tested.

3. **Assumed, not tested by this thesis:**
   - Independence of fault arrivals across classes (Part 2).
   - Stationarity of $\lambda_i$ over the experimental period (faults don't systematically increase/decrease in rate mid-experiment).
   - That the healing policy $\pi$ is deterministic given $(s_t, f)$ — stochastic/adaptive policies are out of scope.

These three assumed-not-tested items will be explicitly restated in the paper's Limitations section (Section 11), matching the honest-limitations principle established in the project's earlier proposal review.

## Part 5 — Empirical Quantities Step 18/19 Must Measure

This model is not fully instantiated until the following are measured from real experimental data:

1. **$p_{heal}(i)$ for each of the four fault classes** — computed from healing success/failure logs (successes ÷ total injected faults, per class), broken down further by subtype if sample size allows (per Step 8's 15 subtypes).
2. **Realized $\lambda_i$ per fault class and aggregate $\lambda$** — measured from the fault-injection harness's actual logged injection timestamps (Step 17), confirmed to match intended design rates within acceptable tolerance.
3. **$V(s_t)$ trajectory across runs** — computed post-hoc from time-to-recovery and residual-error logs, for the empirical drift-condition check in Part 4.2.
4. **MTTD and MTTR distributions per fault class** — feeds directly into the $\mathbb{E}[\text{time-to-recovery}]$ term of $V(s_t)$.
5. **Confirmation (or refutation) of $p_{heal}(i) > p_{min}$** — this is the specific, falsifiable condition under which the convergence claim is stated to hold; Step 19's statistical analysis must report this explicitly, in both directions (confirmed or not, per fault class).

This checklist is the direct input to **Step 11 (Evaluation Protocol)**, which must specify exactly how each of these five quantities will be computed, and to **Step 19 (Statistical Analysis)**, which performs the actual computation once experimental data exists.
