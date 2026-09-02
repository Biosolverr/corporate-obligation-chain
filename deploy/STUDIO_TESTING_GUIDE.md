# Manual Studio testing guide

Everything below is untested by me beyond Direct Mode (see
`docs/architecture.md` §23). This is specifically the sequence that
exercises the parts Direct Mode structurally cannot: the real
cross-contract calls between all three contracts.

1. As `admin`, on the Router:
   `register_authority("fire_safety", AUTHORITY)`
   `register_authority("sanitary", AUTHORITY)`
   `register_authority("final_review", AUTHORITY)`
2. As `AUTHORITY`, on the Gate, create three obligations (the *authority*
   is `buyer` -- see architecture.md §12 role-reversal note, this is
   intentional):
   `create_obligation("permit-1:fire_safety", APPLICANT, "<policy text>", "<deadline iso>")`
   `create_obligation("permit-1:sanitary", APPLICANT, "<policy text>", "<deadline iso>")`
   `create_obligation("permit-1:final_review", APPLICANT, "<policy text>", "<deadline iso>")`
2a. **NEW, post-review, required:** as `AUTHORITY`, on the Router, bind
   each obligation to its stage_type before registering the process --
   this is a new requirement closing a gap where one `AUTHORITY` address
   registered for multiple `stage_type`s (exactly this scenario) could
   otherwise have an obligation meant for one stage_type silently accepted
   for another:
   `bind_obligation_stage_type("permit-1:fire_safety", "fire_safety")`
   `bind_obligation_stage_type("permit-1:sanitary", "sanitary")`
   `bind_obligation_stage_type("permit-1:final_review", "final_review")`
3. As anyone, on the Router:
   `register_process("permit-1", '{"stages":[...3 stages...],"edges":[{"stage_id":"final_review","depends_on":"fire_safety"},{"stage_id":"final_review","depends_on":"sanitary"}]}')`
   **This is the step that needs live cross-contract verification** --
   confirm it reverts if a stage's `obligation_id` was created by someone
   OTHER than the registered authority (the "spoofed obligation" test from
   §14, untestable in Direct Mode), AND confirm it reverts if step 2a was
   skipped for any stage (the "wrong stage_type, same authority" test,
   also untestable in Direct Mode).

## Reference scenario ("Administratum" -- two independent checks + one
converging final review)

Using three different Studio accounts: `AUTHORITY` (plays fire_safety AND
sanitary authority, and final_review authority, for simplicity), `APPLICANT`.

1. As `admin`, on the Router:
   `register_authority("fire_safety", AUTHORITY)`
   `register_authority("sanitary", AUTHORITY)`
   `register_authority("final_review", AUTHORITY)`
2. As `AUTHORITY`, on the Gate, create three obligations (the *authority*
   is `buyer` -- see architecture.md §12 role-reversal note, this is
   intentional):
   `create_obligation("permit-1:fire_safety", APPLICANT, "<policy text>", "<deadline iso>")`
   `create_obligation("permit-1:sanitary", APPLICANT, "<policy text>", "<deadline iso>")`
   `create_obligation("permit-1:final_review", APPLICANT, "<policy text>", "<deadline iso>")`
3. As anyone, on the Router:
   `register_process("permit-1", '{"stages":[...3 stages...],"edges":[{"stage_id":"final_review","depends_on":"fire_safety"},{"stage_id":"final_review","depends_on":"sanitary"}]}')`
   **This is the step that needs live cross-contract verification** --
   confirm it reverts if a stage's `obligation_id` was created by someone
   OTHER than the registered authority (the "spoofed obligation" test from
   §14, untestable in Direct Mode).
4. Confirm `get_unblocked_stages("permit-1")` returns
   `["fire_safety", "sanitary"]` (not `final_review` -- this is the other
   cross-contract-dependent behavior Direct Mode couldn't verify).
5. As `APPLICANT`, submit evidence + as anyone, adjudicate both
   `fire_safety` and `sanitary` to APPROVED.
6. Confirm `get_unblocked_stages("permit-1")` now returns
   `["final_review"]`.
7. Submit evidence + adjudicate `final_review` to APPROVED.
8. Call `refresh_process_status("permit-1")` on the Router, confirm status
   is `COMPLETE`.
9. On CertificationGate, call `claim_eligibility("permit-1")`, confirm
   `is_eligible("permit-1")` returns `true`.

## What to report back

For each numbered step above: pass/fail, and the exact revert message if
anything failed differently than described. That's the fastest way to
find out whether any assumption in `docs/architecture.md` (particularly
§9.1 hashlib, §14 the buyer-ownership check, and the calldata shape
`get_obligation`/`get_process_status` actually return over a real
cross-contract call) doesn't hold on a live node the way Direct Mode
suggested it should.
