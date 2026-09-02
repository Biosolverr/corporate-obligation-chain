# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
CertificationGate -- the deterministic terminal step. Turns a
ProcessGraphRouter process that has reached COMPLETE into a permanent,
queryable eligibility flag. Nothing more.

WHAT THIS CONTRACT DOES (and does not)
---------------------------------------
This is the "SettlementGate" / "CertificationGate" from the original
brief's Milestone 3 (section 22): the last deterministic hop after
semantic adjudication (SemanticObligationGate) and graph orchestration
(ProcessGraphRouter) have both already run. It reads exactly one thing --
whether a process on a configured Router is COMPLETE -- and if so, records
a permanent ELIGIBLE flag for that process_id.

It does NOT:
    - move money, mint a token, or transfer any value
    - issue a permit, certificate, or any real-world credential
    - call `.emit()` on the Router (same reasoning as
      process_graph_router.py's Gate boundary: only `.view()` reads,
      never a write to another contract)
    - contain any LLM call, web fetch, or `gl.vm.run_nondet_unsafe` block.
      This entire contract is deterministic -- there is nothing left to
      adjudicate by the time a process reaches this contract; every
      semantic judgment call already happened, with consensus, inside
      SemanticObligationGate instances upstream.

The domain-specific label ("SETTLEMENT_ELIGIBLE" for procurement,
"CERTIFIED_ELIGIBLE" for a permit) is a documentation-level choice, not a
code one -- this contract stores a single generic `ELIGIBLE` status and
leaves what a caller *does* with that fact (release an escrow, print a
certificate, unlock a shipment) entirely outside its scope, on purpose:
that action requires its own authority model specific to the value being
moved, which is exactly the kind of domain-specific complexity the
original brief's section 22 said an MVP should not build.

WHY THIS IS A SEPARATE CONTRACT, NOT A METHOD ON THE ROUTER
------------------------------------------------------------------
Same reasoning as splitting SemanticObligationGate from ProcessGraphRouter
(process_graph_router.py section 11): a downstream integration that wants
to react to "this permit process is done" should be able to depend on a
small, stable, single-purpose contract instead of the whole graph-
orchestration surface (which keeps changing shape as new stage_types /
authorities are registered). It also keeps the eligibility record's own
finality independent of anything that might later be added to
ProcessGraphRouter.

AUTHORITY MODEL
-------------------
Fully permissionless, on purpose, and consistently with the rest of this
codebase's permissionless-trigger pattern
(SemanticObligationGate.adjudicate(), ProcessGraphRouter.refresh_process_status()):
`claim_eligibility()` does not decide anything -- it only reads an
already-decided fact from the Router and records it. Restricting who may
call it would add a liveness risk (whoever benefits from delay simply
never calls it) without adding security, since the eligibility outcome is
entirely determined by the Router's (and, transitively, the Gate's)
already-consensus-backed state, not by the caller.

FINALITY CHAIN
------------------
This contract's ELIGIBLE flag is only as permanent as the two contracts
beneath it, and it inherits their finality by construction, not by
assumption:
    SemanticObligationGate: FINALIZED is terminal (no resubmission, no
        re-adjudication once decided).
    ProcessGraphRouter: COMPLETE/FAILED are terminal (monotonic transition
        out of ACTIVE, guarded by the Gate's own finality above it).
    CertificationGate (this file): once a process_id is recorded ELIGIBLE,
        `claim_eligibility` refuses to run again for that process_id.
This means there is no path, anywhere in this three-contract chain, for an
already-recorded ELIGIBLE flag to later become invalid -- which is exactly
the property anything downstream (an escrow release, a certificate
printer) needs before it can safely treat this flag as a green light.

DETERMINISM
---------------
Fully deterministic, like ProcessGraphRouter: no `gl.nondet.*` calls
anywhere in this file.

KNOWN LIMITATIONS (stated, not hidden)
-------------------------------------------
1. `_read_router_status` is a cross-contract `.view()` call, subject to
   the exact same Direct Mode testing limitation documented in
   process_graph_router.py section 23.4 / docs/architecture.md: it cannot
   be exercised by any test in this repository, because GenVM enforces one
   contract per process in Direct Mode. Only this file's own internal
   logic (idempotency, structural checks) is verified by
   tests/test_certification_gate.py.
2. Like the other two contracts, `router_address` is a single, immutable
   address set at deploy time (constructor argument, never mutated
   afterward) -- deploying against the wrong Router, or a Router that is
   later replaced, requires deploying a new CertificationGate. This is the
   same trade-off `ProcessGraphRouter.gate_address` already makes, kept
   consistent rather than solved differently here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from genlayer import *

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ROUTER_STATUS_COMPLETE = "COMPLETE"

ELIGIBILITY_STATUS_NONE = ""
ELIGIBILITY_STATUS_ELIGIBLE = "ELIGIBLE"


def _coerce_address(val) -> Address:
    """Defensive coercion for any Address-typed argument coming in from a
    public write/constructor call.

    VERIFIED NEEDED (Studio, live testnet -- reported and reproduced
    against process_graph_router.py's constructor first; identical
    coercion applied here for consistency, since this contract has the
    same single-Address-constructor-argument shape). See
    process_graph_router.py's copy of this function for the full
    writeup.

    Closed post-review finding: a negative `int`, or one wider than
    `Address.SIZE` bytes, previously reached `int.to_bytes` unguarded and
    raised a raw `OverflowError`/`ValueError` instead of the
    `gl.vm.UserError` every other input-validation failure in this file
    uses.
    """
    if hasattr(val, "as_bytes"):
        return val
    if isinstance(val, bool):
        raise gl.vm.UserError(f"invalid address value: {val!r}")
    if isinstance(val, int):
        try:
            return Address(val.to_bytes(Address.SIZE, "big"))
        except (OverflowError, ValueError):
            raise gl.vm.UserError(f"invalid address value: {val!r}")
    if isinstance(val, (bytes, bytearray, memoryview)):
        return Address(bytes(val))
    if isinstance(val, str):
        return Address(val)
    raise gl.vm.UserError(f"unsupported address value type: {type(val)}")


# --------------------------------------------------------------------------
# Storage schema
# --------------------------------------------------------------------------


@allow_storage
@dataclass
class EligibilityRecord:
    status: str
    claimed_by: Address
    claimed_at: str


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


class CertificationGate(gl.Contract):
    router_address: Address
    eligibility: TreeMap[str, EligibilityRecord]
    # Same existence-tracking pattern as the other two contracts in this
    # repo: TreeMap membership semantics for a missing key are not
    # confirmed against docs, so existence is tracked via an explicit,
    # iterated list.
    claimed_process_ids: DynArray[str]

    def __init__(self, router_address: Address):
        self.router_address = _coerce_address(router_address)

    # ---------------------------------------------------------------- #
    # Internal helpers
    # ---------------------------------------------------------------- #

    def _is_claimed(self, process_id: str) -> bool:
        for existing in self.claimed_process_ids:
            if existing == process_id:
                return True
        return False

    def _read_router_status(self, process_id: str) -> str:
        """Deterministic cross-contract read. Never `.emit()` -- see
        module docstring. Wrapped so a Router-side revert (e.g. unknown
        process_id) surfaces as a clear, attributable error instead of an
        opaque cross-contract exception."""
        router = gl.get_contract_at(self.router_address)
        try:
            return router.view().get_process_status(process_id)
        except Exception as exc:
            raise gl.vm.UserError(
                f"could not read process '{process_id}' from router "
                f"{self.router_address}: {exc}"
            )

    # ---------------------------------------------------------------- #
    # Writes
    # ---------------------------------------------------------------- #

    @gl.public.write
    def claim_eligibility(self, process_id: str) -> None:
        if not process_id.strip():
            raise gl.vm.UserError("process_id must not be empty")
        if self._is_claimed(process_id):
            raise gl.vm.UserError(
                f"process already claimed eligible: {process_id}"
            )

        status = self._read_router_status(process_id)
        if status != ROUTER_STATUS_COMPLETE:
            raise gl.vm.UserError(
                f"process '{process_id}' is not COMPLETE on the configured "
                f"router (status={status}); not eligible"
            )

        record = EligibilityRecord(
            status=ELIGIBILITY_STATUS_ELIGIBLE,
            claimed_by=gl.message.sender_address,
            claimed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.eligibility[process_id] = record
        self.claimed_process_ids.append(process_id)

    # ---------------------------------------------------------------- #
    # Views
    # ---------------------------------------------------------------- #

    @gl.public.view
    def is_eligible(self, process_id: str) -> bool:
        return self._is_claimed(process_id)

    @gl.public.view
    def get_eligibility(self, process_id: str) -> dict:
        if not self._is_claimed(process_id):
            return {
                "status": ELIGIBILITY_STATUS_NONE,
                "claimed_by": None,
                "claimed_at": "",
            }
        record = self.eligibility[process_id]
        return {
            "status": record.status,
            "claimed_by": record.claimed_by,
            "claimed_at": record.claimed_at,
        }
