"""
Direct Mode tests for SemanticObligationGate.

Framework: `genlayer-test` (pip install genlayer-test), Direct Mode.

VERIFIED IN THIS SESSION against genlayer-test==0.29.2 / genvm v0.2.16
(the last GenVM release publishing the "genvm-universal.tar.xz" bundle
that this genlayer-test version's Direct Mode downloader expects -- the
current "latest" GitHub release, v0.3.0-rc7, ships per-platform archives
instead and is NOT compatible with this genlayer-test version's Direct
Mode SDK loader as installed; sdk_version is pinned explicitly below for
that reason -- see docs/architecture.md "Verification log" for the full
writeup of this and other real issues found and fixed against actual
execution, not just documentation).

Run with:

    pip install genlayer-test
    pytest tests/test_semantic_gate.py -v

NOTE: `direct_alice` / `direct_bob` / `direct_charlie` fixtures (as
shipped in genlayer-test 0.29.2) resolve BEFORE any contract is deployed,
i.e. before `genlayer.py.types.Address` is importable -- they silently
fall back to raw `bytes` instead of `Address` in that case, which then
fails deep inside storage field assignment with a confusing
`AttributeError: 'bytes' object has no attribute 'as_bytes'`. This file
works around it with `_addr()`, which calls the same `create_address()`
helper directly, AFTER the first `direct_deploy()` call in each test (by
which point genlayer is on sys.path and `create_address` returns a real
`Address`). Do not reintroduce the `direct_alice`/`direct_bob` fixtures
here without this ordering guarantee.
"""

import json

CONTRACT_PATH = "contracts/semantic_obligation_gate.py"
SDK_VERSION = "v0.2.16"


def _addr(seed: str):
    from gltest.direct.loader import create_address

    return create_address(seed)


def _deploy(direct_deploy):
    return direct_deploy(CONTRACT_PATH, sdk_version=SDK_VERSION)


POLICY = (
    "Obligation is satisfied only if: quantity >= 100 units of product X200 "
    "are delivered, AND a delivery certificate is present, AND delivery "
    "occurs on or before the stated deadline."
)
DEADLINE = "2026-08-14T00:00:00+00:00"

APPROVED_JSON = json.dumps(
    {
        "decision": "APPROVED",
        "quantity_match": True,
        "specification_match": True,
        "deadline_match": True,
        "critical_exception": False,
        "reason_code": "ALL_REQUIRED_CRITERIA_MET",
    }
)

REJECTED_JSON = json.dumps(
    {
        "decision": "REJECTED",
        "quantity_match": False,
        "specification_match": True,
        "deadline_match": True,
        "critical_exception": False,
        "reason_code": "QUANTITY_SHORTFALL",
    }
)

UNDETERMINED_JSON = json.dumps(
    {
        "decision": "UNDETERMINED",
        "quantity_match": False,
        "specification_match": False,
        "deadline_match": False,
        "critical_exception": False,
        "reason_code": "EVIDENCE_UNREADABLE",
    }
)

MALFORMED_JSON = json.dumps({"decision": "MAYBE", "reason_code": "X"})


def _create_and_submit(contract, vm, buyer, supplier, evidence_url="https://example.com/delivery.json"):
    with vm.prank(buyer):
        contract.create_obligation("po-1", supplier, POLICY, DEADLINE)
    vm.mock_web(r"example\.com", {"status": 200, "body": "{}"})
    with vm.prank(supplier):
        contract.submit_evidence("po-1", [evidence_url], "hash-abc")


# --------------------------------------------------------------------- #
# PROOF-1 / HAPPY: unstructured evidence -> structured, finalized verdict
# --------------------------------------------------------------------- #


def test_happy_path_approved(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")

    assert contract.get_status("po-1") == "FINALIZED"
    verdict = contract.get_verdict("po-1")
    assert verdict["decision"] == "APPROVED"
    assert verdict["quantity_match"] is True
    assert verdict["critical_exception"] is False


def test_rejected_path(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", REJECTED_JSON)
    contract.adjudicate("po-1")

    assert contract.get_status("po-1") == "FINALIZED"
    assert contract.get_verdict("po-1")["decision"] == "REJECTED"


def test_undetermined_is_not_rejected(direct_vm, direct_deploy):
    """REJECTED (policy clearly not met) must never be used as a stand-in
    for UNDETERMINED (insufficient evidence to decide)."""
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", UNDETERMINED_JSON)
    contract.adjudicate("po-1")

    assert contract.get_status("po-1") == "UNDETERMINED"
    assert contract.get_verdict("po-1")["decision"] == "UNDETERMINED"
    assert contract.get_status("po-1") != "FINALIZED"


def test_undetermined_can_be_re_resolved(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", UNDETERMINED_JSON)
    contract.adjudicate("po-1")
    assert contract.get_status("po-1") == "UNDETERMINED"

    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": "{}"})
    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")

    assert contract.get_status("po-1") == "FINALIZED"
    assert contract.get_verdict("po-1")["decision"] == "APPROVED"


# --------------------------------------------------------------------- #
# PROOF-2: validators independently verify substance, not raw text
# --------------------------------------------------------------------- #


def test_validator_disagrees_on_different_evidence(direct_vm, direct_deploy):
    """Leader sees APPROVED; if the validator's own independent run of the
    same pipeline produces a different semantic result, validator_fn must
    return False (disagreement), never silently accept the leader."""
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")  # leader run captured internally

    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": "{}"})
    direct_vm.mock_llm(r".*", REJECTED_JSON)  # validator now sees a different world

    assert direct_vm.run_validator() is False


def test_validator_agrees_on_same_evidence(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")

    # Same mocks still active -> validator re-derives the identical verdict.
    assert direct_vm.run_validator() is True


def test_validator_disagrees_when_evidence_content_changes(direct_vm, direct_deploy):
    """Regression test for a real vulnerability class discovered via live
    Studio testing (see docs/architecture.md "Evidence Integrity" /
    Verification log): evidence_refs point to a URL, and URLs are
    mutable. If the SAME semantic decision comes back from the LLM both
    times (unlike test_validator_disagrees_on_different_evidence, which
    changes the LLM's answer), a validator must still disagree if the
    underlying evidence CONTENT itself differs between the leader's fetch
    and its own independent fetch -- this is exactly what would happen if
    a submitter edited a live document (e.g. a GitHub Gist) between
    calls, or a malicious host served different content to different
    fetchers. Without the `_evidence_content_hash` comparison added to
    `_verdicts_semantically_equal`, this attack would silently succeed
    (same decision fields -> validator agrees -> unsafe finalization on
    content nobody actually agreed was fetched identically)."""
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_web(r"example\.com", {"status": 200, "body": "version A"})
    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")  # leader fetches "version A"

    direct_vm.clear_mocks()
    direct_vm.mock_web(r"example\.com", {"status": 200, "body": "version B"})
    direct_vm.mock_llm(r".*", APPROVED_JSON)  # identical decision fields

    assert direct_vm.run_validator() is False


# --------------------------------------------------------------------- #
# PROOF-3: malformed / non-schema LLM output never becomes a decision
# --------------------------------------------------------------------- #


def test_malformed_llm_output_never_finalizes(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)

    direct_vm.mock_llm(r".*", MALFORMED_JSON)
    contract.adjudicate("po-1")

    # validator_fn must reject the malformed leader result -> disagreement.
    assert direct_vm.run_validator() is False


# --------------------------------------------------------------------- #
# Authority model
# --------------------------------------------------------------------- #


def test_supplier_cannot_equal_buyer(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice = _addr("alice")
    with direct_vm.prank(alice):
        with direct_vm.expect_revert("supplier must differ from buyer"):
            contract.create_obligation("po-x", alice, POLICY, DEADLINE)


def test_create_obligation_accepts_supplier_as_plain_int(direct_vm, direct_deploy):
    """Regression test for a real Studio bug (reported and reproduced on a
    live testnet deployment): an Address-typed argument was observed
    arriving as a plain Python int in calldata instead of GenVM's Address
    type. Reproduces that shape for `supplier` and confirms
    _coerce_address() now handles it."""
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    bob_as_int = int.from_bytes(bob.as_bytes, "big")
    with direct_vm.prank(alice):
        contract.create_obligation("po-int", bob_as_int, POLICY, DEADLINE)  # must not raise


def test_unauthorized_party_cannot_submit_evidence(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob, charlie = _addr("alice"), _addr("bob"), _addr("charlie")
    with direct_vm.prank(alice):
        contract.create_obligation("po-1", bob, POLICY, DEADLINE)

    with direct_vm.prank(charlie):
        with direct_vm.expect_revert("only buyer or supplier"):
            contract.submit_evidence("po-1", ["https://example.com/x.json"], "h")


def test_duplicate_obligation_id_rejected(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    with direct_vm.prank(alice):
        contract.create_obligation("po-1", bob, POLICY, DEADLINE)
        with direct_vm.expect_revert("already exists"):
            contract.create_obligation("po-1", bob, POLICY, DEADLINE)


def test_invalid_deadline_iso_rejected(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    with direct_vm.prank(alice):
        with direct_vm.expect_revert("not a valid ISO 8601"):
            contract.create_obligation("po-bad-date", bob, POLICY, "asap")


def test_oversized_policy_rejected(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    huge_policy = "x" * 5000
    with direct_vm.prank(alice):
        with direct_vm.expect_revert("policy too long"):
            contract.create_obligation("po-huge", bob, huge_policy, DEADLINE)


# --------------------------------------------------------------------- #
# PROOF-4 / finality & reopening protection
# --------------------------------------------------------------------- #


def test_evidence_cannot_be_resubmitted_after_finalization(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    _create_and_submit(contract, direct_vm, alice, bob)
    direct_vm.mock_llm(r".*", APPROVED_JSON)
    contract.adjudicate("po-1")
    assert contract.get_status("po-1") == "FINALIZED"

    with direct_vm.prank(bob):
        with direct_vm.expect_revert("already finalized"):
            contract.submit_evidence("po-1", ["https://example.com/y.json"], "h2")


def test_adjudicate_rejected_when_no_evidence_submitted_yet(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    with direct_vm.prank(alice):
        contract.create_obligation("po-1", bob, POLICY, DEADLINE)

    with direct_vm.expect_revert("not ready for adjudication"):
        contract.adjudicate("po-1")


# --------------------------------------------------------------------- #
# Evidence handling
# --------------------------------------------------------------------- #


def test_duplicate_evidence_refs_are_deduped(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    with direct_vm.prank(alice):
        contract.create_obligation("po-1", bob, POLICY, DEADLINE)

    direct_vm.mock_web(r"example\.com", {"status": 200, "body": "{}"})
    with direct_vm.prank(bob):
        contract.submit_evidence(
            "po-1",
            [
                "https://example.com/a.json",
                "https://example.com/a.json",
                "https://example.com/b.json",
            ],
            "hash-1",
        )

    refs = contract.get_evidence_refs("po-1")
    assert len(refs) == 2
    assert refs.count("https://example.com/a.json") == 1


def test_empty_evidence_refs_rejected(direct_vm, direct_deploy):
    contract = _deploy(direct_deploy)
    alice, bob = _addr("alice"), _addr("bob")
    with direct_vm.prank(alice):
        contract.create_obligation("po-1", bob, POLICY, DEADLINE)

    with direct_vm.prank(bob):
        with direct_vm.expect_revert("must not be empty"):
            contract.submit_evidence("po-1", [], "hash-1")
