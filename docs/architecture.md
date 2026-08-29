# Architecture — SemanticObligationGate + ProcessGraphRouter + CertificationGate

Status: **all three contracts implemented; 45/45 tests executed for real**
against genlayer-test 0.29.2 / GenVM v0.2.16 in Direct Mode (18 Gate, 21
Router, 6 CertificationGate) -- AND one real bug already found and fixed
via an actual live Studio deployment attempt (§23.8: Address-typed
arguments arriving as plain `int`, not `Address`). This is not a syntax
check: real storage descriptors, real `run_nondet_unsafe` leader/validator
capture, real `gl.vm.UserError` reverts, real
`Root.upgraders`/`lock_default()` calls, and one real on-chain failure
traced to its exact line and fixed. See "Verification log" (§23) for
everything, including what is still NOT verifiable outside a live
environment (the real cross-contract calls between all three contracts).
`deploy/STUDIO_TESTING_GUIDE.md` has the exact manual sequence for that
remaining live verification.

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

## 8. Threat model coverage (this file only — Router/Settlement threats are
out of scope until those contracts exist)

| Threat | Handling | Test |
|---|---|---|
| Unauthorized evidence submission | `sender != buyer and sender != supplier` revert | `test_unauthorized_party_cannot_submit_evidence` |
| Duplicate obligation id | existence check before create | `test_duplicate_obligation_id_rejected` |
| Duplicate evidence ref in one submission | de-duped in `submit_evidence` | `test_duplicate_evidence_refs_are_deduped` |
| Reopening / resubmission after finality | blocked in both `submit_evidence` and `adjudicate` | `test_evidence_cannot_be_resubmitted_after_finalization` |
| Wrong order (adjudicate before evidence) | status check in `adjudicate` | `test_adjudicate_rejected_when_no_evidence_submitted_yet` |
| Prompt injection via evidence | evidence fenced as DATA in prompt + structural output validation | see Known Limitations |
| Malformed / off-schema LLM output | `_is_valid_verdict` rejects; validator disagrees; no state change | `test_malformed_llm_output_never_finalizes` |
| Validators seeing different evidence / disagreeing | `validator_fn` re-derives independently, compares semantic fields only | `test_validator_disagrees_on_different_evidence` |
| Consensus failure | GenVM-level: no `run_nondet_unsafe` return means no write ever executes (Python never reaches the deterministic section) | inherent to the code structure, not separately testable in Direct Mode |
| REJECTED used to mask UNDETERMINED | separate decision values, separate status branch | `test_undetermined_is_not_rejected` |

## 9. Known limitations (stated, not hidden)

1. ~~hashlib availability is assumed~~ **CONFIRMED**: `hashlib.sha256` runs
   without error inside the Direct Mode GenVM Python environment (v0.2.16) —
   exercised by every test that calls `create_obligation`. This is Direct
   Mode, not Studio/a live node, so it confirms the stdlib call works in a
   real GenVM Python build, but a Studio smoke test is still the stronger
   claim before final submission (Direct Mode's Python runtime and the
   on-chain WASM one could in principle diverge, though nothing found this
   session suggests they would).
2. ~~`tests/test_semantic_gate.py` has not been executed~~ **EXECUTED,
   14/14 passing** against real `genlayer-test==0.29.2` / GenVM v0.2.16 —
   see `docs/architecture.md` §23 for the full log, including three real
   bugs (one in this contract, two fixture/framework quirks) found and
   fixed by actually running it rather than by reading the docs alone.
3. **Prompt injection mitigation is not proven, only reasoned about.** No
   automated adversarial-evidence test exists yet because it requires either
   a real LLM call or a very deliberately hand-crafted mock; this needs to
   be a Studio-level test (real model) before the "no unauthorized
   transition" claim in the brief's security section 21 can be considered
   demonstrated rather than argued.
4. **Only one evidence-fetch failure mode is handled explicitly** (HTTP
   exception -> placeholder text). Stale-but-200 responses, partial
   downloads, and non-JSON/non-text bodies are not specially classified;
   they flow into the LLM as raw text and rely on the model + validator
   consensus to call `UNDETERMINED` when they're not useful. This is a
   deliberate simplification for the first proof, not an oversight — but it
   means "stale evidence -> REJECTED" from the original threat list (section
   21) is currently handled as "stale evidence -> likely UNDETERMINED",
   which is arguably the more correct semantics (see decision-value
   discussion above) but is a deviation worth flagging explicitly.
5. **No appeal-specific contract logic.** GenLayer's own protocol-level
   appeal window (Optimistic Democracy) already provides a mechanism to
   contest an `Accepted` transaction before it's `Finalized`; this contract
   does not duplicate that at the application level. If a domain later
   needs application-level disputes *after* finality, that is exactly the
   `DISPUTED` state the original brief sketched — deliberately deferred
   until there's a concrete need driving its design, per the simplification
   rule (do not add complexity/state to look thorough).

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

### 21. Threat model additions (Router-specific)

| Threat | Handling | Test |
|---|---|---|
| Spoofed / unrelated obligation reused for a stage | `buyer` field cross-checked against registered authority at registration | `test_spoofed_obligation_is_rejected` |
| Unregistered stage_type accepted silently | explicit existence check via `registered_stage_types`, revert otherwise | `test_unregistered_stage_type_rejected` |
| Cyclic dependency graph | Kahn's-algorithm cycle detection at registration | `test_cycle_rejected` |
| Duplicate process_id | existence check before registration | `test_duplicate_process_id_rejected` |
| Oversized graph (gas/DoS) | `max_stages_per_process`, hard ceiling | `test_too_many_stages_rejected`, `test_constructor_rejects_oversized_max_stages` |
| Non-admin registering an authority | `sender == admin` check | `test_register_authority_requires_admin` |
| Terminal status flip-flopping | monotonic transition, guarded by Gate-side finality | `test_terminal_status_does_not_flip_back` |
| Cross-contract read of a missing/invalid obligation | wrapped in try/except, clear `UserError` instead of opaque exception | inherent in `_read_gate_obligation`, not separately unit-tested |

### 22. Known limitations specific to the Router

1. Admin centralization — see section 17. Documented trade-off with a native
   exit path, not a hidden one.
2. `get_unblocked_stages` cost scales with `max_stages_per_process`; not a
   correctness issue, a cost one, bounded by design (section 20).
3. ~~`json.loads` for the DAG payload... unconfirmed~~ **CONFIRMED**:
   exercised successfully by every `register_process` test, including the
   malformed-JSON-rejection path (`json.JSONDecodeError` caught and
   converted to a clear `UserError`, exactly as designed).
4. ~~`tests/test_process_graph_router.py` has not been executed~~
   **EXECUTED, 13/13 passing** — but with a real, load-bearing scope
   limit: only code paths that run before any cross-contract `.view()`
   call. See §23.4 for exactly why and what remains unverified (the
   authority-ownership check and both live-status query methods).
5. ~~`gl.storage.Root.get().upgraders.get().append(...)`... not
   independently confirmed~~ **CONFIRMED**: every successful Router
   deployment in the test run executes this line in `__init__` with no
   error, so the write path (`root.upgraders.get().append(...)`) works
   exactly as the Upgradability docs show. Reading membership of that list
   from application code (e.g. "is sender an upgrader") remains
   unconfirmed and untested — this router still deliberately avoids
   relying on that read, keeping its own separate `admin: Address` field
   for the `register_authority` access check instead. See the module
   docstring.
6. `renounce_admin()` and `freeze_upgrades()` are two independent,
   admin-triggered exits from the centralization trade-off in limitation
   #1 above — both CONFIRMED working this session, including the
   important negative case (`freeze_upgrades()` alone does NOT disable
   `register_authority`; they are genuinely independent, not two names for
   the same switch). Neither is automatic; both require the admin (or,
   pre-freeze, someone who still controls the upgraders list) to actively
   choose to give up that power. Until called, the centralization
   trade-off is live — this is a real, live risk between deployment and
   whenever these are called, not a solved problem.

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

### 27. A real, positive finding from testing this in isolation

Direct Mode's cross-contract `.view()` call to an address with nothing
deployed there does not raise an exception — it silently returns `None`
(confirmed this session; see §23.6). `claim_eligibility` was NOT written
expecting that specific behavior (it was written expecting a possible
exception, hence the `try/except` in `_read_router_status`), but it fails
safe anyway: `None != "COMPLETE"` naturally refuses eligibility rather
than granting it. This is worth calling out because it's exactly the kind
of defense-in-depth that matters when an assumption about a dependency's
failure mode turns out to be wrong — the code didn't need to correctly
predict Direct Mode's specific behavior to stay safe against it.

## Part D — Adversarial self-review (post-live-testing pass)

This section exists because live Studio testing surfaced one real,
serious gap (§28.1) that no amount of Direct Mode testing or docs-reading
would have found — Direct Mode's evidence is always byte-identical
between a mocked leader and a mocked validator by construction, so the
whole class of "what if the evidence itself changes between fetches" bugs
is invisible to it. After finding it live, this section is a deliberate,
structured second pass over the whole three-contract chain asking "what
would a hostile reviewer find next" — closing what's cheaply closable now,
and stating plainly what isn't, rather than waiting for a review to find
it.

### 28.1 [CLOSED] Mutable evidence URLs allowed leader/validator to see
different content while still agreeing

**Finding.** `evidence_refs` are plain URLs (GitHub Gist raw links, in our
own live test). URLs are mutable — we personally edited the same gist
mid-testing and expected the next `adjudicate()` call to see the new
content. That convenience is also the vulnerability: nothing before this
fix required the leader's fetch and a validator's independent re-fetch of
the *same* `evidence_refs` to have seen the *same bytes*. `validator_fn`
only compared the six semantic decision fields
(`decision`/`quantity_match`/.../`reason_code`) — if a submitter (or a
malicious/compromised host) served different content to the leader than
to validators, and both independently happened to reach the same
high-level decision from their own (different) content, the mismatch
would go completely undetected and the obligation would finalize on
content nobody agreed was identical. This is a sharper version of the
threat model's "Evidence manipulation" entry (§8) than the original text
implied: the original framing assumed differing content would usually
also produce differing *decisions* (naturally caught by existing
comparison); it does not address content that differs but happens to
support the same conclusion (e.g., two slightly different but both-valid
certificate scans).

**Fix.** `_run_adjudication` now computes
`hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()` from the
exact evidence text it fetched, attaches it to the verdict dict as
`_evidence_content_hash` (computed by contract code, never supplied or
influenced by the LLM), and `_verdicts_semantically_equal` now compares
this hash as a seventh required field alongside the six decision fields.
If leader and validator fetch different content — for any reason,
malicious or accidental — the hashes differ, `validator_fn` returns
`False`, and no unsafe state transition happens, exactly like any other
disagreement. The agreed-upon hash is also now persisted on-chain as
`Obligation.resolved_evidence_hash` (exposed via `get_obligation`),
giving anyone auditing an obligation later a way to compare "what the
submitter claimed at submission time" (`evidence_hash`) against "what
consensus actually agreed was fetched at adjudication time"
(`resolved_evidence_hash`) — a mismatch between those two is itself a
signal worth investigating, even though neither the Gate nor this project
prescribes what to do about it (that's a policy/dispute question, not a
mechanism one).

**Verified.** `test_validator_disagrees_when_evidence_content_changes`
(Direct Mode) reproduces the exact shape of the bug — identical LLM
decision, different underlying evidence content — and confirms the
validator now disagrees where it previously would have agreed. All 18
`test_semantic_gate.py` tests pass, including this one.

**Residual risk, stated plainly.** This closes "leader and validator
disagree about what they fetched" as a silent failure mode. It does NOT
make evidence sources trustworthy or immutable — a URL that returns
IDENTICAL content to every fetcher at every point in time (because, say,
an attacker controls the host and simply hasn't bothered to serve
different content per-requester, or because the content hasn't changed
*yet*) still passes consensus even if that content is fabricated. Hash
comparison catches *inconsistency* across fetches; it cannot catch
*collusion* in what's consistently served. See §28.2.

### 28.2 [DOCUMENTED, not fixed] Mutable evidence URLs are the wrong kind
of evidence source for production

Directly informed by living through it: a GitHub Gist's default "raw"
link is versioned by content hash in the URL path, but the *unversioned*
raw link (`.../raw/<filename>`, no revision hash) always resolves to
whatever the latest edit is — which is exactly what let us "fix" our test
obligation's evidence after the fact by editing the same gist. That
convenience is a liability in production: any evidence source where the
same URL can legitimately mean different things at different times is a
weaker integrity guarantee than the rest of this system provides
everywhere else. §28.1's hash comparison protects consensus *within one
adjudication attempt*; it does nothing to stop a submitter from
legitimately editing evidence between one `adjudicate()` call and the
next re-adjudication (allowed by design, for the `UNDETERMINED` "give
better evidence" case) — which is fine for evidence maturing toward
completeness, but indistinguishable from evidence being changed to game
an outcome. **Recommendation for any real deployment: require
content-addressed evidence** (an IPFS CID, or a git-commit-pinned/
revision-pinned URL) instead of an arbitrary mutable URL, so that
`evidence_ref` itself is the commitment, not just `evidence_hash` (which,
per §28.1's design, is caller-declared and never independently verified
by the contract against the initial submission — only cross-checked
against what was fetched *at adjudication time*, via
`resolved_evidence_hash`). This project does not enforce this (accepting
any URL is simpler for an MVP/hackathon submission and matches the
original brief's "off-chain, reference + hash" evidence model), but it is
the single most important operational guidance for anyone deploying this
for real.

### 28.3 [CLOSED] No cap on `policy` length

**Finding.** `create_obligation` validated `deadline_iso` and
`obligation_id` for non-emptiness but placed no upper bound on `policy`
length — a careless or malicious buyer could submit an arbitrarily large
policy string, inflating storage cost and the size of every future
adjudication prompt indefinitely.
**Fix.** `MAX_POLICY_CHARS = 4000` (matching `MAX_EVIDENCE_CHARS_PER_SOURCE`'s
order of magnitude), enforced in `create_obligation`.
**Verified.** `test_oversized_policy_rejected` passes.

### 28.4 [CLOSED] `deadline_iso` was never validated as a real date

**Finding.** Any non-empty string was accepted as `deadline_iso`,
including garbage like `"asap"` — silently weakening `deadline_match`'s
reliability, since the LLM would have to interpret an unparseable
deadline however it saw fit, with no contract-level guarantee it was ever
a real date to begin with.
**Fix.** `create_obligation` now calls `datetime.fromisoformat(deadline_iso)`
and reverts with a clear message if it doesn't parse.
**Verified.** `test_invalid_deadline_iso_rejected` passes.

### 28.5 [CONFIRMED, no code change needed] `gate_address` and
`router_address` are already immutable post-deployment

A reviewer's natural next question after §17's admin-centralization
discussion is "what if the admin points the Router at a different, fake
Gate?" — checked this explicitly: `self.gate_address` in
`ProcessGraphRouter` and `self.router_address` in `CertificationGate` are
each assigned exactly once, in `__init__`, and grepping both files
confirms no method — including `register_authority`, `renounce_admin`,
or `freeze_upgrades` — ever reassigns either field. There is no
"swap the upstream contract" attack surface here by construction, not by
policy; worth stating explicitly since it preempts a plausible-sounding
attack that isn't actually possible.

### 28.6 [DOCUMENTED, not fixed] `adjudicate()` / `refresh_process_status()`
retry cost has no rate limit

Both are intentionally permissionless (§7, §25) for liveness reasons
already argued at length. The flip side: nothing stops anyone from
repeatedly calling `adjudicate()` on the same `UNDETERMINED` obligation
with byte-identical, unchanged evidence, burning real LLM inference cost
on every attempt for no new information — and, because LLM outputs carry
some irreducible randomness, enough retries against unchanged borderline
evidence could in principle eventually produce an `APPROVED` by chance
rather than by the evidence actually improving. This is a real, live
economic-griefing / outcome-gaming vector this project does not close:
doing so would require either a per-obligation cooldown or an escrowed
per-attempt fee, both of which add real new state and failure modes
(who's exempt from the cooldown during a genuine emergency? who receives
a forfeited fee?) that this MVP deliberately has not taken on. Flagged
here so it's a known, named trade-off rather than something a reviewer
discovers and assumes was missed.

### 28.7 [DOCUMENTED, not fixed] Evidence truncation can manufacture false
negatives on legitimately large documents

`MAX_EVIDENCE_CHARS_PER_SOURCE = 4000` truncates each fetched source
before it reaches the prompt. A genuinely valid, complete certificate
embedded in a long document could have its relevant section truncated
away, producing `UNDETERMINED`/`REJECTED` not because evidence is
insufficient but because this contract cut it off first. This is a
real precision/recall trade-off (unbounded evidence size is itself a cost
and prompt-injection surface — see §5), not a bug, but worth stating
plainly rather than leaving implicit: a real deployment's evidence
sources should be structured/pre-extracted (e.g., point `evidence_refs`
at a short, purpose-built attestation document rather than a 50-page
contract PDF) rather than relying on this contract to find a needle in a
haystack within 4000 characters.

### 28.8 [DOCUMENTED, not fixed] Date/quantity comparison is 100%
delegated to LLM judgment, not partially deterministic

The single strongest "why do you need AI *here specifically*" attack a
sharp reviewer can make: comparing a submitted delivery date against a
stored `deadline_iso`, or a submitted quantity against a stated minimum,
is mechanically trivial once the relevant fact is *extracted* from
evidence — arithmetic and string/date comparison need no non-deterministic
judgment at all. The original brief's own two-stage design (§10:
extraction, then separate deterministic policy evaluation) anticipated
exactly this critique. This project's MVP deliberately collapses both
stages into one LLM call for implementation simplicity (stated in
`semantic_obligation_gate.py`'s module docstring), which is a legitimate
simplification for a first proof but is also the most likely specific
line of attack: "you're spending consensus-and-LLM-cost on a date
comparison a `datetime` import could do for free, and that undermines
your necessity argument for at least those two criteria." The honest
answer is that this is a known, named simplification, not an oversight —
restructuring to a real two-stage extract-then-deterministically-compare
pipeline (extraction stays non-deterministic/LLM-driven and
consensus-checked the same way; the deadline/quantity *comparison* itself
moves into the deterministic section after consensus, operating on the
already-agreed extracted facts) is the concrete next step if this
critique needs to be pre-empted rather than defended against.

### 28.9 [DOCUMENTED, not fixed] `reason_code` is untrusted LLM-generated
text reaching on-chain storage and any future UI

`reason_code` is validated only for "non-empty string, ≤64 chars" — its
actual content is whatever the LLM chose to write, constrained only by
the prompt's instruction to use "UPPER_SNAKE_CASE". Nothing stops a
successfully-consensus-reaching `reason_code` from containing unexpected
characters (the schema doesn't restrict to `[A-Z_]`). This is not a
contract-level vulnerability — it's plain `str` storage, no code
execution risk here — but any frontend that renders `reason_code`
verbatim (e.g., directly into HTML) must treat it as untrusted user
content and escape it accordingly, same as any other on-chain string
originating from an adversarial input path. Worth stating explicitly as
integration guidance rather than assuming it's obvious.

### 28.10 Tally after this adversarial self-review pass

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 18 (+3 this pass) | 18 passed |
| `test_process_graph_router.py` | 21 | 21 passed |
| `test_certification_gate.py` | 6 | 6 passed |
| **Total** | **45** | **45 passed** |

The three new tests
(`test_validator_disagrees_when_evidence_content_changes`,
`test_invalid_deadline_iso_rejected`, `test_oversized_policy_rejected`)
directly correspond to §28.1/§28.3/§28.4's closed findings. §28.2,
§28.6, §28.7, §28.8, §28.9 remain open by deliberate choice and are
listed here precisely so they don't need to be rediscovered by whoever
reviews this next.

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
12. Run adversarial tests                             <- partial: schema/structural adversarial cases pass; live prompt-injection needs Studio (§9.3)
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

Because of §23.4, "38/38 tests pass" is an honest but *partial* proof: it
fully covers PROOF-1 through PROOF-4 for `SemanticObligationGate` (the
whole point of Milestone 1) and fully covers the Router's and
CertificationGate's deterministic logic in isolation, but it does **not**
cover any contract's actual reason for depending on another — safely
composing Gate instances via the Router, or the Router's completion signal
via CertificationGate — end to end. That composition was reasoned through
carefully (§11–§21, §24–§27) and the code was written to the same
standard throughout, but "reasoned through" and "executed" are different
claims, and this project's own rule (§37 of the original brief: "source
code alone is not proof of live behavior") applies here too. The next
concrete step, before this can be called proven the way the Gate alone
now can, is exactly what §22's limitations already said: a glsim or
Studio/localnet run that actually exercises the cross-contract calls —
`deploy/STUDIO_TESTING_GUIDE.md` is the manual sequence for that.

### 23.6 Direct Mode's cross-contract call to a non-existent contract
returns `None`, not an exception

Discovered while testing `CertificationGate.claim_eligibility()` against a
`router_address` with nothing actually deployed there (the same
one-contract-per-process constraint as §23.4 makes this unavoidable for
any single-file test in this repo). The call did not raise — Direct
Mode's `gl_call` handler traces `"Unknown gl_call request type:
['CallContract']"` and the SDK-level cross-contract proxy call resolves to
`None` rather than throwing. `_read_router_status`'s `try/except` was
written expecting a possible exception (a reasonable assumption for a
*real*, live misconfigured/unreachable router on an actual network, where
a cross-contract failure more plausibly does raise); Direct Mode's
specific failure shape turned out to be different, and the original test
assertion (`test_claim_fails_cleanly_when_router_unreachable`, expecting
the `except` branch's message) failed against real execution and had to
be corrected to what the run actually showed
(`test_claim_fails_safely_when_router_unreachable`, expecting the
downstream `status != "COMPLETE"` check to catch it instead). The contract
code itself needed no change — it was already safe against this case by
construction (§27) — only the test's assumption about *which* line would
catch it was wrong, and got fixed to match reality rather than adjusted to
force the original assumption to pass.

### 23.7 Full tally before the live-Studio bug (below) was found and fixed

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
which is exactly what surfaced §23.8 below.

### 23.8 Real bug found via live Studio deployment: Address-typed
arguments arrive as plain `int`, not `Address`

This is the first finding in this project that came from an actual live
GenLayer Studio deployment, not from Direct Mode or from reading docs --
reported back with a full traceback after following
`deploy/STUDIO_TESTING_GUIDE.md`'s deploy order.

**What happened:** deploying `process_graph_router.py` with a `gate_address`
typed into Studio's dedicated "Constructor Inputs" `address` field failed
with `AttributeError: 'int' object has no attribute 'as_bytes'`, deep
inside `genlayer/py/storage/_internal/desc_base_types.py`'s
`.set()` -- i.e. inside GenVM's own storage-assignment code, not this
contract's. The transaction's logged `args` showed the value as a bare
decimal integer (`425837230815689853718213131299007537744004162246`),
which decodes to exactly 20 bytes -- confirming Studio's UI had the
*correct* address, but its calldata serialization for that argument used
GenVM's generic integer type instead of the `Address` type, even though
the input field was labeled and typed as `address`.

**Why this is a Studio/calldata-serialization issue, not a user-input
mistake:** the same failure was reproduced with a properly quoted `0x...`
hex string in the dedicated address field (a later screenshot showed
Studio's UI does provide a real, separate `address`-typed input, distinct
from the numeric field next to it) -- and it still failed identically.
That rules out "wrong field" or "wrong format" as the cause; the
serialization gap sits between Studio's UI and the calldata it constructs,
outside this project's code.

**Fix:** since this project cannot change Studio's calldata encoding, the
fix lives in the contracts instead -- a `_coerce_address(val)` helper
added to all three contract files (duplicated per file, consistent with
each file being deployed as its own standalone GenVM module -- see §11 /
§23.4 on why one-contract-per-module rules out a shared import). It
accepts an already-correct `Address` unchanged, or coerces from `int`
(via `.to_bytes(Address.SIZE, "big")`), `bytes`/`bytearray`/`memoryview`,
or a hex-or-base64 string (the latter two already natively supported by
`genlayer.py.types.Address`'s real constructor, confirmed by reading it
directly). Applied everywhere an `Address` value is accepted from outside
the contract:
- `SemanticObligationGate.create_obligation`'s `supplier` parameter
- `ProcessGraphRouter.__init__`'s `gate_address` parameter
- `ProcessGraphRouter.register_authority`'s `authority_address` parameter
- `CertificationGate.__init__`'s `router_address` parameter

`gl.message.sender_address` (used for `buyer`, `admin`, `applicant`,
`claimed_by` throughout) needed no change -- that value is decoded
internally by GenVM's own message-context injection, not user-supplied
calldata, so it was never exposed to this bug.

**Regression tests added and passing** (Direct Mode, reproducing the exact
failure shape -- an `int` built from a real address's raw bytes,
big-endian, matching the shape of the failing Studio transaction's `args`):
`test_constructor_accepts_gate_address_as_plain_int`,
`test_register_authority_accepts_int_address`,
`test_create_obligation_accepts_supplier_as_plain_int`,
`test_constructor_accepts_router_address_as_plain_int`. All four pass
against the real GenVM SDK, confirming the fix works, not just that it
compiles.

### 23.9 Tally as of §23.8 (superseded — see §28's own tally below for the
current, post-adversarial-self-review total)

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 15 | 15 passed |
| `test_process_graph_router.py` | 21 | 21 passed |
| `test_certification_gate.py` | 6 | 6 passed |
| **Total** | **42** | **42 passed** |

Cross-contract calls between the three contracts (Router→Gate,
CertificationGate→Router) still remain unverified by any test run in
this repository and still require `deploy/STUDIO_TESTING_GUIDE.md`'s
manual sequence (or a glsim/Integration Testing harness) to close -- §23.8
is a real bug found via a *partial* live deployment attempt (a
constructor call failing before any cross-contract interaction was even
reached), not a substitute for that full sequence. The next Studio
attempt should get further now that this specific failure is fixed, and
may well surface the next thing Direct Mode couldn't predict.
