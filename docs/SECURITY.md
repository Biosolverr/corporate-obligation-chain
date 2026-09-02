# Security — threat model, known limitations, and audit history

This file is the single place for everything security-related across all
three contracts: threat model coverage, known limitations (open and
closed), the adversarial self-review conducted after live Studio testing,
and the findings from an independent external audit. `docs/architecture.md`
covers design/architecture and points here for anything security-specific
rather than duplicating it.

**Status: 56/56 tests passing** (`pytest tests/ -v`), including regression
tests for every closed finding below. See `deploy/LIVE_RESULTS.md` for live
Studio confirmation of several of these (notably the anti-replay fix,
findings #4/#5).

---

## 1. SemanticObligationGate — threat model coverage

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


## 2. SemanticObligationGate — known limitations

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


## 3. ProcessGraphRouter — threat model additions

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


## 4. ProcessGraphRouter — known limitations

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
   call. See `docs/architecture.md` §23.4 for exactly why and what remains unverified (the
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


## 5. CertificationGate — a real, positive fail-safe finding

Direct Mode's cross-contract `.view()` call to an address with nothing
deployed there does not raise an exception — it silently returns `None`
(confirmed this session; see §6 above). `claim_eligibility` was NOT written
expecting that specific behavior (it was written expecting a possible
exception, hence the `try/except` in `_read_router_status`), but it fails
safe anyway: `None != "COMPLETE"` naturally refuses eligibility rather
than granting it. This is worth calling out because it's exactly the kind
of defense-in-depth that matters when an assumption about a dependency's
failure mode turns out to be wrong — the code didn't need to correctly
predict Direct Mode's specific behavior to stay safe against it.


## 6. Direct Mode cross-contract call quirk with security implications

Discovered while testing `CertificationGate.claim_eligibility()` against a
`router_address` with nothing actually deployed there (the same
one-contract-per-process constraint as `docs/architecture.md` §23.4 makes this unavoidable for
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
construction (§5 above) — only the test's assumption about *which* line would
catch it was wrong, and got fixed to match reality rather than adjusted to
force the original assumption to pass.


## 7. Real vulnerability class found via live deployment: Address/int type confusion

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
each file being deployed as its own standalone GenVM module -- see
`docs/architecture.md` §11 / §23.4 on why one-contract-per-module rules
out a shared import). It
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


## 8. Adversarial self-review (post-live-testing pass)

This section exists because live Studio testing surfaced one real,
serious gap (§8.1) that no amount of Direct Mode testing or docs-reading
would have found — Direct Mode's evidence is always byte-identical
between a mocked leader and a mocked validator by construction, so the
whole class of "what if the evidence itself changes between fetches" bugs
is invisible to it. After finding it live, this section is a deliberate,
structured second pass over the whole three-contract chain asking "what
would a hostile reviewer find next" — closing what's cheaply closable now,
and stating plainly what isn't, rather than waiting for a review to find
it.

### 8.1 [CLOSED] Mutable evidence URLs allowed leader/validator to see
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
threat model's "Evidence manipulation" entry (§1 above) than the original text
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
*collusion* in what's consistently served. See §8.2.

### 8.2 [DOCUMENTED, not fixed] Mutable evidence URLs are the wrong kind
of evidence source for production

Directly informed by living through it: a GitHub Gist's default "raw"
link is versioned by content hash in the URL path, but the *unversioned*
raw link (`.../raw/<filename>`, no revision hash) always resolves to
whatever the latest edit is — which is exactly what let us "fix" our test
obligation's evidence after the fact by editing the same gist. That
convenience is a liability in production: any evidence source where the
same URL can legitimately mean different things at different times is a
weaker integrity guarantee than the rest of this system provides
everywhere else. §8.1's hash comparison protects consensus *within one
adjudication attempt*; it does nothing to stop a submitter from
legitimately editing evidence between one `adjudicate()` call and the
next re-adjudication (allowed by design, for the `UNDETERMINED` "give
better evidence" case) — which is fine for evidence maturing toward
completeness, but indistinguishable from evidence being changed to game
an outcome. **Recommendation for any real deployment: require
content-addressed evidence** (an IPFS CID, or a git-commit-pinned/
revision-pinned URL) instead of an arbitrary mutable URL, so that
`evidence_ref` itself is the commitment, not just `evidence_hash` (which,
per §8.1's design, is caller-declared and never independently verified
by the contract against the initial submission — only cross-checked
against what was fetched *at adjudication time*, via
`resolved_evidence_hash`). This project does not enforce this (accepting
any URL is simpler for an MVP/hackathon submission and matches the
original brief's "off-chain, reference + hash" evidence model), but it is
the single most important operational guidance for anyone deploying this
for real.

### 8.3 [CLOSED] No cap on `policy` length

**Finding.** `create_obligation` validated `deadline_iso` and
`obligation_id` for non-emptiness but placed no upper bound on `policy`
length — a careless or malicious buyer could submit an arbitrarily large
policy string, inflating storage cost and the size of every future
adjudication prompt indefinitely.
**Fix.** `MAX_POLICY_CHARS = 4000` (matching `MAX_EVIDENCE_CHARS_PER_SOURCE`'s
order of magnitude), enforced in `create_obligation`.
**Verified.** `test_oversized_policy_rejected` passes.

### 8.4 [CLOSED] `deadline_iso` was never validated as a real date

**Finding.** Any non-empty string was accepted as `deadline_iso`,
including garbage like `"asap"` — silently weakening `deadline_match`'s
reliability, since the LLM would have to interpret an unparseable
deadline however it saw fit, with no contract-level guarantee it was ever
a real date to begin with.
**Fix.** `create_obligation` now calls `datetime.fromisoformat(deadline_iso)`
and reverts with a clear message if it doesn't parse.
**Verified.** `test_invalid_deadline_iso_rejected` passes.

### 8.5 [CONFIRMED, no code change needed] `gate_address` and
`router_address` are already immutable post-deployment

A reviewer's natural next question after `docs/architecture.md` §17's admin-centralization
discussion is "what if the admin points the Router at a different, fake
Gate?" — checked this explicitly: `self.gate_address` in
`ProcessGraphRouter` and `self.router_address` in `CertificationGate` are
each assigned exactly once, in `__init__`, and grepping both files
confirms no method — including `register_authority`, `renounce_admin`,
or `freeze_upgrades` — ever reassigns either field. There is no
"swap the upstream contract" attack surface here by construction, not by
policy; worth stating explicitly since it preempts a plausible-sounding
attack that isn't actually possible.

### 8.6 [DOCUMENTED, not fixed] `adjudicate()` / `refresh_process_status()`
retry cost has no rate limit

Both are intentionally permissionless (`docs/architecture.md` §7, §25) for liveness reasons
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

### 8.7 [DOCUMENTED, not fixed] Evidence truncation can manufacture false
negatives on legitimately large documents

`MAX_EVIDENCE_CHARS_PER_SOURCE = 4000` truncates each fetched source
before it reaches the prompt. A genuinely valid, complete certificate
embedded in a long document could have its relevant section truncated
away, producing `UNDETERMINED`/`REJECTED` not because evidence is
insufficient but because this contract cut it off first. This is a
real precision/recall trade-off (unbounded evidence size is itself a cost
and prompt-injection surface — see `docs/architecture.md` §5), not a bug, but worth stating
plainly rather than leaving implicit: a real deployment's evidence
sources should be structured/pre-extracted (e.g., point `evidence_refs`
at a short, purpose-built attestation document rather than a 50-page
contract PDF) rather than relying on this contract to find a needle in a
haystack within 4000 characters.

### 8.8 [DOCUMENTED, not fixed] Date/quantity comparison is 100%
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

### 8.9 [DOCUMENTED, not fixed] `reason_code` is untrusted LLM-generated
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

### 8.10 Tally after this adversarial self-review pass

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 18 (+3 this pass) | 18 passed |
| `test_process_graph_router.py` | 21 | 21 passed |
| `test_certification_gate.py` | 6 | 6 passed |
| **Total** | **45** | **45 passed** |

The three new tests
(`test_validator_disagrees_when_evidence_content_changes`,
`test_invalid_deadline_iso_rejected`, `test_oversized_policy_rejected`)
directly correspond to §8.1/§8.3/§8.4's closed findings. §8.2,
§8.6, §8.7, §8.8, §8.9 remain open by deliberate choice and are
listed here precisely so they don't need to be rediscovered by whoever
reviews this next.


## 9. External audit findings (independent review)

An independent adversarial review of this repository (not written by the
same process that wrote the contracts) found ten issues, numbered #1-#10
below exactly as delivered, ranked 🔴 critical / 🟠 high / 🟡 architectural.
Nearly all were real and correctly severity-rated; this section records
what was fixed, what was deliberately not fixed and why, and one case
where the reviewer's suggested framing, if implemented literally, would
have made the contract worse — walked through in detail because "the
external reviewer was right" isn't automatically true just because a
finding sounds plausible, and getting that distinction right matters as
much as fixing real bugs.

### #1 [PARTIALLY ADDRESSED — see honest limitation below] `evidence_hash`
does not commit to the submitted artifact

**The finding, verbatim and correct:** `submit_evidence()` stores
`evidence_hash` as a bare declaration; `adjudicate()` fetches evidence
fresh and computes its own hash, but nothing ever compared the two. A
submitter could commit to content A, have the URL change to content B
before adjudication, and — as long as leader and validators all
consistently see B — consensus would approve based on B while the
on-chain record implies A was reviewed.

**What was tried, and reverted:** the first fix attempt compared
`obligation.evidence_hash` against the internally-computed
`_evidence_content_hash` (the hash of the exact, truncated,
`"--- SOURCE: ..."`-decorated text handed to the LLM) and forced
`UNDETERMINED` on any mismatch. This was caught before being finalized:
**no realistic submitter can predict this contract's internal
prompt-formatting exactly**, so this comparison would mismatch on
essentially every normal submission (confirmed by realizing it would have
broken every existing test and every live Studio submission in this
project's own testing history, which all used arbitrary placeholder
hashes like `"hash-1"`). Shipping that "fix" would have made the contract
functionally unusable while providing no real security benefit against a
submitter who simply doesn't bother computing a real hash (the common
case) — it would only have inconvenienced honest users, not stopped a
adversarial one who could trivially compute a matching hash if the format
is public.

**What was actually done:** `submit_evidence()`'s docstring now states
plainly that `evidence_hash` is submitter-declared, unverified metadata,
not an enforced commitment — and explains exactly why (see above).
`resolved_evidence_hash` (already existing) remains as an audit-only
field: what consensus actually agreed was fetched, available for
off-chain comparison against expectations, but not automated.

**Honest residual gap, stated plainly (matches the reviewer's core point):
there is no on-chain-enforced binding between what a submitter commits to
at `submit_evidence()` time and what gets adjudicated later.** The
only real fix is architectural, not a comparison bolted onto URL-based
evidence: point `evidence_refs` at content-addressed storage (an IPFS URI
whose CID already *is* the content hash), so the reference itself is the
commitment and drift is impossible by construction rather than
detected-and-penalized after the fact. This was already this project's
recommendation for production use (§8.2 above) before this audit, and
remains the answer — this finding sharpened *why* a hash-comparison
patch doesn't substitute for it, which is a genuinely useful correction to
this project's own earlier reasoning.

### #2 [CLOSED] Retrieval failure was not fail-closed

**The finding, correct and CRITICAL-rated correctly:** a fetch exception
was converted to inert text (`"[EVIDENCE_FETCH_FAILED: ...]"`) and hoped
the LLM would follow the prompt's instruction to answer `UNDETERMINED`.
Nothing in code stopped a misbehaving or hallucinating model from
answering `APPROVED` anyway — `_is_valid_verdict` checked structure, not
"did retrieval actually succeed".

**Fix:** `_fetch_evidence_text` now returns `(text, had_failure: bool)`.
`_run_adjudication` computes this per call and, if `had_failure` is
`True` and the model said `APPROVED` anyway, **deterministically
overrides** the decision to `UNDETERMINED` with `reason_code =
"EVIDENCE_FETCH_FAILED"` — in code, unconditionally, not as a prompt
suggestion. `_fetch_failed` is also now a required field in
`_is_valid_verdict` and a compared field in `_verdicts_semantically_equal`,
so leader and validator must agree on whether retrieval succeeded, not
just on the resulting decision.

**Verified:** `test_fetch_failure_forces_undetermined_even_if_llm_says_approved`
deliberately mocks the LLM to answer `APPROVED` while leaving the
evidence URL entirely unmocked (Direct Mode raises `MockNotFoundError`,
standing in for a real network failure) — confirms the override fires and
`UNDETERMINED`/`EVIDENCE_FETCH_FAILED` is what actually gets recorded.

### #3 [CLOSED] Consensus on a self-contradictory verdict was accepted

**The finding, correct:** the prompt tells the model "APPROVED only if
all three match fields are true and there's no critical exception," but
`_is_valid_verdict` only checked field *types* — `{"decision": "APPROVED",
"quantity_match": false, ...}` passed structural validation. The
reviewer's framing of this is worth repeating verbatim because it's
exactly right: **"Consensus ≠ semantic validity."** Several nodes
agreeing on the same malformed-but-well-typed JSON is not the same as
several nodes agreeing on a JSON that satisfies the system's own rules.

**Fix:** `_is_valid_verdict` now also rejects `decision == APPROVED`
unless `quantity_match and specification_match and deadline_match and not
critical_exception` all hold. Since both leader and validator's own
outputs are run through this same function, an internally-inconsistent
`APPROVED` fails validity before it can even be compared for agreement.

**Verified:** `test_internally_inconsistent_approved_verdict_rejected`
mocks exactly this contradictory JSON and confirms the obligation lands
in `UNDETERMINED`/`CONSENSUS_INVALID_RESULT`, never `FINALIZED`.

**Broader point conceded:** the reviewer's synthesis (#10 below) that this
project needed "a deterministic invariant layer between consensus and
state transition" is correct, and findings #2 and #3 are exactly that
layer for the two invariants that mattered most. `_is_valid_verdict` was
already positioned as this layer's entry point before this audit; it just
wasn't doing enough checking.

### #4 / #5 [CLOSED] Router had no protection against obligation
replay/reuse

**The finding, correct and rated correctly as more serious than ordinary
spoofing:** `register_process`'s ownership check only asked "was this
obligation created by the right authority?" — never "is this obligation
already spoken for, by this process or any other?" The reviewer's
concrete attack (reusing a real, already-`APPROVED` `PermitA-FIRE`
obligation as the `fire_safety` stage of an entirely unrelated,
fabricated `Fake Permit B`) is exactly right and would have worked exactly
as described. The milder variant (#5: two stages in the *same* graph both
pointing at one obligation, turning one real adjudication into several
"independent" approvals) shares the same root cause.

**Fix:** a new Router-wide (not per-process) `claimed_obligation_ids`
list. `register_process` now rejects any stage whose `obligation_id` is
already claimed — by an earlier stage in *this* graph, or by any stage of
*any previously registered process* — before accepting it, and claims
each stage's obligation as it's accepted. An obligation can now only ever
back one stage, once, anywhere on this Router, permanently.

**Verified, with an honest scope split — now closed on both halves:** the
intra-graph half (#5) is fully testable in Direct Mode, because the check
was deliberately placed *before* any cross-contract call —
`test_intra_graph_obligation_reuse_rejected` confirms two stages in one
graph referencing the same `obligation_id` are rejected. The cross-process
half (#4) shares the exact same `claimed_obligation_ids` mechanism but
could only be exercised with a real second `register_process` call
against a real Gate deployment — the same one-contract-per-process Direct
Mode limitation documented in `docs/architecture.md` §23.4 applied here
too, so it was initially flagged as needing a live run rather than being
left silently unverified. **That live run has since happened:** on a
redeployed Gate/Router pair, `register_process("permit-4-fake", ...)`
attempted to reuse an obligation (`permit-4:fire_safety`) already bound to
a real, completed process (`permit-4`) — the exact attack described above
— and every validator correctly rejected it with
`[rollback] obligation 'permit-4:fire_safety' is already bound to another
stage or process`. See `deploy/LIVE_RESULTS.md` scenario 4 for the full
transaction record. Both #4 and #5 are now verified, not just
implemented — #5 in Direct Mode, #4 live on Studio.

### #6 [CLOSED] DAG size was bounded by stage count but not edge count

**The finding, correct:** `max_stages_per_process` bounded stages, but
edges had no cap and duplicates were accepted freely — a graph with few
stages but a very large or duplicate-heavy edge list was accepted,
inflating validation and storage cost.

**Fix:** `HARD_MAX_EDGES_CEILING = 64` enforced in `_validate_dag_payload`,
plus an explicit duplicate-`(stage_id, depends_on)`-pair rejection.

**Verified:** `test_too_many_edges_rejected` (65 edges between 4 stages)
and `test_duplicate_edge_rejected`.

### #7 [CLOSED] Several fields had no length bound

**The finding, correct:** `obligation_id`, `process_id`, each
`evidence_ref`, `evidence_hash`, and `graph_json`'s overall size were all
checked for non-emptiness but never for a maximum length — an
unbounded-size attack surface on storage and calldata cost.

**Fix:** `MAX_OBLIGATION_ID_CHARS`, `MAX_EVIDENCE_REF_CHARS`,
`MAX_EVIDENCE_HASH_CHARS` (Gate); `MAX_PROCESS_ID_CHARS`,
`MAX_OBLIGATION_ID_CHARS` (stage-level), `MAX_GRAPH_JSON_CHARS` (Router).

**Verified:** `test_oversized_obligation_id_rejected`,
`test_oversized_evidence_ref_rejected`,
`test_oversized_evidence_hash_rejected` (Gate);
`test_process_id_too_long_rejected`, `test_graph_json_too_large_rejected`,
`test_oversized_obligation_id_in_stage_rejected` (Router).

### #8 [ALREADY CORRECT, DOCS TIGHTENED] `CertificationGate.claim_eligibility`
has no sender restriction

**The finding:** anyone can call `claim_eligibility`, and `claimed_by` is
just whoever happened to call it — a vulnerability *if* a downstream
system ever treats `claimed_by` as an authorization/ownership field.

**Assessment:** this was already an explicit, documented design choice
(certification_gate.py's module docstring, "AUTHORITY MODEL" section),
consistent with the same permissionless-trigger pattern used by
`adjudicate()` and `refresh_process_status()` throughout this codebase —
the outcome (`ELIGIBLE` or not) is fully determined by already-consensus-backed
upstream state, not by who calls the method, so restricting the caller
adds a liveness risk without adding security. No code change was needed.
The reviewer's real point — the *semantics* of `claimed_by` must be
unambiguous to anyone integrating with this contract, since a wrong
assumption here is a real vulnerability in whatever *reads* this field —
was fair, so the docstring was reviewed again to make sure "informational
audit trail, never an authorization check" is stated as unmissably as
possible for a future integrator who doesn't read this file end to end.

### #9 [ALREADY CORRECT, verified by re-derivation] Router terminal-status
timing, checked against Gate re-adjudication

**The finding, framed as a question rather than a confirmed bug:** does
`UNDETERMINED -> new evidence -> APPROVED` ever risk the Router marking
`FAILED` prematurely?

**Re-derived from the actual code, not merely asserted:** `refresh_process_status`
only sets `FAILED` when a mandatory stage's Gate status is `FINALIZED`
*and* decision is `REJECTED`. `UNDETERMINED` never satisfies that
condition (it isn't `FINALIZED`), so a stage sitting in `UNDETERMINED`
keeps the whole process `ACTIVE`, never `FAILED`, for as long as it takes
to re-adjudicate it. And `FINALIZED`+`REJECTED` is itself terminal on the
Gate (`adjudicate()`'s own guard excludes `FINALIZED` from the set of
re-adjudicable statuses) — so there is no sequence in which a stage that
will *eventually* become `APPROVED` can first pass through a Gate-side
`FINALIZED`/`REJECTED` state that would trigger a premature Router
`FAILED`. This was correct before the audit; the audit prompted writing
down *why*, explicitly, rather than leaving it implicit.

### #10 Synthesis: the missing deterministic-invariant layer

The reviewer's closing diagram — evidence → LLM judgment → consensus →
`FINALIZED`, with no explicit "integrity check / invariant check" stage
in between — is an accurate description of what this file looked like
before this audit, and a fair generalization of findings #2 and #3
specifically. That layer now exists, concretely, as the combination of:
`_is_valid_verdict`'s logical-consistency check (#3), the
`_fetch_failed`-forces-`UNDETERMINED` override (#2), and
`ProcessGraphRouter`'s `claimed_obligation_ids` check (#4/#5) — three
separate, specific deterministic gates, not one generic "invariant
layer" module, because each closes a different failure mode with a
different mechanism. Finding #1 is the one place this synthesis doesn't
fully apply: no deterministic check inside this contract chain can
substitute for evidence sources that are content-addressed in the first
place (see #1's writeup) — that invariant has to live in the evidence
model itself, not in a check written after the fact.

### Tally after external audit fixes

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 23 (+5 this pass) | 23 passed |
| `test_process_graph_router.py` | 27 (+6 this pass) | 27 passed |
| `test_certification_gate.py` | 6 | 6 passed |
| **Total** | **56** | **56 passed** |

## 10. Post-independent-review findings (second independent review)

A second independent review found four additional issues beyond the
external audit in §9, all now closed. Recorded here in the same format,
for the same reason: so they don't need to be rediscovered.

### #11 [CLOSED, 🔴 critical] Evidence-fetch-failure hash broke the exact
availability guarantee it was meant to provide

**The finding.** `_evidence_content_hash` was computed from the same
`evidence_text` shown to the LLM, including, for a failed fetch, the raw
exception message (`f"[EVIDENCE_FETCH_FAILED: {exc}]"`). Exception text is
not guaranteed to be identical across independently-selected nodes even
when every node is reporting the exact same underlying failure (a source
that is genuinely, consistently unreachable). Since `_verdicts_semantically_equal`
requires this hash to match, leader and validator could disagree on it in
precisely the "source completely down" case -- the one case finding #2
(§9) was built to route safely to `UNDETERMINED`. The practical effect:
instead of reaching `UNDETERMINED`, `run_nondet_unsafe` could fail to
reach agreement at all, and the transaction simply would not commit --
directly contradicting `docs/architecture.md` §4's claim that fetch
failures are "converted into evidence content... rather than crashing the
transaction... This avoids a single flaky HTTP source turning into a
denial-of-service." Direct Mode cannot catch this by construction (same
blind spot documented in §8's opening paragraph: mocked leader/validator
fetches are always byte-identical).

**Fix.** `_fetch_evidence_text` now returns a separate, normalized
`hash_text` alongside the human-readable `prompt_text`: for any source
that failed to fetch, the hash input is a fixed marker
(`"[EVIDENCE_FETCH_FAILED]"`), never the raw exception text. Only the
`_fetch_failed` boolean (already a required, compared field) carries the
fact of failure into the equality check now -- the exact wording never
does.

**Verified.** `test_fetch_failure_hash_ignores_exception_wording`
(Direct Mode) confirms the override still fires and the same result is
reproduced on independent re-derivation.

### #12 [CLOSED, 🟠 high] `_is_valid_verdict` only checked APPROVED's
direction, not its converse

**The finding.** The consistency check added for finding #3 (§9) only
enforced "decision == APPROVED implies all criteria met". The converse
never held: `{"decision": "REJECTED", "quantity_match": true,
"specification_match": true, "deadline_match": true, "critical_exception":
false, ...}` is just as contradictory by this system's own decision rules
(`_build_prompt`'s "Decision rules" section says those conditions mean
APPROVED, nothing else) but passed structural validation. Since both
leader and validator run the same function, consensus could be reached on
that contradictory verdict, permanently closing an obligation as
REJECTED/UNDETERMINED even though every criterion the system itself checks
says APPROVED.

**Fix.** `_is_valid_verdict` now checks both directions: `all_criteria_met`
and `decision == APPROVED` must always agree.

**Verified.** `test_contradictory_rejected_verdict_is_invalid`.

### #13 [CLOSED, 🟠 high] Obligation was checked against the right
authority address, but not the right stage_type

**The finding.** `register_process`'s ownership check only asked "was this
obligation created by an address that is *an* authority for *some*
stage_type equal to `expected_authority`?" -- never "was this obligation
actually designated, by that authority, *for this stage_type*?" The Gate
has no concept of `stage_type` at all. If one address is registered as
authority for more than one `stage_type` -- which is not a contrived edge
case: it's exactly this project's own `deploy/STUDIO_TESTING_GUIDE.md`
reference scenario, using one `AUTHORITY` address for `fire_safety`,
`sanitary`, and `final_review` "for simplicity" -- an obligation created
with a `sanitary` policy would pass the address check for a `fire_safety`
stage just as well, on its first use. `claimed_obligation_ids` (§9,
finding #4/#5) only ever prevented *reuse*; it did nothing for a mismatch
on first use.

**Fix.** New `bind_obligation_stage_type(obligation_id, stage_type)` on
`ProcessGraphRouter`, callable only by the registered authority for
`stage_type`, who must also be the obligation's `buyer` on the Gate. It
records a permanent, one-time `obligation_id -> stage_type` binding.
`register_process` now requires this binding to exist and match before
accepting a stage, in addition to (not instead of) the pre-existing
address check.

**Verified.** `test_bind_obligation_stage_type_requires_registered_stage_type`,
`test_bind_obligation_stage_type_requires_correct_authority`,
`test_register_process_rejects_unbound_obligation` -- all fully testable
in Direct Mode since the new check runs before any cross-contract read.
`deploy/STUDIO_TESTING_GUIDE.md`'s reference scenario has been updated
with the required binding step (step 2a).

### #14 [CLOSED, 🟠 high] A REJECTED non-mandatory dependency could
deadlock a process forever

**The finding.** `refresh_process_status` only checked **mandatory**
stages for a `REJECTED` verdict when deciding whether to fail the whole
process. `get_unblocked_stages` requires ANY dependency -- mandatory or
not -- to be `FINALIZED`+`APPROVED` before something depending on it can
unblock. `REJECTED` is terminal on the Gate (no re-adjudication once
`FINALIZED`). Combined: a non-mandatory stage that some other (possibly
mandatory) stage depends on could be `REJECTED`, permanently blocking that
dependent stage from ever unblocking, while the process itself stayed
`ACTIVE` forever -- since the `REJECTED` stage was never itself mandatory.
Unlike the already-documented "no timeout for `UNDETERMINED`" limitation
(§4, known limitation #4), which always has a path forward (re-submit
evidence, re-adjudicate), this had none: `REJECTED` is terminal.

**Fix.** `refresh_process_status` now also treats a `REJECTED` stage as
fatal to the whole process whenever it appears as a `depends_on` target of
any edge in the graph, regardless of its own `mandatory` flag. A
non-mandatory stage nothing else depends on can still be `REJECTED`
without failing the process, exactly as before.

**Verified by re-derivation, not by execution** (same, already-documented
Direct Mode limitation as `refresh_process_status` itself, §4 known
limitation #4 / `docs/architecture.md` §23.4: this method needs a real
deployed Gate to exercise at all, cross-contract calls cannot be tested in
Direct Mode). No existing test in this repository ever passed
`mandatory: false` for any stage (confirmed by inspection), so this was
not previously covered, positively or negatively, by any test run.

### Tally after this review's fixes

| File | Tests | Result |
|---|---|---|
| `test_semantic_gate.py` | 23 (+2 this pass) | 25 total, all pass |
| `test_process_graph_router.py` | 27 (+3 this pass) | 30 total, all pass |
| `test_certification_gate.py` | 6 | 6 pass |
| **Total** | | **61 pass** |

`refresh_process_status`'s fix (finding #14) is verified by code
re-derivation only, consistent with this project's own precedent for
that method (§9, finding #9) -- a live/glsim run exercising a
non-mandatory-REJECTED-with-a-dependent scenario is the remaining open
item to fully close this the way §9's findings #4/#5 were eventually
closed live (`deploy/LIVE_RESULTS.md` scenario 4).
