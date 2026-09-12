# Resubmission results — E010 lint fix

## What was wrong

`genvm-lint check contracts/semantic_obligation_gate.py --json` on the
originally-submitted file:

```
E010: gl.nondet.* call in 'SemanticObligationGate.adjudicate.<locals>._fetch_evidence_text' not reachable from equivalence principle block  (line 590)
E010: gl.nondet.* call in 'SemanticObligationGate.adjudicate.<locals>._run_adjudication' not reachable from equivalence principle block      (line 607)
```

`gl.nondet.web.request` and `gl.nondet.exec_prompt` lived in helper
functions (`_fetch_evidence_text`, `_run_adjudication`) that `leader_fn`/
`validator_fn` merely called. genvm-lint's static check does not walk the
call graph — it only looks inside the function bodies passed directly to
`gl.vm.run_nondet_unsafe`, so the nested calls were invisible to it, even
though at runtime they only ever executed inside that block.

## The fix

Inlined the fetch-and-adjudicate logic directly into the bodies of both
`leader_fn` and `validator_fn` (duplicated on purpose, no shared helper),
so every `gl.nondet.*` call is textually inside the function the linter
inspects.

```
genvm-lint check contracts/semantic_obligation_gate.py --json
{"ok":true,"lint":{"ok":true,"passed":3},"validate":{"ok":true,"contract":"SemanticObligationGate","methods":7,"view_methods":4,"write_methods":3,"ctor_params":0}}
```

## Follow-up fix: fetch-failure determinism

Studio testing (see below) surfaced a real, separate issue: when
`gl.nondet.web.request` fails, only `decision` was previously forced to
`UNDETERMINED` — the four booleans (`quantity_match`, `specification_match`,
`deadline_match`, `critical_exception`) were left to whatever each model
individually inferred from the (non-deterministic-wording) fetch exception
text, causing spurious validator `Disagree`. Fixed: all five fields are now
hardcoded when `fetch_failed=True`, in both `leader_fn` and `validator_fn`.

## Live Studio test results (real testnet transactions)

All three contracts (`SemanticObligationGate`, `ProcessGraphRouter`,
`CertificationGate`) deployed fresh with the fixed `semantic_obligation_gate.py`.

### Gate-only, fetch-failure path (before fix)

| Step | Tx hash | Result |
|---|---|---|
| `create_obligation("test-fetch-fail", ...)` | `0xe7e5bf36a283cec44d51ff1fe8728c116e8d2b0cf9b6d105e2ab8bd3bed766bd` | SUCCESS |
| `submit_evidence` (unreachable `.invalid` URL) | `0x43eab3975d98d840629107c15738af03681fa0e3cfc54aa0b78f89cf1b6de6e6` | SUCCESS |
| `adjudicate` | `0xcbc74389a0e71fe0cff35d8feed677293d82a4b9602537ab78384e41205a813e` | SUCCESS → `UNDETERMINED`/`EVIDENCE_FETCH_FAILED`, but 1 validator `Disagree` (the bug the follow-up fix closes) |

### Gate-only, fetch-failure path (after follow-up fix)

| Step | Tx hash | Result |
|---|---|---|
| `create_obligation("test-fetch-fail-2", ...)` | `0xa633f9af9c99f643c6f2438655037ee9df8d219bdb7608a18643eb738e2530d2` | SUCCESS |
| `submit_evidence` | `0x4ad9977e702f270fc64535abf449d38130f53a6b508e64559af62629224e4540` | SUCCESS |
| `adjudicate` | `0xd6dde153713c1ff8508f08601ec81e34e92aa776fe51227cbab59be5ab30079b` | SUCCESS → `UNDETERMINED`/`EVIDENCE_FETCH_FAILED`, **0 Disagree**, all active validators `Agree` |

### Gate-only, successful-fetch path

| Step | Tx hash | Result |
|---|---|---|
| `create_obligation("test-approve-1", ...)` | `0x9d162dac7267d3c09cc25fe61bcf00259819015f12017f7b700c83f21d835b5e` | SUCCESS |
| `submit_evidence` (real README, "Hello World") | `0xf6787bf0791030394d78174cb64a2296867446ee5de33b597e5f78d59ad53abe` | SUCCESS |
| `adjudicate` | `0xf573d4964252e75c73f695ae24dbff557327e17abe0bbf3a44e679b5bdc8606d` | SUCCESS → `APPROVED`/`EXACT_PHRASE_PRESENT`, all `Agree` |
| repeated on new deployment (`test-approve-2`) | create `0x22da4bb3ea4abf465ba087867659117623a1d4be0b2ddf20d53aa5511298c4d7`, submit `0x8afa96bee1bc42b30968e32ceb6c5070b0090173db30e03211fa56983567776b`, adjudicate `0x16e35cd9ddb6f6b46721fbd2c8dbf44944d13ca743896dcf48973e5a7ae402f8` | all SUCCESS → `APPROVED`, all `Agree` |

### Full 3-contract chain ("Administratum" shape)

`ProcessGraphRouter` deployed at `0x3C757f636D13698C2226a2Ae6C66fF7aa6e2D496`
(pointed at the fixed Gate), `CertificationGate` deployed at
`0x3f08065b7401d29615d28f8da85116f4eef00dd5a530256f3cbd9c30f0e8d42a`.

| Step | Result |
|---|---|
| `register_authority` ×3 (fire_safety, sanitary, final_review) | SUCCESS |
| `create_obligation` ×3, `bind_obligation_stage_type` ×3 | SUCCESS |
| `register_process("permit-1-v2", ...)` — **live cross-contract read of Gate `buyer`, untestable in Direct Mode** | SUCCESS |
| `get_unblocked_stages` before adjudication | `["fire_safety","sanitary"]` — correct |
| adjudicate both independent stages | both `APPROVED` |
| `get_unblocked_stages` after | `["final_review"]` — correctly unblocked |
| adjudicate `final_review` | `APPROVED` |
| `refresh_process_status` | `get_process_status` → `"COMPLETE"` |
| `claim_eligibility` + `is_eligible` (2nd cross-contract hop, into CertificationGate) | `true` |

## Separate finding — not a security issue, not fixed (by design)

On genuinely ambiguous/sparse evidence content (mismatched against
policy, but not a fetch failure), different LLM validators can legitimately
disagree on individual semantic fields (`quantity_match` etc.) even while
agreeing on the overall `decision`, occasionally triggering leader rotation
or a rare VM-level `Undetermined` transaction (storage untouched, safe to
retry). Observed live on `permit-1:sanitary` with a one-line evidence
document; resolved on retry with a longer, more specific evidence body.
This is inherent to comparing full multi-model semantic verdicts, not a
contract defect — noted here for anyone writing policies for this contract.
