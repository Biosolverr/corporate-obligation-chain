"""
Direct Mode tests for CertificationGate -- SINGLE-CONTRACT SCOPE ONLY.

Same real, verified constraint as tests/test_process_graph_router.py:
GenVM allows only one contract per process, so no test here can deploy a
real ProcessGraphRouter alongside CertificationGate. Every code path that
depends on `_read_router_status` succeeding (i.e. the entire "happy path"
of `claim_eligibility`, and everything depending on it having already
succeeded) is therefore NOT exercised by this file and cannot be, in
Direct Mode. What IS verified here, for real: constructor behaviour,
input validation, the two read-only views against un-claimed state, and
that a cross-contract-call failure degrades to a clear `UserError`
instead of an opaque exception -- which is itself a real, useful thing to
confirm, since `router_address` pointing at nothing deployed is exactly
what happens in this test environment.

Run with:
    pip install genlayer-test
    pytest tests/test_certification_gate.py -v
"""

CONTRACT_PATH = "contracts/certification_gate.py"
SDK_VERSION = "v0.2.16"


def _ensure_sdk_loaded():
    from pathlib import Path

    from gltest.direct.sdk_loader import setup_sdk_paths

    setup_sdk_paths(Path(CONTRACT_PATH), SDK_VERSION)


def _addr(seed: str):
    _ensure_sdk_loaded()
    from gltest.direct.loader import create_address

    return create_address(seed)


def _deploy(direct_deploy, router_address):
    return direct_deploy(CONTRACT_PATH, router_address, sdk_version=SDK_VERSION)


def test_constructor_succeeds(direct_vm, direct_deploy):
    router_addr = _addr("router")
    contract = _deploy(direct_deploy, router_addr)
    assert contract is not None


def test_constructor_accepts_router_address_as_plain_int(direct_vm, direct_deploy):
    """Regression test for a real Studio bug (reported and reproduced on a
    live testnet deployment): an Address-typed constructor argument was
    observed arriving as a plain Python int in calldata instead of
    GenVM's Address type. Reproduces that shape and confirms
    _coerce_address() now handles it."""
    router_addr = _addr("router")
    router_addr_as_int = int.from_bytes(router_addr.as_bytes, "big")
    contract = _deploy(direct_deploy, router_addr_as_int)
    assert contract is not None


def test_empty_process_id_rejected(direct_vm, direct_deploy):
    router_addr = _addr("router")
    contract = _deploy(direct_deploy, router_addr)

    with direct_vm.expect_revert("process_id must not be empty"):
        contract.claim_eligibility("")


def test_claim_fails_safely_when_router_unreachable(direct_vm, direct_deploy):
    """No real ProcessGraphRouter is deployed in this Direct Mode session
    (GenVM allows only one contract per process -- see
    docs/architecture.md section 23.4). VERIFIED THIS SESSION: Direct
    Mode's cross-contract `.view()` call to an address with nothing
    deployed there does not raise -- it silently returns None instead of
    a real status string (see the `_gl_call_hook`-less "Unknown gl_call
    request type: ['CallContract']" trace). This confirms the contract
    fails SAFE in that situation anyway: `None != "COMPLETE"` naturally
    refuses eligibility rather than granting it, without needing the
    `except Exception` branch in `_read_router_status` to fire at all.
    (The `try/except` wrapper in `_read_router_status` still matters for a
    REAL misconfigured/unreachable router on a live network, where a
    cross-contract call failure more plausibly raises -- this test just
    documents that Direct Mode's specific failure shape isn't the one that
    exercises it.)"""
    router_addr = _addr("router")
    contract = _deploy(direct_deploy, router_addr)

    with direct_vm.expect_revert("not eligible"):
        contract.claim_eligibility("permit-1")


def test_is_eligible_false_for_unclaimed(direct_vm, direct_deploy):
    router_addr = _addr("router")
    contract = _deploy(direct_deploy, router_addr)

    assert contract.is_eligible("permit-1") is False


def test_get_eligibility_returns_none_status_for_unclaimed(direct_vm, direct_deploy):
    router_addr = _addr("router")
    contract = _deploy(direct_deploy, router_addr)

    record = contract.get_eligibility("permit-1")
    assert record["status"] == ""
    assert record["claimed_at"] == ""
