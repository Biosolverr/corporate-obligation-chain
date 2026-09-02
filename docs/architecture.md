# Architecture — SemanticObligationGate + ProcessGraphRouter + CertificationGate

Status: **all three contracts implemented, live-deployed on GenLayer
Studio, and 56/56 tests executed for real** against genlayer-test 0.29.2 /
GenVM v0.2.16 in Direct Mode (23 Gate, 27 Router, 6 CertificationGate).
This is not a syntax check: real storage descriptors, real
`run_nondet_unsafe` leader/validator capture, real `gl.vm.UserError`
reverts, real `Root.upgraders`/`lock_default()` calls, and multiple real
on-chain transactions on a live network, including a confirmed live
rejection of an obligation-replay attack. See "Verification log" (§23)
for the Direct Mode testing history, `deploy/LIVE_RESULTS.md` for the
live Studio transaction record, and `SECURITY.md` for the full threat
model, known limitations, and audit history (both the self-review after
live testing and an independent external audit) — kept in its own file
so security content isn't duplicated or scattered across documents.

## 0. Three contracts, one chain

This repo now covers two reference use cases built on the exact same
`SemanticObligationGate`, unmodified, composed via `ProcessGraphRouter`
into a graph, terminating in `CertificationGate`:

1. **Corporate Obligation Chain** — linear procurement (PO -> Delivery ->
   Acceptance -> Invoice). A degenerate DAG: one edge per consecutive pair.
2. **Administratum** — "consensus against bureaucracy". Independent
   regulatory checks (fire safety, sanitary, cadastre, ...) that don't
   actually depend on each other get adjudicated in parallel instead of
   forced through a sequential human queue, converging on a final review
   stage that does depend on all of them.

`ProcessGraphRouter` is the shared orchestration layer for both: procurement
is simply the special case of a DAG shaped like a straight line. No part of
`SemanticObligationGate` changed to support this generalization.
`CertificationGate` is the deterministic terminal step for both: it never
moves money or issues anything itself, only flips a permanent eligibility
flag once the Router reports a process `COMPLETE` — see Part C.

## 1. What exists right now

```
corporate-obligation-chain/
├── contracts/
│   ├── semantic_obligation_gate.py   <- implemented, 14/14 tests pass
│   ├── process_graph_router.py       <- implemented, 19/19 tests pass
│   └── certification_gate.py         <- implemented, 5/5 tests pass
├── tests/
│   ├── test_semantic_gate.py
│   ├── test_process_graph_router.py
│   └── test_certification_gate.py
├── deploy/
│   └── STUDIO_TESTING_GUIDE.md       <- manual live cross-contract test sequence
└── docs/
    └── architecture.md               <- this file
```

`CertificationGate`/`SettlementGate` (deterministic terminal step that turns
a `COMPLETE` process into `SETTLEMENT_ELIGIBLE` / `CERTIFIED_ELIGIBLE`) is
**not built**. Per the development order (and good sense): prove each layer
before composing the next one on top.

## 2. Why GenLayer, specifically, for this piece

The removal test: delete GenLayer and keep everything else (roles, storage,
hashing, timestamps, deterministic transitions) — what's left standing is a
normal contract that can check `quantity <= budget` but cannot decide
"does this PDF delivery note, cross-referenced against this policy, actually
satisfy the obligation". That question requires interpreting unstructured
evidence against natural-language rules. A centralized backend + LLM can
answer it too, but then a single operator and a single model instance *is*
the adjudicator — counterparties have to trust your server.

`SemanticObligationGate` makes the adjudicator plural and independently
re-derivable: a leader and a validator set are chosen by the protocol (not
by the contract author), each independently fetches evidence and runs the
same policy-evaluation prompt, and the result is only written to state if
they agree on the **semantic fields** (`decision`, `quantity_match`,
`specification_match`, `deadline_match`, `critical_exception`) — not on
matching LLM prose. That is `docs.genlayer.com`'s Equivalence Principle,
applied to adjudication instead of to a single number.

## 3. Deterministic / non-deterministic boundary (verified against docs)

Per `developers/intelligent-contracts/features/non-determinism`:

- All `gl.nondet.*` calls (evidence fetch via `gl.nondet.web.request`, LLM
  call via `gl.nondet.exec_prompt`) live strictly inside `leader_fn` /
  `validator_fn`, themselves inside `gl.vm.run_nondet_unsafe`.
- No `self.obligations[...] = ...` write happens until *after*
  `run_nondet_unsafe` returns. Grep the file: every storage write is below
  the `# ---- Deterministic section starts here. ----` comment in
  `adjudicate()`.
- The nondet closures capture **plain local variables**
  (`policy_text`, `deadline_iso`, `evidence_refs`) copied out of `self`
  *before* the closures are defined — not `self` itself. This sidesteps the
  documented "reading from storage directly in non-deterministic blocks"
  limitation without needing `gl.storage.copy_to_memory` (that helper is for
  copying whole storage *objects*; here we only need a handful of scalars /
  a plain list, so plain Python locals are enough and are unambiguously
  safe).

## 4. Evidence model

Only a **reference** (URL) + a caller-supplied **hash** of the evidence
bundle are ever written to contract storage (`evidence_refs`,
`evidence_hash`). Raw documents are fetched fresh, per-node, inside the
nondet block and never persisted — matching `features/web-access`'s
"Consensus-Friendly Web Requests" guidance (stable fields / derived status,
not raw bytes, are what consensus should run on — here that's taken further:
not even the extracted *raw text* is compared, only the model's structured
adjudication of it).

Evidence fetch failures are converted into evidence content
(`"[EVIDENCE_FETCH_FAILED: ...]"`) rather than crashing the transaction —
the LLM is instructed to treat that as insufficient evidence, which
naturally routes to `UNDETERMINED` rather than a hard revert. This avoids a
single flaky HTTP source turning into a denial-of-service on the whole
obligation.

## 5. Prompt-injection posture

Per `security-and-best-practices/prompt-injection`'s three strategies
("restrict inputs", "restrict outputs", "construct prompts within the
contract code"):

- The prompt is built entirely inside `_build_prompt` in contract code. The
  caller never supplies free-form prompt text — only `policy` (set once, at
  creation, by the buyer) and evidence references (URLs fetched by the VM).
- POLICY / DEADLINE / EVIDENCE / QUESTION / OUTPUT SCHEMA are kept in
  clearly labeled, separate sections, with an explicit instruction that
  EVIDENCE content is inert data, never a command.
- Output is restricted to a fixed JSON schema, validated structurally by
  `_is_valid_verdict` before it can ever touch storage — an LLM "helpfully"
  complying with an injected instruction to output `{"decision":
  "APPROVED", ...}` still has to pass **independent** validator
  re-derivation to be written; an attacker would need to compromise a
  majority of an unpredictable validator set, not just craft a clever
  document.

This is a mitigation, not a proof of immunity — see Known Limitations.

## 6. State machine

```
CREATED --submit_evidence--> EVIDENCE_SUBMITTED --adjudicate-->
    +-- (decision APPROVED/REJECTED) --> FINALIZED   [terminal]
    +-- (decision UNDETERMINED)      --> UNDETERMINED --adjudicate--> (loop)
```

`FINALIZED` is terminal for `submit_evidence` and `adjudicate` alike
(explicit checks in both methods) — this is the finality-bypass / reopening
guard from the threat model.

`REJECTED` and `UNDETERMINED` are never conflated: the prompt and the
validator both treat them as distinct fields of the same schema, and the
contract's `if decision == DECISION_UNDETERMINED` branch is the only place
that routes to the non-terminal status — `REJECTED` always finalizes.

## 7. Authority model

| Actor | Can | Cannot |
|---|---|---|
| Buyer (`create_obligation` caller) | create obligation, submit evidence | write a decision directly, finalize own obligation, use own address as supplier |
| Supplier | submit evidence | write a decision directly, finalize own obligation |
| Anyone | call `adjudicate()` | influence the outcome — the caller doesn't participate in consensus, they only trigger it |

`adjudicate()` is intentionally permissionless. It doesn't decide anything —
GenLayer consensus does. Restricting the trigger to buyer/supplier would add
a liveness risk (the party disadvantaged by a verdict simply never calls it)
without adding security, since the verdict itself is produced by leader +
validator agreement, not by whoever happened to call the method.
## 8-9. Threat model and known limitations

Moved to `SECURITY.md` (§1-§2) to keep all security content in one place
rather than split across two files. This section intentionally left as a
pointer, not a duplicate.

## Part B — ProcessGraphRouter ("Administratum" direction)

### 11. Why a second contract instead of extending the Gate

`SemanticObligationGate` is a single-obligation primitive on purpose: one
policy, one evidence bundle, one verdict — and it is already proven-by-design
against a specific, narrow threat model (section 8). Composing many
obligations into a dependency graph is a different problem entirely
(topology, cross-stage authority, parallelism, cycle safety) with its own
failure modes that have nothing to do with semantic adjudication. Keeping
them as two contracts means the Gate never has to change to support new
graph shapes, and the Router never has to know anything about prompts,
evidence, or LLMs — it is 100% deterministic and calls no `gl.nondet.*` API
at all.

### 12. The role reversal that must not be missed

Procurement's buyer/supplier roles do not carry over unchanged. In
procurement, the buyer wants proof from the supplier. In a regulatory/permit
graph, the **regulator is the `buyer`** (it writes policy — "these are the
fire-safety rules") and the **applicant is the `supplier`** (submits
evidence of compliance). Reversing this (applicant as buyer) would let an
applicant write their own compliance policy, defeating the mechanism
entirely. This is documented prominently in `process_graph_router.py`'s
module docstring specifically so a future reuser of the primitive for a new
domain does not get this backwards.

### 13. Read-only composition: why the Router never calls `.emit()`

Per `docs.genlayer.com`'s "What is GenLayer" architecture section, every
Intelligent Contract has a ghost contract that *"relays transactions to
consensus, and executes external messages"* — meaning `.emit()` sends a
message through a **second round of protocol consensus**, not a synchronous
call. The Router never needs to change Gate state, only read it, so it only
ever uses `.view()`. This is stated as an explicit, permanent architectural
boundary (not just an implementation detail) precisely because it would be
easy to reach for `.emit()` without noticing the async-consensus complexity
that comes with it.

### 14. Closing the "spoofed obligation" hole

A naive router would accept any `obligation_id` a caller supplies for a
stage, including one belonging to a completely unrelated, already-approved
obligation. `register_process` closes this by reading each obligation's
`buyer` field live from the Gate (`gate.view().get_obligation(...)`) and
checking it equals the address registered as the authority for that stage's
`stage_type`. Since `buyer` is set once at obligation creation and no Gate
method ever mutates it, this check cannot be spoofed without controlling the
real authority's private key.

### 15. Never caching Gate decisions

`get_unblocked_stages`, `get_process_status` (read) and
`refresh_process_status` (write) all re-read Gate state live on every call.
The Router's own storage holds only graph *structure*
(`stage_id -> obligation_id`, edges) and its own derived `status`
(ACTIVE/FAILED/COMPLETE) — never a copy of the Gate's `decision` fields.
This avoids a second, independently-stale source of truth if a `Gate`
obligation is re-adjudicated from `UNDETERMINED`.

### 16. Cycle safety

`register_process` runs Kahn's algorithm (`_assert_acyclic`) over the
declared edges before accepting a graph. A cyclic dependency would make some
stage permanently unblockable — this is rejected at registration time, not
discovered later as a stuck process.

### 17. Admin / authority-registry trade-off, and its exit path

The `stage_type -> authority address` registry is written by a single
`admin` address (whoever deployed the Router) via `register_authority`.
This is a real centralization point for this MVP, stated openly rather than
downplayed. Its mitigation uses a GenVM-native mechanism instead of a
custom one: the same admin address is seeded into the contract's `upgraders`
list (`docs.genlayer.com` → Upgradability: `gl.storage.Root`, `upgraders`,
`locked_slots`). Once a domain's authority set is considered stable, whoever
controls `upgraders` can call `root.lock_default()` with an emptied
`upgraders` list, **irreversibly freezing** the contract — turning a
temporary, publicly-auditable trust assumption into permanent immutability.
Until frozen, a compromised admin key can rewrite the registry (or, via
`upgraders`, the whole contract) — the same trade-off every upgradable
contract on any chain accepts; it is not hidden or special-cased here.

### 18. Fail-fast, not fail-stop

If a mandatory stage is `REJECTED`, `refresh_process_status` immediately
marks the whole process `FAILED`. The Router cannot stop other, independent
`adjudicate()` transactions already in flight on the Gate for other stages
of the same process — it has no write access to the Gate at all — so
"fail-fast" here means "the Router stops counting those other stages
toward completion", not "the Router prevents them from running". This is
inherent to the read-only design, not a gap.

### 19. Liveness without a timeout

No stuck-`UNDETERMINED` timeout is implemented, deliberately.
`SemanticObligationGate.adjudicate()` is already permissionless and callable
by anyone, at any time, as many times as needed — so liveness does not
require the Router to add a redundant mechanism. Adding a
timeout-to-decision would also risk exactly the REJECTED/UNDETERMINED
conflation the Gate was built to avoid (section 6): if evidence never
improves, staying `UNDETERMINED` forever is the *correct* outcome, not a
bug to route around.

### 20. Graph size limit

`max_stages_per_process` is a constructor parameter (deployer-chosen per
domain) capped by a hardcoded `HARD_MAX_STAGES_CEILING = 32` that no
deployer can exceed. This bounds the O(stages) cross-contract `.view()`
calls each `register_process` / `get_unblocked_stages` /
`refresh_process_status` call performs.
## Part C — CertificationGate (the deterministic terminal step)

### 24. What it is and isn't

`CertificationGate` is the "SettlementGate" from the original brief's
section 22, generalized past procurement the same way `ProcessGraphRouter`
generalized `ProcessRouter`: it reads whether a `ProcessGraphRouter`
process is `COMPLETE` and, if so, permanently records a single generic
`ELIGIBLE` flag for that `process_id`. It never moves money, never issues
a permit, and never calls `.emit()` on the Router — same read-only
boundary as Router→Gate. What a caller does with a confirmed-`ELIGIBLE`
flag (release an escrow, print a certificate) is deliberately outside its
scope: that action needs its own, domain-specific authority model over
whatever value is being moved, which is exactly the complexity the
original brief said an MVP should not build (section 22: "не делай
сложную treasury system").

### 25. Why permissionless, consistently

`claim_eligibility()` has no sender restriction, for the same reason
`SemanticObligationGate.adjudicate()` and
`ProcessGraphRouter.refresh_process_status()` don't: it doesn't decide
anything, it only reads an already-decided fact and records it. The
outcome is fully determined by state that already exists on the Router
(and, transitively, the Gate) before this call happens — restricting who
may trigger that recording would add a liveness risk without adding any
security.

### 26. The finality chain this contract's guarantee depends on

`CertificationGate`'s own `ELIGIBLE` flag is only as trustworthy as the
finality of the two contracts under it, and that trust isn't assumed —
it's structural: `SemanticObligationGate.FINALIZED` is terminal (§6);
`ProcessGraphRouter`'s `COMPLETE`/`FAILED` are terminal, monotonic
transitions out of `ACTIVE`, themselves guarded by the Gate's finality
above them (module docstring, State Machine section); and this contract
refuses to run `claim_eligibility` a second time for an already-claimed
`process_id`. There is no path anywhere in the three-contract chain for an
already-recorded `ELIGIBLE` flag to later become invalid.

### 27. Fail-safe finding

Moved to `SECURITY.md` (§5).

## Adversarial self-review and external audit findings

Both the post-live-testing adversarial self-review and the independent
external audit's findings — what was closed, what was deliberately left
open, and why — live in `SECURITY.md` (§8-§9), not here, to keep all
security-relevant content in one place. `SECURITY.md`'s own tallies
supersede the test counts quoted earlier in this file's history; the
current total is 56/56 (`pytest tests/ -v`).

## 10. Development order followed

```
1. Inspect current GenLayer docs/API              <- done this session
2. Lock architecture for this one contract          <- done (this file)
3. Define data model                                <- done (Obligation dataclass)
4. Define state machine                             <- done (section 6)
5. Define authority model                           <- done (section 7)
6. Define consensus semantics                       <- done (section 2, 3)
7. Define evidence model                            <- done (section 4)
8. Define security invariants                       <- done (section 8)
9. Define proof tests                               <- done (tests/test_semantic_gate.py)
10. Implement SemanticObligationGate                 <- done
11. Run tests                                        <- DONE, 14/14 pass (§23)
12. Run adversarial tests                             <- partial: schema/structural adversarial cases pass; live prompt-injection needs Studio (`SECURITY.md` §2, prompt injection limitation)
13. Verify actual GenLayer behavior (Studio)          <- NOT DONE (Direct Mode only so far)
14. Implement ProcessGraphRouter                      <- done (Part B)
15. Run Router tests                                  <- DONE, 19/19 pass, Router-isolated scope only (§23.4)
16. Implement CertificationGate                       <- done (Part C)
17. Run CertificationGate tests                       <- DONE, 5/5 pass, isolated scope only (§23.4)
18-22. Live cross-contract verification, deploy, etc.  <- NOT STARTED — deploy/STUDIO_TESTING_GUIDE.md is the plan
```

Steps 11, 12 (partial), 15, and 17 are now DONE (see §23 below) for
everything that could be run in Direct Mode. What remains before step
18+ is exactly the glsim/localnet or Studio verification that Direct Mode
cannot provide by construction (§23.4) — the manual sequence for that is
`deploy/STUDIO_TESTING_GUIDE.md`.
## 23. Verification log (this session)

Ran against a real installed `genlayer-test==0.29.2` (Python 3.12), which
pulled in `genlayer-py==0.16.3` and downloaded a real GenVM SDK build. This
is the actual record of what was executed, in order, including the
mistakes found and fixed along the way — kept here rather than only
showing the final green state, because the mistakes are exactly the kind
of thing "don't invent the API" is meant to catch, and future-reuse of
these contracts should know they were real, not assumed.

**Result: 14/14 `test_semantic_gate.py` pass. 13/13
`test_process_graph_router.py` pass (Router-only scope — see below for
why). 27/27 total.**

### 23.1 `genlayer-test`'s Direct Mode SDK downloader is stale against the
current GenVM "latest" release

`sdk_loader.py` (inside `genlayer-test`) downloads
`genvm-universal.tar.xz` from the GitHub release tagged "latest". As of
this session, GitHub's "latest" tag for `genlayerlabs/genvm` is
`v0.3.0-rc7` (published 2026-06-19), and that release — and the whole
`v0.3.0-rc*` series — ships per-platform archives
(`genvm-linux-amd64.tar.xz`, etc.) instead of a universal bundle. The last
release that still publishes `genvm-universal.tar.xz` is `v0.2.16`
(2026-03-10). Both test files now pin `sdk_version="v0.2.16"` explicitly
for every deploy call for this reason. **This is a real, dated
compatibility gap between the installed `genlayer-test` version and the
current GenVM release line**, not a mistake in the contracts — worth
re-checking whether a newer `genlayer-test` release has caught up before
assuming `v0.2.16` pinning is still necessary.

### 23.2 `DynArray[T]()` cannot be called directly — real contract bug, now fixed

Both contracts originally did `DynArray[str]()` (and `DynArray[StageRef]()`
/ `DynArray[Edge]()` in the Router) to build a fresh empty collection
before assigning it to a dataclass field or a local variable that would
later be assigned into storage. This fails at runtime:
`TypeError: this class can't be instantiated by user` — confirmed by
reading `genlayer/py/storage/vec.py`: `DynArray.__init__` unconditionally
raises; `DynArray` instances only exist as storage-backed views created by
the storage system itself, never by direct user construction. The fix,
also confirmed by reading `_DynArrayDesc.set` in the same file: it accepts
a plain `collections.abc.Sequence` (an ordinary Python `list`) and builds
the storage entries from it. **Every place that did `DynArray[T]()` now
uses a plain `[]` / `list[T]` instead** — both in dataclass field
defaults passed to constructors (e.g. `Obligation(..., evidence_refs=[])`)
and in local accumulator variables (e.g. `deduped: list[str] = []`). No
change was needed anywhere a field is read from an *existing* storage-backed
object (`for ref in obligation.evidence_refs`) — iteration already worked
correctly; only fresh construction was broken.

### 23.3 `direct_alice`/`direct_bob`/`direct_charlie` fixtures silently
return raw `bytes` instead of `Address`

`gltest.direct.loader.create_address()` does
`try: from genlayer.py.types import Address; return Address(addr_bytes)
except ImportError: return addr_bytes`. The `direct_alice`/`direct_bob`/
`direct_charlie` pytest fixtures call this at fixture-resolution time,
which happens *before* the test function body runs — and therefore
*before* any `direct_deploy(...)` call has set up `sys.path` for
`genlayer` (that only happens inside `deploy_contract` →
`load_contract_class` → `setup_sdk_paths`). So on a fresh test, these
fixtures always hit the `ImportError` branch and silently return raw
`bytes`. The failure surfaces much later and far from the real cause: a
`bytes` object flows all the way into a storage field typed `Address`, and
fails deep inside the SDK with
`AttributeError: 'bytes' object has no attribute 'as_bytes'` — a
confusing error for what is really a fixture-ordering issue, not a
contract bug. **Both test files now avoid these three fixtures entirely**
and instead call `create_address(seed)` directly, after SDK paths are
known to be loaded: `test_semantic_gate.py` does this by only computing
addresses after its (mandatory, always-first) `direct_deploy(...)` call;
`test_process_graph_router.py` needs an `Address` *before* its first
deploy (the constructor takes a `gate_address` argument), so it calls
`gltest.direct.sdk_loader.setup_sdk_paths(...)` directly, unconditionally,
on every single address request — not just once per test file. That
"unconditionally" matters: `VMContext._cleanup_after_deactivate()` strips
SDK paths from `sys.path` and evicts `genlayer` modules from
`sys.modules` at the end of *every* test (by design, to prevent
stale-SDK-version conflicts between tests using different `sdk_version`
pins). A first attempt at this fix used a "load once, cache a flag" guard,
which passed for the first one or two tests in the file (the ones that
revert before ever touching an `Address` field) and then failed on every
test after that with the exact same confusing `bytes`/`as_bytes` error,
for the same underlying reason, one layer later. Caching "is the SDK
loaded" across tests is wrong in this framework; call the setup every time.

### 23.4 GenVM enforces one contract per process — Direct Mode cannot test
cross-contract calls at all

Attempting to deploy both `SemanticObligationGate` and
`ProcessGraphRouter` in the same Direct Mode session
(`vm.activate()` block) fails with:
`TypeError: only one contract is allowed; first:
<SemanticObligationGate>; second: <ProcessGraphRouter>`. This comes from
`genlayer/gl/genvm_contracts.py`'s `__known_contract__` module-level
singleton check — **a genuine GenVM runtime constraint** (in production,
one WASM module = one contract), not a testing-tool limitation. Direct
Mode's `gl_call` handler explicitly stubs out cross-contract operations
(`DeployContract`/`CallContract`/`PostMessage`) unless a `_gl_call_hook`
is installed, which none of the standard `direct_*` pytest fixtures do (a
hook is how "glsim" mode presumably wires two in-process contracts
together, or a real multi-contract localnet would just work via
Integration Testing — neither was available in this sandbox).

**Practical consequence for `ProcessGraphRouter`:** every code path that
calls `_read_gate_obligation` (i.e., the authority-ownership check inside
`register_process`, all of `get_unblocked_stages`, and all of
`refresh_process_status`) is **not exercised by any test in this
session**, and cannot be, in Direct Mode. `tests/test_process_graph_router.py`
was restructured to deploy the Router alone (no Gate) and test only what
runs *before* any cross-contract call is reached: constructor bounds,
`register_authority`'s admin check, and every branch of DAG structural/
cycle validation in `register_process` (all of which run and fail-fast, if
they're going to fail at all, strictly before `_read_gate_obligation` is
ever called). This is a real, load-bearing scope boundary, not a
convenience choice — the module docstring of that test file says so
explicitly, and it is repeated here so it isn't missed: **the "spoofed
obligation" authority check (§14) and both live-status query methods
remain unverified by execution.** Verifying them requires either a glsim
harness with `_gl_call_hook` wired to a second in-process contract
instance, or GenLayer's Integration Testing mode against a running
localnet.

### 23.5 What this does and doesn't license claiming (Gate + Router)

Because of §23.4, "38/38 tests pass" (a historical snapshot at that point
in testing, before the external audit added more) was an honest but
*partial* proof: it fully covers PROOF-1 through PROOF-4 for
`SemanticObligationGate` (the whole point of Milestone 1) and fully
covers the Router's and CertificationGate's deterministic logic in
isolation, but did **not** cover any contract's actual reason for
depending on another — safely composing Gate instances via the Router,
or the Router's completion signal via CertificationGate — end to end.
That composition was reasoned through carefully (§11–§20, `SECURITY.md`
§3-§5) and the code was written to the same standard throughout, but
"reasoned through" and "executed" are different claims, and this
project's own rule (§37 of the original brief: "source code alone is not
proof of live behavior") applies here too. What that Studio/localnet run
required is exactly what `SECURITY.md` §4's limitations already flagged
— `deploy/STUDIO_TESTING_GUIDE.md` was the manual sequence for it, and
that run has since happened: see `deploy/LIVE_RESULTS.md`.

### 23.6 Direct Mode's cross-contract call quirk

(Moved to `SECURITY.md` §6 — a security-relevant discovery, kept there
with the rest of the audit trail rather than duplicated here.)

### 23.7 Full tally before the live-Studio bug (§23.8) was found and fixed

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 14 | 14 passed |
| `test_process_graph_router.py` | 19 | 19 passed |
| `test_certification_gate.py` | 5 | 5 passed |
| **Total** | **38** | **38 passed** |

All three contracts' code paths that do NOT require a real second
deployed contract were proven by execution, not just by reading. Every
cross-contract call in this repository — Router→Gate,
CertificationGate→Router — remained unverified by any test run and
required `deploy/STUDIO_TESTING_GUIDE.md`'s manual sequence to close --
which is exactly what surfaced §23.8.

### 23.8 Real vulnerability class found via live Studio deployment

(Moved to `SECURITY.md` §7 — a real vulnerability class found via live
deployment, kept with the rest of the security audit trail rather than
duplicated here.)

### 23.9 Tally as of §23.8 (superseded — see `SECURITY.md`'s own tallies
for the current, post-adversarial-self-review and post-external-audit
totals; the current repository-wide total is 56/56, see the top of this
file and `SECURITY.md`)

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 15 | 15 passed |
| `test_process_graph_router.py` | 21 | 21 passed |
| `test_certification_gate.py` | 6 | 6 passed |
| **Total** | **42** | **42 passed** |

Cross-contract calls between the three contracts (Router→Gate,
CertificationGate→Router) were still unverified by any test run in this
repository at this point and still required `deploy/STUDIO_TESTING_GUIDE.md`'s
manual sequence (or a glsim/Integration Testing harness) to close -- §23.8
was a real bug found via a *partial* live deployment attempt (a
constructor call failing before any cross-contract interaction was even
reached), not a substitute for that full sequence. **Update: that further
Studio testing has since happened** — see `deploy/LIVE_RESULTS.md`
scenarios 3-4 for the anti-replay fix (`SECURITY.md` §9, findings #4/#5)
confirmed live, not just in Direct Mode.
