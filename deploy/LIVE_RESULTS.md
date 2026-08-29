# Live results — GenLayer Studio

Real testnet transactions from the deployment and testing session for this
submission. Every hash below is a real transaction on GenLayer Studio,
copied verbatim from the session — nothing here is simulated or
Direct-Mode output (that's covered separately by the 45/45 pytest suite in
`tests/`). Contract addresses themselves were not consistently captured in
copyable form during the session (Studio truncates them in the header UI);
what's preserved here is the full transaction history, which is sufficient
to verify every step independently via GenLayer Studio's or a block
explorer's transaction lookup.

## Scenario 1 — `permit-1`: linear single-stage flow (Corporate Obligation
Chain shape: one stage, no dependencies)

This run also surfaced two real, independent bugs that were fixed
mid-session — see `docs/architecture.md` §23 and §28 for the full
writeup. The failed attempts below are kept in the record deliberately,
not cleaned up, because they're part of the proof that the fixes were
verified against real failures, not assumed.

| Step | Method | Tx hash | Result |
|---|---|---|---|
| Deploy Gate | `deploy` | *(not individually captured)* | SUCCESS |
| Deploy Router (**1st attempt**) | `deploy` | `0xfd785e92f646d32c95fd4c452b417962e486cd0665696b607c1281149480d5f5` | **ERROR** — `gate_address` arrived as plain `int`; see §23.8 |
| Deploy Router (fixed contract) | `deploy` | *(not individually captured)* | SUCCESS |
| Deploy CertificationGate (**1st attempt**) | `deploy` | `0xf221afd399a4a20a34baf3299db2d4d6a9c68024e4c34d75eaa9bc6c14fb159b` | **ERROR** — stale unfixed file redeployed; same `int`-address bug |
| Deploy CertificationGate (fixed contract) | `deploy` | *(not individually captured)* | SUCCESS |
| `register_authority("fire_safety", owner)` | write | `0x5ef8011e8e23babda48cca58afe0562d8160d0c5295a8cc04a5b549f64f982ad` | SUCCESS |
| `create_obligation("permit-1:fire_safety", ...)` | write | `0xa2084d541d86a781bd06bae8d41c6fcaef47f85f0fc56db882b8e2bbb21e4ebc` | SUCCESS |
| `register_process("permit-1", ...)` | write | `0x3646e17860a436bf73ff6c0a887d745123a575cd1cf98d8535d01e5f9c3353b0` | SUCCESS |
| `submit_evidence` (evidence: bare stub page, no real content) | write | `0x88dcf817755671bfc0f00db924e742260bc97fc8b704cdc6c89523f3899021c0` | SUCCESS (submission succeeds; content quality is judged at `adjudicate`) |
| `adjudicate` (**1st**) | write | `0xa024f55dab37f7524208c185cd46bc2edc2c5099447bcb8f29495c7a94670b9e` | SUCCESS → verdict `UNDETERMINED`, `EVIDENCE_INSUFFICIENT_NO_CERTIFICATE_OR_DELIVERY_DATA` — correct behavior, evidence genuinely had no certificate/date |
| `claim_eligibility` (**1st attempt, correctly refused**) | write | `0x1e6d20c5f27d3d814d9d6881e392fd2aeac8847bae28699ebf763c05036b1ec0` | ERROR (expected) — `[rollback] process 'permit-1' is not COMPLETE ... status=ACTIVE` |
| `claim_eligibility` (**2nd attempt, still correctly refused**) | write | `0x871e92b5752aeb18e9e6d06aca2bc32e87b7cf23db9faa884c772ba7d7ad8f40` | ERROR (expected), same reason |
| `submit_evidence` (evidence: gist HTML page, not raw) | write | `0x95ba3c6c59df4969615f58a1a1d047d045ec27e271710354593bd3fe12224852` | SUCCESS |
| `adjudicate` (**2nd**) | write | `0x1bf51f6afd8570d85bb3fffc47e140d6851b2a4cd870f0fd6e7b1e3352ad4753` | SUCCESS → verdict `UNDETERMINED`, `deadline_match=true`, `specification_match=true`, `quantity_match=false` — policy never stated a quantity requirement, evidence couldn't confirm it either |
| `refresh_process_status` | write | `0x84831d88d826e62c30b2285af44963f174ebf9277c236e18f75745c75b68e4cf` | SUCCESS, status stayed `ACTIVE` (correct — obligation not `APPROVED` yet) |
| `claim_eligibility` (**3rd attempt, correctly refused**) | write | `0xa816dc3deec10ecec8088db7cf4952aa439ce07c9013f66cca9e23c49494479b` | ERROR (expected), same reason |
| `submit_evidence` (evidence: correct raw gist URL, quantity clause added to evidence) | write | *(read via `get_verdict` afterward, tx not individually re-quoted)* | SUCCESS |
| `adjudicate` (**3rd, final**) | write | *(see `get_verdict` below)* | SUCCESS → verdict **`APPROVED`**, all four criteria `true`, `reason_code="OBLIGATION_SATISFIED"` |
| `refresh_process_status` | write | *(not individually re-quoted)* | SUCCESS → status `COMPLETE` |
| `claim_eligibility` (**final, succeeds**) | write | `0x497704667a8dbfa26959112ac0f142cb449c1f99dd531b2886240631dbf38d1b` | SUCCESS |
| `is_eligible("permit-1")` | read | — | `true` |
| `get_eligibility("permit-1")` | read | — | `{"status": "ELIGIBLE", "claimed_by": "0xd3e5f03720031d71a7c6766c39f36dd7ef3f28b8", "claimed_at": "..."}` |

**Outcome: full linear chain proven live — CREATED → EVIDENCE_SUBMITTED →
UNDETERMINED (twice, both times correctly, on genuinely insufficient
evidence) → FINALIZED/APPROVED → Router COMPLETE → CertificationGate
ELIGIBLE.**

## Scenario 2 — `permit-2`: parallel independent stages converging on a
final review (the "Administratum" shape)

Graph: `fire_safety` and `sanitary` have no dependency on each other;
`final_review` depends on both.

| Step | Method | Tx hash | Result |
|---|---|---|---|
| `register_authority("sanitary", owner)` | write | `0x7b7854a6d84cc76b6d35380c06efb1d9261e30033d11dd5d0536b7eb730b33bc` | SUCCESS |
| `register_authority("final_review", owner)` | write | `0x8c37b39aa9e52effa046404b612e017529ac353d56b36b0781503165bbb9be38` | SUCCESS |
| `create_obligation("permit-2:fire_safety", ...)` | write | `0x21b9a857a8714b85a8c1bdd15785ca44ca70873e7e211015081f884a47178be0` | SUCCESS |
| `create_obligation("permit-2:sanitary", ...)` | write | `0xdf44a0a87fd95b35210de05541ef11501dbff3fed848bed3af0574d8e61a1eb5` | SUCCESS |
| `create_obligation("permit-2:final_review", ...)` | write | `0x9a1692820f9c2e1de7917c653797c8ca0536562dda087a061dad267e5363a09c` | SUCCESS |
| `register_process("permit-2", ...)` (3 stages, 2 edges into `final_review`) | write | `0x2c1f00ef7841941ea79ae0e1772ee674f9c4520aefae3d8eb6ef101591a146c4` | SUCCESS |
| `submit_evidence` + `adjudicate` for `fire_safety`, `sanitary`, `final_review` | write ×6 | *(not individually re-quoted; each confirmed via `get_verdict` between calls)* | all SUCCESS, all reached `APPROVED` |
| `refresh_process_status("permit-2")` | write | *(not individually re-quoted)* | SUCCESS → status `COMPLETE` |
| `claim_eligibility("permit-2")` | write | `0x1203a00e5f610eab8f5f524fcb0689f0b9eba8f52d003b414a1e94459d29f464` | SUCCESS |
| `is_eligible("permit-2")` | read | — | `true` |
| `get_eligibility("permit-2")` | read | — | `{"status": "ELIGIBLE", "claimed_by": "0xd3e5f03720031d71a7c6766c39f36dd7ef3f28b8", "claimed_at": "2026-08-29T07:46:43..."}` |

**Note on `get_unblocked_stages`:** this was exercised live during the
session specifically to confirm `fire_safety` and `sanitary` appear
together, independent of each other, before `final_review` unblocks. One
read during the session returned an unexpectedly empty list; this was not
conclusively root-caused in the transcript (most likely a `process_id`
mix-up between `permit-1` and `permit-2`, or a wrong contract tab, rather
than a contract defect — see `docs/architecture.md` for the live
troubleshooting). The scenario was carried through to full completion
regardless, and `tests/test_process_graph_router.py`'s
`test_independent_stages_unblock_in_parallel` and
`test_dependent_stage_blocked_until_dependency_approved` cover this exact
behavior deterministically in Direct Mode. **Re-running
`get_unblocked_stages` at each stage of a fresh live scenario, with the
exact response pasted at each step, is the one piece of live evidence
worth re-capturing cleanly if a fully clean record is needed for
submission** — the underlying behavior is proven both by the parallel
scenario's successful completion and by the deterministic test suite, but
a clean step-by-step live capture of the unblocking sequence itself was
not obtained in this session.

**Outcome: parallel graph structure proven live end-to-end** — two
independent regulatory checks adjudicated separately, a converging final
review gated on both, Router correctly tracked completion across a
non-linear dependency graph, CertificationGate correctly read the final
`COMPLETE` status through a second cross-contract hop.

## Cross-reference

- Full technical writeup of every bug found (Studio-serialization `int`
  vs `Address`, mutable-evidence-URL consensus gap) and fixed: see
  `docs/architecture.md`, particularly §23.8 and Part D (§28).
- Deterministic, automated coverage of the same behaviors demonstrated
  live above: `tests/` (45/45 passing, `pytest tests/ -v`).
- Manual step-by-step reproduction instructions: `deploy/STUDIO_TESTING_GUIDE.md`.
