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

**Update (post-independent-review pass):** a second independent review
found and closed four more issues -- see `docs/SECURITY.md` §10 for the
full writeup. Two are relevant to anyone integrating with this repo right
now: (1) `ProcessGraphRouter` has a new required step,
`bind_obligation_stage_type`, between creating an obligation and
referencing it in `register_process` (see "DAG payload format" below); (2)
`refresh_process_status` now also fails the whole process when a
non-mandatory stage that something else depends on is `REJECTED`, closing
a dead-end that previously left such a process stuck `ACTIVE` forever.
Current total: **61/61 tests passing** (25 Gate, 30 Router, 6
CertificationGate).

## The primitive: Semantic Obligation Gate

obligation + policy + untrusted evidence
-> semantic adjudication (leader, independently)
-> GenLayer consensus (validators re-derive, compare structured fields)
-> finalized verdict (APPROVED / REJECTED / UNDETERMINED)
-> deterministic state transition (never inside the nondet block)


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

contracts/semantic_obligation_gate.py the adjudication primitive — 25/25 tests pass
contracts/process_graph_router.py the DAG orchestrator — 30/30 tests pass
contracts/certification_gate.py the deterministic terminal step — 6/6 tests pass
tests/test_semantic_gate.py Direct Mode tests, executed
tests/test_process_graph_router.py Direct Mode tests, executed (Router-isolated scope)
tests/test_certification_gate.py Direct Mode tests, executed (isolated scope)
deploy/STUDIO_TESTING_GUIDE.md manual live cross-contract test sequence
deploy/LIVE_RESULTS.md real transaction record from live Studio runs
docs/architecture.md design/architecture — data model, state machine, consensus model
docs/SECURITY.md everything security: threat model, limitations, audit history


All three contracts now exist and have been deployed live on GenLayer
Studio. See `docs/SECURITY.md` for the full security picture and
`deploy/LIVE_RESULTS.md` for the real transaction record, including a
live-confirmed rejection of an obligation-replay attack. See
`docs/architecture.md` §10 for the development order followed.

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
bind_obligation_stage_type(obligation_id: str, stage_type: str) -> None   # that stage_type's authority only, once per obligation
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
deployment, created by the address registered (via `register_authority`)
as the authority for that stage's `stage_type` -- **and** that authority
must have explicitly called `bind_obligation_stage_type(obligation_id,
stage_type)` beforehand. The second step exists because the Gate itself
has no concept of `stage_type`: if one address is registered as authority
for more than one `stage_type` (a realistic setup -- see
`deploy/STUDIO_TESTING_GUIDE.md`'s own reference scenario), the address
check alone cannot tell which stage_type a given obligation was actually
meant for. See `docs/SECURITY.md` §10 finding #13 for the full writeup and
`docs/architecture.md` §14 for the original address-spoofing check this
complements.

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

## Security, limitations, and audit history

All of it lives in `docs/SECURITY.md` now, not here or in
`docs/architecture.md` -- threat model coverage for all three contracts,
every known limitation (closed and still-open), the post-live-testing
adversarial self-review, an independent external audit's findings, and a
second independent review's findings, each with what was fixed, what was
deliberately left alone, and why. One highlight worth surfacing here: the
external audit's most important finding (evidence URLs are mutable, so
nothing guaranteed a leader and a validator fetched identical content) is
fixed, and a plausible-sounding but actually-harmful "fix" for a related
finding was caught and reverted before shipping -- see `docs/SECURITY.md`
§9, finding #1, for the full story of why that mattered. A second
highlight: the second independent review found that the fix for evidence
integrity had itself introduced a new problem -- hashing raw exception text
for failed fetches could prevent consensus from ever reaching
`UNDETERMINED` for a genuinely unreachable source -- see `docs/SECURITY.md`
§10, finding #11.

## Before you trust this

1. `pip install genlayer-test && pytest tests/ -v` -- 61/61 pass as of
   this writing; re-run it yourself to confirm on your machine (pins
   `sdk_version="v0.2.16"` internally -- see `docs/architecture.md` §23.1
   for why that pin currently matters).
2. Read `docs/SECURITY.md` -- it states plainly what's closed, what's
   confirmed-safe-by-design, and what's still an open, named trade-off
   (prompt-injection resistance against a real model, evidence-hash
   commitment strength, retry-cost griefing, and a few others).
3. All three contracts have now been deployed and exercised live on
   GenLayer Studio, including a live-confirmed rejection of an
   obligation-replay attack -- see `deploy/LIVE_RESULTS.md` for the full
   transaction record, and `deploy/STUDIO_TESTING_GUIDE.md` if you want to
   reproduce it yourself. Note: the guide's reference scenario now
   includes the required `bind_obligation_stage_type` step (§10 finding
   #13) -- if you're comparing against the transaction hashes in
   `deploy/LIVE_RESULTS.md`, those predate that step and were run against
   the contracts as they stood before this review pass.

## Next

All three planned contracts exist and have been proven both in Direct
Mode (61/61 tests) and live on Studio (`deploy/LIVE_RESULTS.md`, predating
the post-independent-review fixes in `docs/SECURITY.md` §10). What's left
is exactly what `docs/SECURITY.md` lists as still open -- most notably a
live adversarial-evidence adjudication against a real model to test
prompt-injection resistance under real conditions, not mocked ones, and a
fresh live/glsim run exercising the `bind_obligation_stage_type` step and
the non-mandatory-REJECTED-dependency fix (§10, findings #13-#14), neither
of which has been re-verified live since being added.
