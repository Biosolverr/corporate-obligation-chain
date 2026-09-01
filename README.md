# Corporate Obligation Chain / Administratum — GenLayer Intelligent Contracts

**Category:** GenLayer Intelligent Contracts.
**Status:** all three contracts implemented, documented, AND executed for
real against `genlayer-test==0.29.2` / GenVM v0.2.16 (Direct Mode) --
**56/56 tests passing** (23 Gate, 27 Router, 6 CertificationGate) -- plus
one real bug already found and fixed via an actual live Studio deployment
attempt: Address-typed arguments were arriving as plain `int` instead of
GenVM's `Address` type (see `docs/architecture.md` §23.8). This is not a
syntax check. See §23 ("Verification log") for everything found and
fixed, and -- just as important -- what Direct Mode structurally cannot
verify (cross-contract calls between any of the three, per GenVM's own
one-contract-per-process constraint). `deploy/STUDIO_TESTING_GUIDE.md` is
the manual sequence to close that gap -- try the deploy again with these
fixed contracts and report back what happens next.

## The primitive: Semantic Obligation Gate

```
obligation + policy + untrusted evidence
    -> semantic adjudication (leader, independently)
    -> GenLayer consensus (validators re-derive, compare structured fields)
    -> finalized verdict (APPROVED / REJECTED / UNDETERMINED)
    -> deterministic state transition (never inside the nondet block)
```

One obligation, one policy, one verdict — unchanged since Milestone 1.

## The orchestration layer: ProcessGraphRouter, and the terminal step: CertificationGate

`SemanticObligationGate` instances can be composed into a **dependency
graph**, not just a line. This one router serves two reference use cases
built on the exact same, unmodified Gate contract:

- **Corporate Obligation Chain** — linear procurement (PO -> Delivery ->
  Acceptance -> Invoice): a degenerate DAG, one edge per consecutive pair.
- **Administratum** — "consensus against bureaucracy": independent
  regulatory checks (fire safety, sanitary, cadastre, ...) that don't
  actually depend on each other get adjudicated in **parallel** instead of
  a forced sequential human queue, converging on a final-review stage that
  depends on all of them.

The Router is entirely deterministic — no LLM, no web fetch, no
`gl.vm.run_nondet_unsafe` anywhere in it. It only ever reads Gate state
(`.view()`, never `.emit()` — see `docs/architecture.md` §13) and computes
graph readiness / pass-fail. It cannot write a single decision field on any
Gate obligation. Two admin exit paths (`renounce_admin()`,
`freeze_upgrades()`) let the deploying admin permanently give up control of
the authority registry and/or contract upgrades once a domain's setup is
considered stable — see `docs/architecture.md` §17.

`CertificationGate` is the deterministic terminal step: once
`ProcessGraphRouter` reports a process `COMPLETE`, anyone can call
`claim_eligibility` to permanently record a generic `ELIGIBLE` flag for it.
It never moves money or issues anything itself — see `docs/architecture.md`
Part C.

## Why this needs GenLayer and not a backend + GPT

Delete GenLayer and you keep: roles, storage, hashes, timestamps,
deterministic transitions. You lose: an adjudicator that isn't a single
operator or a single model instance. The Router adds a second, distinct
necessity on top of the Gate's: safely **parallelizing** several independent
semantic adjudications requires something that computes "what's unblocked"
and "did the whole graph fail" without being able to quietly falsify any
single stage's result — and since the Router only ever reads
consensus-backed Gate state, no party (including the Router's own admin)
can fabricate a stage outcome. See `docs/architecture.md` §2 and §11 for
the full argument.

## What's in this repo right now

```
contracts/semantic_obligation_gate.py   the adjudication primitive — 14/14 tests pass
contracts/process_graph_router.py       the DAG orchestrator — 19/19 tests pass
contracts/certification_gate.py         the deterministic terminal step — 5/5 tests pass
tests/test_semantic_gate.py             Direct Mode tests, executed
tests/test_process_graph_router.py      Direct Mode tests, executed (Router-isolated scope)
tests/test_certification_gate.py        Direct Mode tests, executed (isolated scope)
deploy/STUDIO_TESTING_GUIDE.md          manual live cross-contract test sequence
docs/architecture.md                    full design doc, threat model, limitations, verification log
```

All three contracts now exist. What's left is not writing more code — it's
the live cross-contract verification that Direct Mode cannot provide by
construction (GenVM allows only one contract per process in that mode; see
`docs/architecture.md` §23.4). See §10 for the exact step order followed.

## Contract APIs

**SemanticObligationGate**
```python
create_obligation(obligation_id: str, supplier: Address, policy: str, deadline_iso: str) -> None
submit_evidence(obligation_id: str, evidence_refs: list[str], evidence_hash: str) -> None
adjudicate(obligation_id: str) -> None

get_obligation(obligation_id: str) -> dict
get_status(obligation_id: str) -> str
get_verdict(obligation_id: str) -> dict
get_evidence_refs(obligation_id: str) -> list[str]
```

**ProcessGraphRouter**
```python
register_authority(stage_type: str, authority_address: Address) -> None   # admin only
renounce_admin() -> None                                                   # admin only, irreversible
freeze_upgrades() -> None                                                  # admin only, irreversible
register_process(process_id: str, graph_json: str) -> None                 # see DAG format below

get_unblocked_stages(process_id: str) -> list[str]
get_stalled_stages(process_id: str) -> list[str]                           # UNDETERMINED stages
get_process_status(process_id: str) -> str                                 # ACTIVE / FAILED / COMPLETE
refresh_process_status(process_id: str) -> None                             # permissionless
get_process(process_id: str) -> dict
```

**CertificationGate**
```python
claim_eligibility(process_id: str) -> None   # permissionless

is_eligible(process_id: str) -> bool
get_eligibility(process_id: str) -> dict
```

No `ai_decide(...)` / `approve_anything(...)` style generic method exists on
any of the three contracts — every entry point reflects a specific step of
the obligation, graph, or eligibility lifecycle.

### DAG payload format (`graph_json` argument to `register_process`)

```json
{
  "stages": [
    {"stage_id": "fire_safety", "stage_type": "fire_safety", "obligation_id": "permit-42:fire_safety", "mandatory": true},
    {"stage_id": "sanitary",    "stage_type": "sanitary",    "obligation_id": "permit-42:sanitary",    "mandatory": true},
    {"stage_id": "final_review","stage_type": "final_review","obligation_id": "permit-42:final_review","mandatory": true}
  ],
  "edges": [
    {"stage_id": "final_review", "depends_on": "fire_safety"},
    {"stage_id": "final_review", "depends_on": "sanitary"}
  ]
}
```

Every `obligation_id` referenced must already exist on the configured Gate
deployment, created by the address registered (via `register_authority`) as
the authority for that stage's `stage_type` — see `docs/architecture.md`
§14 for why this check exists and what it prevents.

## Role reversal — read before reusing for a new domain

In procurement, the **buyer** wants proof from the **supplier**. In a
regulatory graph, this flips: the **regulator is the `buyer`** (writes
policy), the **applicant is the `supplier`** (submits evidence of
compliance). Getting this backwards lets an applicant write their own
compliance policy. See `docs/architecture.md` §12.

## Output schema (what consensus actually agrees on, per obligation)

```json
{
  "decision": "APPROVED | REJECTED | UNDETERMINED",
  "quantity_match": true,
  "specification_match": true,
  "deadline_match": true,
  "critical_exception": false,
  "reason_code": "ALL_REQUIRED_CRITERIA_MET"
}
```

Validators never compare raw LLM prose — only these six fields.

## Claim limitation

Neither contract claims to prove a real-world obligation was physically
fulfilled. The Gate establishes that GenLayer consensus was reached on:
"the submitted evidence, adjudicated against the specified policy, produces
verdict X." The Router establishes only "these already-consensus-backed
verdicts satisfy this dependency graph's completion condition." Neither is
a court; neither performs independent physical-world verification.

## Adversarial self-review (found and fixed before any reviewer had to)

After live Studio testing worked end-to-end, a deliberate second pass
asked "what would a hostile reviewer find next" across all three
contracts — see `docs/architecture.md` Part D (§28). The headline finding:
**evidence URLs are mutable, and nothing previously required a leader and
a validator to have fetched byte-identical content** — only that they
reached the same high-level decision. Fixed by adding a
`_evidence_content_hash` (computed by contract code from what was
actually fetched, never by the LLM) to the consensus-compared fields, and
persisting the agreed hash on-chain as `resolved_evidence_hash`. Also
closed: no cap on `policy` length, no validation that `deadline_iso` is a
real date. Also confirmed (no code change needed): `gate_address` and
`router_address` cannot be swapped post-deployment by anyone, including
admin. Also documented, deliberately not fixed yet: mutable evidence URLs
remain the wrong evidence source for production (use content-addressed
evidence instead), no rate limit on `adjudicate()` retries, evidence
truncation can produce false negatives on long documents, and date/quantity
comparison is 100% LLM-judged rather than partially deterministic — all
five are named, load-bearing trade-offs now, not silent gaps. An
independent external audit (see `docs/architecture.md` Part E) then found
and this project fixed three more real issues (fail-closed on retrieval
failure, rejecting internally-inconsistent verdicts, obligation
replay/reuse in the Router) -- and, just as important, correctly declined
to ship one plausible-sounding but actually-harmful "fix" (auto-comparing
submitted evidence hashes against an internal, unreproducible format,
which would have broken every normal submission). 56/56 tests pass.

## Before you trust this

1. ~~`pip install genlayer-test && pytest tests/ -v`~~ **done this
   session** -- 38/38 pass. Re-run yourself to confirm:
   `pip install genlayer-test && pytest tests/ -v` (pins `sdk_version="v0.2.16"`
   internally -- see §23.1 for why that pin currently matters).
2. ~~Confirm `hashlib.sha256` / `json.loads` run inside GenVM~~ **confirmed**
   by the test run above (§9.1, §22.3) -- still worth one Studio smoke test
   since Direct Mode's Python runtime and the on-chain WASM one could in
   principle diverge, though nothing found this session suggests they would.
3. ~~Confirm `gl.storage.Root.get().upgraders.get().append(...)`
   behaves as documented~~ **confirmed** -- every Router deployment in the
   test run executes this in `__init__` without error, and
   `renounce_admin()`/`freeze_upgrades()` (the two admin-exit paths) are
   both confirmed working and confirmed independent of each other (§22.5,
   §23 limitation #6).
4. **Still open:** run at least one adversarial-evidence adjudication
   against a real model in Studio before relying on the prompt-injection
   claims in `docs/architecture.md` §5/§9.3 -- Direct Mode mocks the LLM,
   so this specific claim genuinely needs a live model.
5. **Still open, and structural, not just untried:** every cross-contract
   code path in this repo -- the Router's spoofed-obligation check,
   `get_unblocked_stages`, `refresh_process_status`, and
   CertificationGate's `claim_eligibility` -- cannot be exercised in Direct
   Mode at all. GenVM enforces one contract per process, so two contracts
   can never both be live in the same Direct Mode session. Needs either a
   glsim harness or GenLayer's Integration Testing mode against a running
   localnet -- `deploy/STUDIO_TESTING_GUIDE.md` is the manual sequence for
   exactly this. See `docs/architecture.md` §23.4.

## Next

Not more contracts -- all three planned contracts exist now. The next step
is live verification: work through `deploy/STUDIO_TESTING_GUIDE.md` on
GenLayer Studio (deploy order: Gate, then Router with the Gate's address,
then CertificationGate with the Router's address) and report back
pass/fail per step, especially the "spoofed obligation" rejection and the
`get_unblocked_stages` parallel-unblocking behavior -- those are the two
things this whole project exists to guarantee, and neither has been
verified against real cross-contract execution yet.
