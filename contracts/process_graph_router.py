# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
ProcessGraphRouter -- orchestrates a DAG of independent SemanticObligationGate
adjudications ("Administratum" direction: consensus against bureaucracy).

WHAT THIS CONTRACT DOES (and does not)
---------------------------------------
Where SemanticObligationGate answers "was ONE obligation satisfied", this
contract answers "given a graph of independent obligations with dependency
edges between them, which stages can be adjudicated right now, and has the
whole graph succeeded or failed". It turns a bureaucratic process that
humans usually run as a SEQUENTIAL queue (one office after another, even
when the offices don't actually depend on each other) into a graph where
genuinely independent checks can run in parallel, with the router doing
nothing except reading already-consensus-backed results and computing
readiness.

This is a strict generalization, not a rewrite: a linear procurement chain
(PO -> Delivery -> Acceptance -> Invoice) is simply a DAG with one edge per
consecutive pair. Nothing about SemanticObligationGate changes to support
this -- the same, already-proven contract is reused unmodified as the
adjudication unit for every node in the graph.

THIS CONTRACT NEVER:
    - creates obligations on the Gate (every stage's obligation is created
      directly by its real authority -- see Authority Model)
    - writes decision fields to the Gate
    - calls `.emit()` on the Gate. Only `.view()` reads. `.emit()` relays a
      message through the ghost-contract consensus layer (see
      docs.genlayer.com Messages / "What is GenLayer" architecture section)
      -- a second round of protocol consensus this router has no reason to
      trigger, since it never needs to *change* Gate state, only read
      already-finalized state. Introducing `.emit()` here would silently
      import a whole class of async-message complexity for zero benefit.
    - caches Gate decision fields in its own storage. `get_unblocked_stages`
      and `refresh_process_status` always re-read Gate state live, via
      cross-contract `.view()` calls, so there is exactly one source of
      truth for any obligation's verdict -- the Gate itself.

WHY GENLAYER IS NECESSARY HERE, SPECIFICALLY FOR THE GRAPH LAYER
---------------------------------------------------------------------
The single-gate primitive already answers "why not a centralized backend +
GPT" (see semantic_obligation_gate.py). The graph layer adds a second,
distinct necessity: safely PARALLELIZING several independent semantic
adjudications requires *someone* to compute "what's unblocked now" and
"did the whole thing fail" without being able to quietly falsify any single
stage's result. Because this router only ever reads consensus-backed Gate
state and never writes to it, no party -- including this router's own admin
-- can fabricate a stage's outcome. The graph-level trust requirement is
reduced to exactly one thing: "is the authority registry correct" (see
Authority Model) -- everything else inherits the Gate's consensus guarantee
unchanged.

AUTHORITY MODEL
-------------------
Procurement's buyer/supplier roles do NOT map onto bureaucratic review the
way intuition suggests. In a permit process, the REGULATOR (e.g. the fire
safety authority) is the Gate's `buyer` -- it is the one who writes policy
("these are the fire-safety rules"). The APPLICANT is the Gate's `supplier`
-- they submit evidence of compliance. Getting this backwards (making the
applicant the buyer) would let the applicant write their own policy, which
defeats the entire mechanism. This reversal versus the procurement
reference case is deliberate and must be preserved by anyone reusing this
router for a new domain.

Three roles exist at the router level:

    admin              deploys the router, registers which on-chain address
                        is the legitimate authority for each `stage_type`
                        (e.g. "fire_safety" -> the fire department's
                        address). See "Known limitations" for the
                        centralization trade-off this implies and its
                        documented exit path (contract freeze).
    process registrant  anyone; calls register_process() to declare a DAG
                        of already-existing obligation_ids. Purely
                        descriptive -- see next paragraph.
    the public          anyone; can call get_unblocked_stages(),
                        get_process_status(), refresh_process_status().

IMPORTANT: registering a process here confers NO authority over the
underlying obligations. All real enforcement (who may submit evidence, who
may trigger adjudication) lives entirely inside SemanticObligationGate,
unchanged. This router cannot write a single field on the Gate. The
`applicant` field stored per process is informational metadata only (who
asked this router to start tracking the graph) -- it is never used to gate
any action in this file. Do not read it as an access-control mechanism.

CLOSING THE "SPOOFED OBLIGATION" HOLE
-------------------------------------------
A naive router would accept any obligation_id a caller supplies for a given
stage_type, including one that belongs to a completely unrelated process
approved by nobody relevant (e.g. reusing someone else's already-APPROVED
obligation to fake completion of an unrelated permit's fire-safety stage).
`register_process` closes this by cross-contract-reading each referenced
obligation's `buyer` field from the Gate and checking it equals the
registered authority address for that stage's `stage_type`. Since `buyer`
on the Gate is set once, at obligation creation, by whoever called
`create_obligation` (and cannot be changed afterward -- see
semantic_obligation_gate.py's Obligation dataclass, no method mutates
`buyer`), this check is not spoofable by the process registrant: they would
need control of the real authority's private key to pass it.

DETERMINISM
---------------
This entire contract is deterministic. It calls no `gl.nondet.*` API and
never opens a `gl.vm.run_nondet_unsafe` block -- there is nothing here that
needs an LLM or a live web fetch; graph validation and cross-contract reads
of already-committed Gate state are both fully deterministic operations
across every validator.

STATE MACHINE (per registered process)
-------------------------------------------
    ACTIVE --refresh_process_status()--> COMPLETE   (all mandatory stages APPROVED)
    ACTIVE --refresh_process_status()--> FAILED      (any mandatory stage REJECTED)
    ACTIVE --refresh_process_status()--> ACTIVE       (no change; still pending)

FAILED and COMPLETE are terminal at the router level. This is safe because
FINALIZED is itself terminal on the Gate (semantic_obligation_gate.py
blocks resubmission and re-adjudication once an obligation is FINALIZED),
so a stage that has reached REJECTED or APPROVED cannot later flip -- the
router's terminal states cannot be invalidated by a later Gate write.

KNOWN LIMITATIONS (stated, not hidden)
-------------------------------------------
1. The authority registry is centralized in a single `admin` address for
   this MVP -- a deliberate, documented trade-off, not an oversight. Two
   independent, explicit, admin-triggered exits are provided rather than
   left implicit: `renounce_admin()` permanently disables
   `register_authority` alone (fast, narrow, closes the "compromised key
   rewrites the registry" risk specifically), and `freeze_upgrades()`
   calls GenVM's native `Root.lock_default()` to permanently lock this
   contract's code and its own `upgraders` slot (closes the broader
   "compromised key rewrites the whole contract" risk). These are
   independent switches -- call one, both, or neither, in either order.
   VERIFIED (this session, by reading the real SDK's
   `genlayer/py/storage/root.py`): `lock_default()` is never called
   automatically anywhere in the deploy path, so freezing is always this
   explicit, later, admin-triggered action -- not something that happens
   for you. Until one or both are called, a compromised admin key can
   rewrite the authority registry and/or (via `upgraders`) the whole
   contract -- the same trade-off every upgradable contract on any chain
   makes; it is not specific to this design and is not hidden here.
2. `get_unblocked_stages` and `get_stalled_stages` each perform up to
   `max_stages_per_process` cross-contract `.view()` calls per invocation.
   Both are read-only, consensus-free operations, but still O(stages) work
   per call -- `max_stages_per_process` exists specifically to bound this.
3. `json.loads` is standard library, not GenLayer-specific -- CONFIRMED
   working in this session's Direct Mode test run (see
   docs/architecture.md "Verification log"), which is a real GenVM Python
   environment, though not yet Studio/a live node.
4. No liveness timeout is implemented for stuck UNDETERMINED stages, on
   purpose: SemanticObligationGate's `adjudicate()` is already permissionless
   and callable by anyone at any time, so liveness does not require this
   router to add a redundant, and potentially harmful (see
   semantic_obligation_gate.py section on REJECTED vs UNDETERMINED),
   timeout-to-decision mechanism. `get_stalled_stages()` surfaces which
   stages are in this state so a human/UI can act on it, without the
   contract itself ever forcing a decision.
5. Registering an authority for a `stage_type` is a point-in-time
   assignment: if `admin` later calls `register_authority` again to change
   who the authority for a `stage_type` is, ALREADY-registered processes
   that referenced the old authority are unaffected (their ownership check
   already ran, once, at their own `register_process` time). This is
   intentional -- process registration snapshots trust at the moment it
   happens, the same way `SemanticObligationGate.policy_hash` snapshots a
   policy at obligation-creation time -- not a silent gap.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from genlayer import *

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

STATUS_ACTIVE = "ACTIVE"
STATUS_FAILED = "FAILED"
STATUS_COMPLETE = "COMPLETE"

# Gate-side vocabulary this router reads but never writes.
GATE_STATUS_FINALIZED = "FINALIZED"
GATE_STATUS_UNDETERMINED = "UNDETERMINED"
GATE_DECISION_APPROVED = "APPROVED"
GATE_DECISION_REJECTED = "REJECTED"

HARD_MAX_STAGES_CEILING = 32  # absolute ceiling; deployer cannot exceed this
MAX_STAGE_ID_CHARS = 64
MAX_STAGE_TYPE_CHARS = 64


def _coerce_address(val) -> Address:
    """Defensive coercion for any Address-typed argument coming in from a
    public write/constructor call.

    VERIFIED NEEDED (Studio, live testnet -- this exact failure was
    reported and reproduced): Studio's "Constructor Inputs" UI, even using
    its dedicated `address` field (not a raw JSON args array), was
    observed submitting the value as a plain Python `int` in calldata
    instead of GenVM's `Address` type -- causing
    `AttributeError: 'int' object has no attribute 'as_bytes'` deep inside
    storage assignment (`desc_base_types.py`'s `.set()`), long before it
    reaches any of this contract's own validation. This is a calldata/UI
    serialization issue upstream of this contract, not a mistake in how
    the address was typed -- so the fix has to live here: accept an
    already-correct `Address` unchanged, or coerce from `int` / `bytes` /
    a hex-or-base64 string (verified against `genlayer.py.types.Address`'s
    real constructor, which already accepts all of those except `int`).
    """
    if hasattr(val, "as_bytes"):
        return val
    if isinstance(val, bool):
        raise gl.vm.UserError(f"invalid address value: {val!r}")
    if isinstance(val, int):
        return Address(val.to_bytes(Address.SIZE, "big"))
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
class StageRef:
    stage_id: str
    stage_type: str
    obligation_id: str
    mandatory: bool


@allow_storage
@dataclass
class Edge:
    stage_id: str  # this stage...
    depends_on: str  # ...cannot be considered unblocked until this one is APPROVED


@allow_storage
@dataclass
class ProcessGraph:
    applicant: Address  # informational only -- see Authority Model
    stages: DynArray[StageRef]
    edges: DynArray[Edge]
    status: str
    created_at: str
    updated_at: str


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def _validate_dag_payload(payload: dict, max_stages: int):
    """Structural + graph-theoretic validation of the JSON DAG description.
    Raises gl.vm.UserError with a specific reason on any problem. Returns
    (stages, edges) as plain lists of dicts on success. Pure/deterministic --
    no I/O of any kind."""
    if not isinstance(payload, dict):
        raise gl.vm.UserError("graph payload must be a JSON object")

    raw_stages = payload.get("stages")
    raw_edges = payload.get("edges", [])
    if not isinstance(raw_stages, list) or len(raw_stages) == 0:
        raise gl.vm.UserError("graph payload must contain a non-empty 'stages' list")
    if not isinstance(raw_edges, list):
        raise gl.vm.UserError("'edges' must be a list")
    if len(raw_stages) > max_stages:
        raise gl.vm.UserError(
            f"too many stages ({len(raw_stages)}), max is {max_stages}"
        )

    seen_stage_ids = set()
    for s in raw_stages:
        if not isinstance(s, dict):
            raise gl.vm.UserError("each stage must be a JSON object")
        for key in ("stage_id", "stage_type", "obligation_id"):
            if not isinstance(s.get(key), str) or not s[key].strip():
                raise gl.vm.UserError(f"stage missing/empty required field: {key}")
        if len(s["stage_id"]) > MAX_STAGE_ID_CHARS:
            raise gl.vm.UserError("stage_id too long")
        if len(s["stage_type"]) > MAX_STAGE_TYPE_CHARS:
            raise gl.vm.UserError("stage_type too long")
        if not isinstance(s.get("mandatory"), bool):
            raise gl.vm.UserError(f"stage {s['stage_id']}: 'mandatory' must be a boolean")
        if s["stage_id"] in seen_stage_ids:
            raise gl.vm.UserError(f"duplicate stage_id in graph: {s['stage_id']}")
        seen_stage_ids.add(s["stage_id"])

    for e in raw_edges:
        if not isinstance(e, dict):
            raise gl.vm.UserError("each edge must be a JSON object")
        for key in ("stage_id", "depends_on"):
            if not isinstance(e.get(key), str) or not e[key].strip():
                raise gl.vm.UserError(f"edge missing/empty required field: {key}")
        if e["stage_id"] == e["depends_on"]:
            raise gl.vm.UserError(f"self-dependency not allowed: {e['stage_id']}")
        if e["stage_id"] not in seen_stage_ids:
            raise gl.vm.UserError(f"edge references unknown stage_id: {e['stage_id']}")
        if e["depends_on"] not in seen_stage_ids:
            raise gl.vm.UserError(f"edge references unknown depends_on: {e['depends_on']}")

    _assert_acyclic(seen_stage_ids, raw_edges)
    return raw_stages, raw_edges


def _assert_acyclic(stage_ids: set, edges: list) -> None:
    """Kahn's algorithm. Raises gl.vm.UserError if the dependency graph
    contains a cycle (which would make some stage permanently unblockable)."""
    indegree = {sid: 0 for sid in stage_ids}
    dependents: dict = {sid: [] for sid in stage_ids}
    for e in edges:
        # e["stage_id"] depends on e["depends_on"]:
        # an edge dependents[depends_on] -> stage_id, incrementing stage_id's indegree.
        dependents[e["depends_on"]].append(e["stage_id"])
        indegree[e["stage_id"]] += 1

    queue = [sid for sid in stage_ids if indegree[sid] == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if visited != len(stage_ids):
        raise gl.vm.UserError(
            "dependency graph contains a cycle -- some stage(s) would never unblock"
        )


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


class ProcessGraphRouter(gl.Contract):
    admin: Address
    admin_active: bool
    gate_address: Address
    max_stages_per_process: u32
    authority_registry: TreeMap[str, Address]
    # TreeMap membership semantics for a missing key are not confirmed
    # against docs, so stage_type existence is tracked via an explicit,
    # iterated list instead of relying on TreeMap defaults -- same pattern
    # as `obligation_ids` in semantic_obligation_gate.py.
    registered_stage_types: DynArray[str]
    processes: TreeMap[str, ProcessGraph]
    process_ids: DynArray[str]

    def __init__(self, gate_address: Address, max_stages_per_process: u32):
        gate_address = _coerce_address(gate_address)
        if max_stages_per_process == 0 or max_stages_per_process > HARD_MAX_STAGES_CEILING:
            raise gl.vm.UserError(
                f"max_stages_per_process must be in [1, {HARD_MAX_STAGES_CEILING}]"
            )
        self.admin = gl.message.sender_address
        self.admin_active = True
        self.gate_address = gate_address
        self.max_stages_per_process = max_stages_per_process

        # Seed the native upgrade-authority list with the same admin -- see
        # "Known limitations" #1 and freeze_upgrades()/renounce_admin()
        # below for the two independent, explicit ways to end this trust
        # assumption. VERIFIED (this session, reading the real SDK's
        # genlayer/py/storage/root.py): `lock_default()` is NOT called
        # automatically anywhere in the deploy path -- an earlier version
        # of this comment claimed it was; that was wrong and has been
        # corrected. Freezing is therefore always an explicit, later
        # action (freeze_upgrades()), never implicit.
        root = gl.storage.Root.get()
        root.upgraders.get().append(self.admin)

    # ---------------------------------------------------------------- #
    # Internal helpers
    # ---------------------------------------------------------------- #

    def _is_stage_type_registered(self, stage_type: str) -> bool:
        for t in self.registered_stage_types:
            if t == stage_type:
                return True
        return False

    def _process_exists(self, process_id: str) -> bool:
        for existing in self.process_ids:
            if existing == process_id:
                return True
        return False

    def _get_process_or_revert(self, process_id: str) -> ProcessGraph:
        if not self._process_exists(process_id):
            raise gl.vm.UserError(f"process not found: {process_id}")
        return self.processes[process_id]

    def _read_gate_obligation(self, obligation_id: str) -> dict:
        """Deterministic cross-contract read. Never uses `.emit()` -- see
        module docstring. Wrapped so a Gate-side revert (e.g. unknown
        obligation_id) surfaces as a clear router-level error instead of an
        opaque cross-contract exception."""
        gate = gl.get_contract_at(self.gate_address)
        try:
            return gate.view().get_obligation(obligation_id)
        except Exception as exc:
            raise gl.vm.UserError(
                f"could not read obligation '{obligation_id}' from gate "
                f"{self.gate_address}: {exc}"
            )

    # ---------------------------------------------------------------- #
    # Admin-only writes
    # ---------------------------------------------------------------- #

    @gl.public.write
    def register_authority(self, stage_type: str, authority_address: Address) -> None:
        if not self.admin_active:
            raise gl.vm.UserError(
                "admin has renounced authority-registry control; the "
                "registry is now permanently fixed"
            )
        if gl.message.sender_address != self.admin:
            raise gl.vm.UserError("only admin may register stage authorities")
        if not stage_type.strip():
            raise gl.vm.UserError("stage_type must not be empty")
        if len(stage_type) > MAX_STAGE_TYPE_CHARS:
            raise gl.vm.UserError("stage_type too long")

        if not self._is_stage_type_registered(stage_type):
            self.registered_stage_types.append(stage_type)
        self.authority_registry[stage_type] = _coerce_address(authority_address)

    @gl.public.write
    def renounce_admin(self) -> None:
        """Permanently and irreversibly disables `register_authority`.
        Closes the "compromised admin key rewrites the authority registry"
        risk on its own, independent of and faster than
        `freeze_upgrades()` -- a domain can renounce routine registry
        control once its authority set is considered final, while still
        leaving `upgraders` open a while longer for genuine bug fixes, or
        freeze both at once. There is no un-renounce: `admin_active` only
        ever goes True -> False."""
        if not self.admin_active:
            raise gl.vm.UserError("admin already renounced")
        if gl.message.sender_address != self.admin:
            raise gl.vm.UserError("only admin may renounce admin")
        self.admin_active = False

    @gl.public.write
    def freeze_upgrades(self) -> None:
        """Calls GenVM's native `Root.lock_default()`, permanently locking
        this contract's code and its own `upgraders`/`locked_slots` slots
        -- after this, nobody, including the current admin, can upgrade or
        patch this contract's code ever again. This is independent of
        `renounce_admin()`: freezing upgrades does NOT by itself disable
        `register_authority` (that is `self.admin`, an ordinary,
        unlocked storage field, not one of Root's special slots) -- call
        both for full, permanent immutability of this router. Restricted
        to the current admin so a stale/irrelevant caller cannot freeze a
        contract that is still being configured."""
        if gl.message.sender_address != self.admin:
            raise gl.vm.UserError("only admin may freeze upgrades")
        root = gl.storage.Root.get()
        root.lock_default()

    # ---------------------------------------------------------------- #
    # Process registration
    # ---------------------------------------------------------------- #

    @gl.public.write
    def register_process(self, process_id: str, graph_json: str) -> None:
        if not process_id.strip():
            raise gl.vm.UserError("process_id must not be empty")
        if self._process_exists(process_id):
            raise gl.vm.UserError(f"process already exists: {process_id}")

        try:
            payload = json.loads(graph_json)
        except Exception as exc:
            raise gl.vm.UserError(f"graph_json is not valid JSON: {exc}")

        raw_stages, raw_edges = _validate_dag_payload(payload, self.max_stages_per_process)

        stages: list[StageRef] = []
        for s in raw_stages:
            stage_type = s["stage_type"]
            if not self._is_stage_type_registered(stage_type):
                raise gl.vm.UserError(
                    f"no authority registered for stage_type '{stage_type}' "
                    f"-- call register_authority first"
                )
            expected_authority = self.authority_registry[stage_type]

            obligation = self._read_gate_obligation(s["obligation_id"])
            if obligation["buyer"] != expected_authority:
                raise gl.vm.UserError(
                    f"obligation '{s['obligation_id']}' for stage "
                    f"'{s['stage_id']}' (type '{stage_type}') was not "
                    f"created by the registered authority for that type -- "
                    f"refusing to trust it as evidence of that stage"
                )

            stages.append(
                StageRef(
                    stage_id=s["stage_id"],
                    stage_type=stage_type,
                    obligation_id=s["obligation_id"],
                    mandatory=s["mandatory"],
                )
            )

        edges: list[Edge] = []
        for e in raw_edges:
            edges.append(Edge(stage_id=e["stage_id"], depends_on=e["depends_on"]))

        now = datetime.now(timezone.utc).isoformat()
        graph = ProcessGraph(
            applicant=gl.message.sender_address,
            stages=stages,
            edges=edges,
            status=STATUS_ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self.processes[process_id] = graph
        self.process_ids.append(process_id)

    # ---------------------------------------------------------------- #
    # Live graph queries (never cache Gate decision fields)
    # ---------------------------------------------------------------- #

    @gl.public.view
    def get_unblocked_stages(self, process_id: str) -> list[str]:
        graph = self._get_process_or_revert(process_id)

        # One live read per stage, cached only for the duration of this
        # single view call (never written to storage) so dependency checks
        # below don't re-fetch the same stage repeatedly.
        verdicts: dict = {}
        for stage in graph.stages:
            obligation = self._read_gate_obligation(stage.obligation_id)
            verdicts[stage.stage_id] = (obligation["status"], obligation["decision"])

        unblocked = []
        for stage in graph.stages:
            status, decision = verdicts[stage.stage_id]
            if status == GATE_STATUS_FINALIZED:
                continue  # already decided either way, not "unblocked" -- it's done
            deps_satisfied = True
            for edge in graph.edges:
                if edge.stage_id != stage.stage_id:
                    continue
                dep_status, dep_decision = verdicts.get(edge.depends_on, (None, None))
                if not (dep_status == GATE_STATUS_FINALIZED and dep_decision == GATE_DECISION_APPROVED):
                    deps_satisfied = False
                    break
            if deps_satisfied:
                unblocked.append(stage.stage_id)
        return unblocked

    @gl.public.view
    def get_process_status(self, process_id: str) -> str:
        return self._get_process_or_revert(process_id).status

    @gl.public.view
    def get_stalled_stages(self, process_id: str) -> list[str]:
        """Stages whose Gate obligation is currently UNDETERMINED -- i.e.
        consensus WAS reached, and what it agreed on is "evidence is
        insufficient to decide" (see semantic_obligation_gate.py's
        REJECTED-vs-UNDETERMINED distinction). Not stuck by contract
        design (adjudicate() is permissionless and re-callable by anyone,
        anytime -- see module docstring's Known Limitation #4 on why no
        timeout exists), but genuinely useful for a UI/ops process to
        surface "these need a human to go get better evidence and trigger
        adjudicate() again" without silently waiting forever. Live read,
        same cost profile as get_unblocked_stages -- see Known
        Limitation #2."""
        graph = self._get_process_or_revert(process_id)
        stalled = []
        for stage in graph.stages:
            obligation = self._read_gate_obligation(stage.obligation_id)
            if obligation["status"] == GATE_STATUS_UNDETERMINED:
                stalled.append(stage.stage_id)
        return stalled

    @gl.public.write
    def refresh_process_status(self, process_id: str) -> None:
        """Permissionless, like Gate's adjudicate(): anyone may trigger a
        re-check. Monotonic -- ACTIVE can move to FAILED or COMPLETE, but
        FAILED/COMPLETE never move again (safe because FINALIZED is
        terminal on the Gate; see module docstring)."""
        graph = self._get_process_or_revert(process_id)
        if graph.status != STATUS_ACTIVE:
            return  # already terminal; nothing to do, not an error

        any_pending_mandatory = False
        for stage in graph.stages:
            obligation = self._read_gate_obligation(stage.obligation_id)
            status = obligation["status"]
            decision = obligation["decision"]

            if stage.mandatory and status == GATE_STATUS_FINALIZED and decision == GATE_DECISION_REJECTED:
                graph.status = STATUS_FAILED
                graph.updated_at = datetime.now(timezone.utc).isoformat()
                self.processes[process_id] = graph
                return

            if stage.mandatory and not (status == GATE_STATUS_FINALIZED and decision == GATE_DECISION_APPROVED):
                any_pending_mandatory = True

        if not any_pending_mandatory:
            graph.status = STATUS_COMPLETE
            graph.updated_at = datetime.now(timezone.utc).isoformat()
            self.processes[process_id] = graph
        # else: still ACTIVE, nothing to write.

    @gl.public.view
    def get_process(self, process_id: str) -> dict:
        graph = self._get_process_or_revert(process_id)
        return {
            "applicant": graph.applicant,
            "status": graph.status,
            "created_at": graph.created_at,
            "updated_at": graph.updated_at,
            "stages": [
                {
                    "stage_id": s.stage_id,
                    "stage_type": s.stage_type,
                    "obligation_id": s.obligation_id,
                    "mandatory": s.mandatory,
                }
                for s in graph.stages
            ],
            "edges": [
                {"stage_id": e.stage_id, "depends_on": e.depends_on} for e in graph.edges
            ],
        }
