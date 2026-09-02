"""
Direct Mode tests for ProcessGraphRouter -- SINGLE-CONTRACT SCOPE ONLY.

VERIFIED FINDING (this session, genlayer-test==0.29.2 / genvm v0.2.16):
GenVM enforces "only one contract is allowed" per Python process
(genlayer/gl/genvm_contracts.py, __known_contract__ singleton check) --
this is a genuine runtime constraint, not a testing-tool quirk (in
production, one WASM module = one contract). Direct Mode's pytest
fixtures activate one VM per test and do NOT install a cross-contract
call hook (`_gl_call_hook` is None unless a "glsim" harness sets it up),
so `gl.get_contract_at(...)` calls fail in Direct Mode: there is no
second, actually-deployed contract to route the call to.

CONSEQUENCE: every test below deploys ProcessGraphRouter ALONE (no Gate
deployed alongside it) and only exercises code paths that do NOT reach
`_read_gate_obligation` (i.e. everything in `register_process` up to and
including DAG structural/cycle validation, plus `register_authority` and
constructor validation, all of which run before any cross-contract call).

NOT verified by this file, and NOT verifiable in Direct Mode at all:
    - the "spoofed obligation" authority check (needs a real Gate to read
      `buyer` from)
    - `get_unblocked_stages` / `refresh_process_status` (both need live
      Gate reads)
    - anything touching an actual Gate<->Router interaction

Those require either a "glsim" harness with `vm._gl_call_hook` wired to a
second in-process contract instance, or GenLayer's Integration Testing
mode against a running localnet -- neither is available in this sandbox.
This is stated plainly rather than glossed over; see
docs/architecture.md "Verification log" for the full writeup and what
running these would require.

Run with:
    pip install genlayer-test
    pytest tests/test_process_graph_router.py -v
"""

import json

ROUTER_PATH = "contracts/process_graph_router.py"
SDK_VERSION = "v0.2.16"


def _ensure_sdk_loaded():
    """Router's constructor needs a gate_address Address argument BEFORE
    any deploy happens (chicken-and-egg vs. genlayer.py.types.Address only
    becoming importable once SDK paths are set up, which normally happens
    inside deploy_contract). Force that setup, standalone, so addresses
    can be constructed up front.

    MUST run unconditionally on every call, not just once per process:
    VMContext._cleanup_after_deactivate() strips SDK paths from sys.path
    and evicts genlayer modules from sys.modules at the end of EVERY test
    (by design, to avoid stale-SDK-version conflicts across tests with
    different sdk_version pins) -- caching "already loaded" across tests
    silently breaks the second and subsequent tests in a file with a
    confusing 'bytes has no as_bytes' error deep in storage code. Found
    the hard way in this session; see docs/architecture.md
    "Verification log"."""
    from pathlib import Path

    from gltest.direct.sdk_loader import setup_sdk_paths

    setup_sdk_paths(Path(ROUTER_PATH), SDK_VERSION)


def _addr(seed: str):
    _ensure_sdk_loaded()
    from gltest.direct.loader import create_address

    return create_address(seed)


def _deploy_router(direct_deploy, admin_prank_vm, admin, gate_address, max_stages=8):
    with admin_prank_vm.prank(admin):
        return direct_deploy(ROUTER_PATH, gate_address, max_stages, sdk_version=SDK_VERSION)


# --------------------------------------------------------------------- #
# Constructor validation
# --------------------------------------------------------------------- #


def test_constructor_rejects_zero_max_stages(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    with direct_vm.prank(admin):
        with direct_vm.expect_revert("max_stages_per_process must be in"):
            direct_deploy(ROUTER_PATH, gate_addr, 0, sdk_version=SDK_VERSION)


def test_constructor_rejects_oversized_max_stages(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    with direct_vm.prank(admin):
        with direct_vm.expect_revert("max_stages_per_process must be in"):
            direct_deploy(ROUTER_PATH, gate_addr, 999, sdk_version=SDK_VERSION)


def test_constructor_accepts_valid_max_stages(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr, max_stages=8)
    assert router is not None


def test_constructor_accepts_gate_address_as_plain_int(direct_vm, direct_deploy):
    """Regression test for a real Studio bug (reported and reproduced on a
    live testnet deployment): Studio's Constructor Inputs UI, even using
    its dedicated 'address' field, was observed submitting the value as a
    plain Python int in calldata instead of GenVM's Address type, causing
    'AttributeError: int object has no attribute as_bytes' deep inside
    storage assignment. Reproduces that exact shape here (an int derived
    from a real address's raw bytes, big-endian -- matching how the
    failing Studio transaction's args looked) and confirms
    _coerce_address() in the contract now handles it."""
    gate_addr = _addr("gate")
    admin = _addr("admin")
    gate_addr_as_int = int.from_bytes(gate_addr.as_bytes, "big")

    with direct_vm.prank(admin):
        router = direct_deploy(
            ROUTER_PATH, gate_addr_as_int, 8, sdk_version=SDK_VERSION
        )
    assert router is not None


# --------------------------------------------------------------------- #
# Admin-only authority registration
# --------------------------------------------------------------------- #


def test_register_authority_requires_admin(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    intruder = _addr("intruder")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(intruder):
        with direct_vm.expect_revert("only admin"):
            router.register_authority("fire_safety", intruder)


def test_register_authority_accepts_int_address(direct_vm, direct_deploy):
    """Same Studio-observed shape as the constructor regression test
    above, but for a regular write-method Address argument."""
    gate_addr = _addr("gate")
    admin = _addr("admin")
    fire_dept = _addr("fire_dept")
    fire_dept_as_int = int.from_bytes(fire_dept.as_bytes, "big")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.register_authority("fire_safety", fire_dept_as_int)  # must not raise


def test_register_authority_succeeds_for_admin(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    fire_dept = _addr("fire_dept")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.register_authority("fire_safety", fire_dept)
    # No direct getter exposed for the registry (by design -- see contract
    # docstring); success here means no revert. Re-registration (update)
    # should also succeed idempotently.
    with direct_vm.prank(admin):
        router.register_authority("fire_safety", fire_dept)


# --------------------------------------------------------------------- #
# Admin exit paths: renounce_admin() / freeze_upgrades()
# --------------------------------------------------------------------- #


def test_renounce_admin_blocks_further_registration(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    fire_dept = _addr("fire_dept")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.renounce_admin()

    with direct_vm.prank(admin):
        with direct_vm.expect_revert("admin has renounced"):
            router.register_authority("fire_safety", fire_dept)


def test_renounce_admin_requires_admin(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    intruder = _addr("intruder")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(intruder):
        with direct_vm.expect_revert("only admin may renounce"):
            router.renounce_admin()


def test_renounce_admin_cannot_be_called_twice(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.renounce_admin()
        with direct_vm.expect_revert("already renounced"):
            router.renounce_admin()


def test_freeze_upgrades_requires_admin(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    intruder = _addr("intruder")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(intruder):
        with direct_vm.expect_revert("only admin may freeze"):
            router.freeze_upgrades()


def test_freeze_upgrades_succeeds_for_admin(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.freeze_upgrades()  # must not raise


def test_freeze_upgrades_and_renounce_admin_are_independent(direct_vm, direct_deploy):
    """Freezing upgrades must not, by itself, disable register_authority --
    they are two separate switches (see contract docstring)."""
    gate_addr = _addr("gate")
    admin = _addr("admin")
    fire_dept = _addr("fire_dept")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.prank(admin):
        router.freeze_upgrades()
        router.register_authority("fire_safety", fire_dept)  # still allowed


# --------------------------------------------------------------------- #
# DAG structural / cycle validation
# (all of this runs BEFORE any cross-contract read, so it is fully
# testable in Direct Mode even without a real Gate deployed)
# --------------------------------------------------------------------- #


def test_unregistered_stage_type_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    graph = json.dumps(
        {
            "stages": [
                {
                    "stage_id": "sanitary",
                    "stage_type": "sanitary",  # never registered
                    "obligation_id": "permit-1:sanitary",
                    "mandatory": True,
                }
            ],
            "edges": [],
        }
    )
    with direct_vm.expect_revert("no authority registered"):
        router.register_process("permit-1", graph)


def test_cycle_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("a_type", admin)
        router.register_authority("b_type", admin)

    graph = json.dumps(
        {
            "stages": [
                {"stage_id": "a", "stage_type": "a_type", "obligation_id": "p1:a", "mandatory": True},
                {"stage_id": "b", "stage_type": "b_type", "obligation_id": "p1:b", "mandatory": True},
            ],
            "edges": [
                {"stage_id": "a", "depends_on": "b"},
                {"stage_id": "b", "depends_on": "a"},
            ],
        }
    )
    with direct_vm.expect_revert("cycle"):
        router.register_process("p1", graph)


def test_self_dependency_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("a_type", admin)

    graph = json.dumps(
        {
            "stages": [
                {"stage_id": "a", "stage_type": "a_type", "obligation_id": "p1:a", "mandatory": True},
            ],
            "edges": [{"stage_id": "a", "depends_on": "a"}],
        }
    )
    with direct_vm.expect_revert("self-dependency"):
        router.register_process("p1", graph)


def test_duplicate_stage_id_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("a_type", admin)

    graph = json.dumps(
        {
            "stages": [
                {"stage_id": "a", "stage_type": "a_type", "obligation_id": "p1:a1", "mandatory": True},
                {"stage_id": "a", "stage_type": "a_type", "obligation_id": "p1:a2", "mandatory": True},
            ],
            "edges": [],
        }
    )
    with direct_vm.expect_revert("duplicate stage_id"):
        router.register_process("p1", graph)


def test_too_many_stages_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr, max_stages=2)
    with direct_vm.prank(admin):
        router.register_authority("t", admin)

    graph = json.dumps(
        {
            "stages": [
                {"stage_id": "a", "stage_type": "t", "obligation_id": "p1:a", "mandatory": True},
                {"stage_id": "b", "stage_type": "t", "obligation_id": "p1:b", "mandatory": True},
                {"stage_id": "c", "stage_type": "t", "obligation_id": "p1:c", "mandatory": True},
            ],
            "edges": [],
        }
    )
    with direct_vm.expect_revert("too many stages"):
        router.register_process("p1", graph)


def test_malformed_json_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.expect_revert("not valid JSON"):
        router.register_process("p1", "{not json")


def test_empty_stages_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    graph = json.dumps({"stages": [], "edges": []})
    with direct_vm.expect_revert("non-empty 'stages'"):
        router.register_process("p1", graph)


def test_edge_referencing_unknown_stage_rejected(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("t", admin)

    graph = json.dumps(
        {
            "stages": [
                {"stage_id": "a", "stage_type": "t", "obligation_id": "p1:a", "mandatory": True},
            ],
            "edges": [{"stage_id": "a", "depends_on": "ghost"}],
        }
    )
    with direct_vm.expect_revert("unknown depends_on"):
        router.register_process("p1", graph)


# --------------------------------------------------------------------- #
# Post-review regression tests: obligation <-> stage_type binding
# (all of this runs BEFORE any cross-contract read, so it is fully
# testable in Direct Mode even without a real Gate deployed -- same
# reasoning as the DAG structural/cycle validation tests above)
# --------------------------------------------------------------------- #


def test_bind_obligation_stage_type_requires_registered_stage_type(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)

    with direct_vm.expect_revert("no authority registered"):
        router.bind_obligation_stage_type("p1:a", "fire_safety")


def test_bind_obligation_stage_type_requires_correct_authority(direct_vm, direct_deploy):
    gate_addr = _addr("gate")
    admin = _addr("admin")
    fire_dept = _addr("fire_dept")
    intruder = _addr("intruder")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("fire_safety", fire_dept)

    with direct_vm.prank(intruder):
        with direct_vm.expect_revert("only the registered authority"):
            router.bind_obligation_stage_type("p1:a", "fire_safety")


def test_register_process_rejects_unbound_obligation(direct_vm, direct_deploy):
    """Closed post-review finding: an obligation created by an address
    that IS the registered authority for a stage_type must still be
    explicitly bound to that stage_type before register_process will
    trust it -- otherwise an authority governing more than one stage_type
    could have an obligation meant for one stage_type silently accepted
    for another. This check is local (no cross-contract read needed) and
    runs before `_read_gate_obligation` is ever reached, so it is fully
    verifiable in Direct Mode even without a live Gate."""
    gate_addr = _addr("gate")
    admin = _addr("admin")
    authority = _addr("authority")
    router = _deploy_router(direct_deploy, direct_vm, admin, gate_addr)
    with direct_vm.prank(admin):
        router.register_authority("fire_safety", authority)

    graph = json.dumps(
        {
            "stages": [
                {
                    "stage_id": "a",
                    "stage_type": "fire_safety",
                    "obligation_id": "p1:a",
                    "mandatory": True,
                }
            ],
            "edges": [],
        }
    )
    with direct_vm.expect_revert("has not been bound"):
        router.register_process("p1", graph)
