from __future__ import annotations

"""Domain-aware guard minimization for Dedicated Event IR.

The earlier guard minimizer deliberately treats predicate-basis values as
independent Boolean variables.  That is sound but leaves logic on the table:
for a recovered finite-width state, predicates such as ``state == 0`` and
``state[1] == 1`` cannot take arbitrary combinations.

This pass uses only proof-backed Cartesian value domains already attached to
Dedicated Event storage IR.  Predicate components whose finite domain can be
fully enumerated are represented by their exact feasible truth signatures.
Components that cannot be proved finite/small are conservatively treated as
independent Boolean variables, so failure to analyze a component can only
reduce optimization, never make it unsound.

No protocol names, GPIO numbers, source stack names, basic-block identities, or
transition identities are used to decide a rewrite.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from .dedicated_event_guard_minimization import (
    _cube, 
    _cube_key, 
    _hold_key, 
    _outcome_key, 
)
from .dedicated_event_reachability import _eval_expr


Variable = tuple[str, Any]


def _freeze(x: Any) -> Any: 
    if isinstance(x, list): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, tuple): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, dict): 
        return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
    return x


def _canonical_basis_aliases(ir: dict[str, Any]) -> dict[str, str]: 
    """Exact-expression predicate aliases; no semantic guessing is involved."""
    first: dict[Any, str] = {}
    out: dict[str, str] = {}
    for row in ir.get("predicate_basis", []): 
        bid = str(row["id"])
        key = _freeze(row["expression"])
        out[bid] = first.setdefault(key, bid)
    return out


def _canonical_cube(rule: dict[str, Any], aliases: dict[str, str]) -> dict[str, bool] | None: 
    out: dict[str, bool] = {}
    for lit in rule.get("enable", []): 
        key = aliases.get(str(lit["basis"]), str(lit["basis"]))
        value = bool(lit["polarity"])
        if key in out and out[key] != value: 
            return None
        out[key] = value
    return out


def _gpio_mask_bits(value: int) -> set[int]: 
    value &= 0xFFFFFFFF
    return {i for i in range(32) if (value >> i) & 1}


def _predicate_variables(expr: Any) -> set[Variable] | None: 
    """Return exact finite variables needed by an expression when recognizable.

    ``None`` means the GPIO support could not be bounded exactly; callers must
    conservatively fall back to Boolean-independent treatment for that
    predicate. Register/scheduler references are always collected exactly.
    """
    variables: set[Variable] = set()
    unsupported_gpio = False

    def walk(x: Any) -> None: 
        nonlocal unsupported_gpio
        if not isinstance(x, list) or not x: 
            return
        tag = x[0]
        if tag == "REG": 
            variables.add(("REG", str(x[1])))
            return
        if tag == "SCHED_REG": 
            variables.add(("SCHED", str(x[1])))
            return
        if tag == "GPIO_INPUT": 
            unsupported_gpio = True
            return
        if tag == "BIT_VALUE": 
            base, bit = x[1], int(x[2])
            if isinstance(base, list) and base and base[0] == "GPIO_INPUT": 
                variables.add(("GPIO", bit))
                return
            walk(base)
            return
        if tag == "OP" and len(x) >= 3 and str(x[1]) == "AND" and len(x[2]) == 2: 
            a, b = x[2]
            if isinstance(a, list) and a and a[0] == "GPIO_INPUT" and isinstance(b, list) and b and b[0] == "CONST": 
                variables.update(("GPIO", bit) for bit in _gpio_mask_bits(int(b[1])))
                return
            if isinstance(b, list) and b and b[0] == "GPIO_INPUT" and isinstance(a, list) and a and a[0] == "CONST": 
                variables.update(("GPIO", bit) for bit in _gpio_mask_bits(int(a[1])))
                return
        for child in x[1:]: 
            if isinstance(child, list): 
                if child and isinstance(child[0], list): 
                    for item in child: 
                        walk(item)
                else: 
                    walk(child)

    walk(expr)
    return None if unsupported_gpio else variables


def _scheduler_domain(ir: dict[str, Any], sid: str) -> tuple[int, ...] | None: 
    row = next((x for x in ir.get("scheduler_owned_sources", []) if str(x.get("id")) == sid), None)
    if row is None: 
        return None
    values: set[int] = set()
    init = row.get("initial_value")
    if isinstance(init, list) and len(init) >= 2 and init[0] == "CONST": 
        values.add(int(init[1]))
    maintenance = row.get("maintenance")
    if maintenance == "CAPTURE_GPIO_INPUT": 
        values.update((0, 1))
    elif maintenance == "EVENT_PHASE_AUTOMATON": 
        for det in ir.get("scheduler_detectors", []): 
            if str(det.get("phase_state")) == sid: 
                if "phase_before" in det: 
                    values.add(int(det["phase_before"]))
                if "phase_after" in det: 
                    values.add(int(det["phase_after"]))
    return tuple(sorted(values)) if values else None


def _variable_domains(ir: dict[str, Any]) -> dict[Variable, tuple[int, ...]]: 
    out: dict[Variable, tuple[int, ...]] = {}
    plan = ir.get("storage_optimization") or {}
    for row in plan.get("register_storage", []): 
        rid = str(row["register"])
        values = row.get("reachable_values_upper_bound")
        if values is None: 
            values = row.get("reachable_values")
        if values is not None: 
            out[("REG", rid)] = tuple(sorted({int(v) & 0xFFFFFFFF for v in values}))
    # Safe full-domain fallback for small recovered registers.
    widths = {str(r["id"]): int(r["width"]) for r in ir.get("architectural_registers", [])}
    for rid, width in widths.items(): 
        if ("REG", rid) not in out and 0 <= width <= 10: 
            out[("REG", rid)] = tuple(range(1 << width))
    for row in ir.get("scheduler_owned_sources", []): 
        sid = str(row["id"])
        vals = _scheduler_domain(ir, sid)
        if vals is not None: 
            out[("SCHED", sid)] = vals
    return out


@dataclass
class SemanticPredicateComponent: 
    predicates: tuple[str, ...]
    variables: tuple[Variable, ...]
    truth_patterns: tuple[tuple[tuple[str, bool], ...], ...] | None
    assignment_count: int
    fallback_independent: bool


class SemanticCubeDomain: 
    def __init__(self, ir: dict[str, Any], *, max_component_assignments: int = 250_000): 
        self.ir = ir
        self.aliases = _canonical_basis_aliases(ir)
        by_id = {str(p["id"]): p["expression"] for p in ir.get("predicate_basis", [])}
        # Canonical expression is evaluated once; exact duplicate bases share it.
        self.pred_expr: dict[str, Any] = {}
        for bid, expr in by_id.items(): 
            self.pred_expr.setdefault(self.aliases[bid], expr)
        self.var_domains = _variable_domains(ir)
        pred_vars: dict[str, set[Variable] | None] = {
            bid: _predicate_variables(expr) for bid, expr in self.pred_expr.items()
        }

        supported = {p: vs for p, vs in pred_vars.items() if vs is not None and all(v[0] == "GPIO" or v in self.var_domains for v in vs)}
        opaque = set(pred_vars) - set(supported)

        # Connect finite predicates through shared underlying variables.
        var_adj: dict[Variable, set[Variable]] = defaultdict(set)
        all_vars: set[Variable] = set()
        for vs in supported.values(): 
            all_vars |= set(vs)
            for a in vs: 
                var_adj[a] |= set(vs) - {a}

        raw_components: list[set[Variable]] = []
        seen: set[Variable] = set()
        for start in sorted(all_vars, key = repr): 
            if start in seen: 
                continue
            stack = [start]
            comp: set[Variable] = set()
            while stack: 
                item = stack.pop()
                if item in seen: 
                    continue
                seen.add(item)
                comp.add(item)
                stack.extend(var_adj[item] - seen)
            raw_components.append(comp)

        components: list[SemanticPredicateComponent] = []
        assigned_predicates: set[str] = set()
        for variables in raw_components: 
            pids = tuple(sorted(p for p, vs in supported.items() if vs is not None and vs <= variables))
            if not pids: 
                continue
            domains: list[tuple[int, ...]] = []
            count = 1
            for var in sorted(variables, key = repr): 
                vals = (0, 1) if var[0] == "GPIO" else self.var_domains[var]
                domains.append(tuple(vals))
                count *= len(vals)
            if count > max_component_assignments: 
                components.append(SemanticPredicateComponent(pids, tuple(sorted(variables, key = repr)), None, count, True))
                assigned_predicates.update(pids)
                continue

            truths: set[tuple[tuple[str, bool], ...]] = set()
            vars_sorted = tuple(sorted(variables, key = repr))
            for values in product(*domains): 
                regs: dict[str, int] = {}
                sched: dict[str, int] = {}
                gpio = 0
                for var, value in zip(vars_sorted, values): 
                    kind, name = var
                    if kind == "REG": 
                        regs[str(name)] = int(value)
                    elif kind == "SCHED": 
                        sched[str(name)] = int(value)
                    elif kind == "GPIO": 
                        gpio |= (int(value) & 1) << int(name)
                signature = tuple((pid, bool(_eval_expr(self.pred_expr[pid], regs, sched, gpio))) for pid in pids)
                truths.add(signature)
            components.append(SemanticPredicateComponent(pids, vars_sorted, tuple(sorted(truths, key = repr)), count, False))
            assigned_predicates.update(pids)

        # Optional cross-register joint control domain.  This metadata is
        # produced by a reset-started, feasibility-transition fixed-point
        # analysis and therefore may safely replace the independent finite
        # components for predicates wholly contained in that joint domain.
        # Refuse partial component replacement: losing a predicate that shares
        # a variable component would make the satisfiability model weaker in a
        # way that is hard to audit.
        joint = ir.get("joint_control_domain")
        if joint: 
            joint_pids = tuple(sorted({self.aliases.get(str(pid), str(pid)) for pid in joint.get("predicate_ids", [])}))
            joint_set = set(joint_pids)
            if joint_pids: 
                retained: list[SemanticPredicateComponent] = []
                removed: set[str] = set()
                for comp in components: 
                    overlap = set(comp.predicates) & joint_set
                    if not overlap: 
                        retained.append(comp)
                        continue
                    outside = set(comp.predicates) - joint_set
                    if outside: 
                        raise ValueError(
                            "joint control predicate domain partially overlaps an existing semantic component: "
                            f"joint={sorted(overlap)} outside={sorted(outside)}"
                        )
                    removed.update(comp.predicates)
                missing = joint_set - removed
                if missing: 
                    raise ValueError(f"joint control predicate ids not present in finite semantic components: {sorted(missing)}")
                truth_set: set[tuple[tuple[str, bool], ...]] = set()
                for raw_sig in joint.get("truth_patterns", []): 
                    row: dict[str, bool] = {}
                    for lit in raw_sig: 
                        pid = self.aliases.get(str(lit["basis"]), str(lit["basis"]))
                        if pid not in joint_set: 
                            continue
                        value = bool(lit["polarity"])
                        if pid in row and row[pid] != value: 
                            raise ValueError(f"contradictory joint truth pattern for {pid}")
                        row[pid] = value
                    if set(row) != joint_set: 
                        raise ValueError("joint truth pattern does not cover every joint predicate")
                    truth_set.add(tuple(sorted(row.items())))
                if not truth_set: 
                    raise ValueError("joint control predicate domain has no truth patterns")
                tracked = tuple(("REG", str(r)) for r in joint.get("tracked_registers", []))
                retained.append(
                    SemanticPredicateComponent(
                        joint_pids, 
                        tracked, 
                        tuple(sorted(truth_set, key = repr)), 
                        int(joint.get("reachable_states", len(truth_set))), 
                        False, 
                    )
                )
                components = retained
                assigned_predicates -= joint_set
                assigned_predicates |= joint_set

        # Constant/no-variable predicates can be evaluated directly.  Truly
        # opaque predicates are independent Boolean variables.
        for pid in sorted(set(self.pred_expr) - assigned_predicates): 
            vs = pred_vars[pid]
            if vs == set(): 
                value = bool(_eval_expr(self.pred_expr[pid], {}, {}, 0))
                truths = (((pid, value),),)
                components.append(SemanticPredicateComponent((pid,), (), truths, 1, False))
            else: 
                components.append(SemanticPredicateComponent((pid,), tuple(sorted(vs or (), key = repr)), None, 0, True))

        self.components = components
        self.pid_to_component: dict[str, int] = {}
        for ci, comp in enumerate(components): 
            for pid in comp.predicates: 
                self.pid_to_component[pid] = ci
        self._sat_cache: dict[tuple[tuple[str, bool], ...], bool] = {}
        # Precompute finite-component truth sets as Python integer bitmasks.
        # Semantic satisfiability is unchanged; this only replaces repeated
        # linear scans over truth_patterns with bitwise intersections.
        self._component_full_masks: dict[int, int] = {}
        self._component_literal_masks: dict[tuple[int, str, bool], int] = {}
        for ci, comp in enumerate(components): 
            if comp.truth_patterns is None: 
                continue
            full = (1 << len(comp.truth_patterns)) - 1
            self._component_full_masks[ci] = full
            for pi, sig in enumerate(comp.truth_patterns): 
                row = dict(sig)
                bit = 1 << pi
                for pid in comp.predicates: 
                    value = bool(row[pid])
                    key = (ci, pid, value)
                    self._component_literal_masks[key] = self._component_literal_masks.get(key, 0) | bit

    def canonicalize_cube(self, cube: dict[str, bool]) -> dict[str, bool] | None: 
        out: dict[str, bool] = {}
        for pid, value in cube.items(): 
            key = self.aliases.get(str(pid), str(pid))
            if key in out and out[key] != bool(value): 
                return None
            out[key] = bool(value)
        return out

    def satisfiable(self, cube: dict[str, bool]) -> bool: 
        c = self.canonicalize_cube(cube)
        if c is None: 
            return False
        key = _cube_key(c)
        if key in self._sat_cache: 
            return self._sat_cache[key]
        by_component: dict[int, dict[str, bool]] = defaultdict(dict)
        for pid, value in c.items(): 
            ci = self.pid_to_component.get(pid)
            if ci is None: 
                # Unknown basis: independent Boolean fallback.
                continue
            by_component[ci][pid] = value
        for ci, literals in by_component.items(): 
            comp = self.components[ci]
            if comp.truth_patterns is None: 
                continue
            mask = self._component_full_masks[ci]
            for pid, value in literals.items(): 
                mask &= self._component_literal_masks.get((ci, pid, bool(value)), 0)
                if not mask: 
                    self._sat_cache[key] = False
                    return False
        self._sat_cache[key] = True
        return True

    def overlaps(self, a: dict[str, bool], b: dict[str, bool]) -> bool: 
        ca = self.canonicalize_cube(a)
        cb = self.canonicalize_cube(b)
        if ca is None or cb is None: 
            return False
        merged = dict(ca)
        for pid, value in cb.items(): 
            if pid in merged and merged[pid] != value: 
                return False
            merged[pid] = value
        return self.satisfiable(merged)

    def implies(self, specific: dict[str, bool], general: dict[str, bool]) -> bool: 
        """Whether specific => general over the proof-backed domain."""
        s = self.canonicalize_cube(specific)
        g = self.canonicalize_cube(general)
        if s is None: 
            return True
        if g is None: 
            return False
        if not self.satisfiable(s): 
            return True
        for pid, value in g.items(): 
            if pid in s: 
                if s[pid] != value: 
                    return False
                continue
            witness = dict(s)
            witness[pid] = not value
            if self.satisfiable(witness): 
                return False
        return True


@dataclass
class SemanticGuardMinimizationStats: 
    source_rules: int
    source_materialized_rules: int
    source_enable_literals: int
    minimized_rules: int
    minimized_enable_literals: int
    predicate_basis_total: int
    predicate_basis_exact_unique: int
    predicate_basis_used: int
    finite_components: int
    fallback_components: int
    max_component_assignments: int
    max_source_cube_literals: int
    max_minimized_cube_literals: int
    semantic_cross_outcome_overlaps: int


def _remove_semantically_subsumed(cubes: list[dict[str, bool]], domain: SemanticCubeDomain) -> list[dict[str, bool]]: 
    result: list[dict[str, bool]] = []
    for cube in sorted(cubes, key = lambda c: (len(c), _cube_key(c))): 
        # existing covers cube iff cube => existing
        if any(domain.implies(cube, existing) for existing in result): 
            continue
        # cube covers old iff old => cube
        result = [old for old in result if not domain.implies(old, cube)]
        result.append(cube)
    return result


def _generalize(
    source: dict[str, bool], 
    bad_cubes: list[dict[str, bool]], 
    domain: SemanticCubeDomain, 
    order_fn, 
) -> dict[str, bool]: 
    current = dict(source)
    if any(domain.overlaps(current, bad) for bad in bad_cubes): 
        return current
    while current: 
        changed = False
        for key in list(order_fn(current)): 
            trial = dict(current)
            del trial[key]
            if any(domain.overlaps(trial, bad) for bad in bad_cubes): 
                continue
            current = trial
            changed = True
            break
        if not changed: 
            break
    return current


def _candidates(
    source: dict[str, bool], 
    bad_cubes: list[dict[str, bool]], 
    domain: SemanticCubeDomain, 
    good_frequency: dict[str, int], 
    bad_frequency: dict[str, int], 
) -> list[dict[str, bool]]: 
    insertion = list(source)
    orders = [
        lambda c: [k for k in insertion if k in c], 
        lambda c: sorted(c), 
        lambda c: sorted(c, reverse = True), 
        lambda c: sorted(c, key = lambda k: (bad_frequency.get(k, 0), k)), 
        lambda c: sorted(c, key = lambda k: (-good_frequency.get(k, 0), k)), 
    ]
    unique: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
    for order in orders: 
        cube = _generalize(source, bad_cubes, domain, order)
        unique[_cube_key(cube)] = cube
    return list(unique.values())


def _select_cover(
    candidates: list[dict[str, bool]], 
    source_cubes: list[dict[str, bool]], 
    domain: SemanticCubeDomain, 
) -> list[dict[str, bool]]: 
    primes = _remove_semantically_subsumed(candidates, domain)
    cover_sets = [
        {i for i, src in enumerate(source_cubes) if domain.implies(src, cube)}
        for cube in primes
    ]
    uncovered = set(range(len(source_cubes)))
    chosen: list[int] = []
    while uncovered: 
        best = None
        for i, covered in enumerate(cover_sets): 
            newly = len(covered & uncovered)
            if not newly: 
                continue
            score = (newly, -len(primes[i]), tuple(reversed(_cube_key(primes[i]))))
            if best is None or score > best[0]: 
                best = (score, i)
        if best is None: 
            raise ValueError("semantic guard candidates do not cover same-outcome source cubes")
        idx = best[1]
        chosen.append(idx)
        uncovered -= cover_sets[idx]
    changed = True
    while changed: 
        changed = False
        for pos in range(len(chosen) - 1, -1, -1): 
            trial = chosen[:pos] + chosen[pos + 1 :]
            if all(any(i in cover_sets[j] for j in trial) for i in range(len(source_cubes))): 
                chosen = trial
                changed = True
                break
    return [primes[i] for i in chosen]


def minimize_dedicated_event_semantic_guards(
    complete_ir: dict[str, Any], 
    *, 
    max_component_assignments: int = 250_000, 
) -> tuple[dict[str, Any], SemanticGuardMinimizationStats]: 
    """Minimize a complete relation that still contains HOLD rows.

    ``complete_ir`` should already carry the proof-backed storage value domains
    and any expression simplification intended for emitted outcomes.
    """
    source_rules = list(complete_ir.get("update_rules", []))
    if not source_rules: 
        raise ValueError("Dedicated Event IR has no update_rules")
    domain = SemanticCubeDomain(complete_ir, max_component_assignments = max_component_assignments)
    aliases = domain.aliases

    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    cubes: dict[str, dict[str, bool]] = {}
    for rule in source_rules: 
        c = _canonical_cube(rule, aliases)
        if c is None: 
            # An impossible duplicate-predicate contradiction is an infeasible
            # source row and should never survive FSE materialization.
            raise ValueError(f"source rule has contradictory equivalent predicates: {rule.get('rule_id')}")
        cubes[str(rule["rule_id"])] = c
        by_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)

    output: list[dict[str, Any]] = []
    for (event, target), rules in sorted(by_pair.items()): 
        by_outcome: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for rule in rules: 
            by_outcome[_outcome_key(rule)].append(rule)
        for outcome_key, good_rules in sorted(by_outcome.items(), key = lambda kv: repr(kv[0])): 
            if outcome_key == _hold_key(target): 
                continue
            bad_cubes = [cubes[str(r["rule_id"])] for r in rules if _outcome_key(r) != outcome_key]
            good_cubes = [cubes[str(r["rule_id"])] for r in good_rules]
            good_frequency = Counter(k for cube in good_cubes for k in cube)
            bad_frequency = Counter(k for cube in bad_cubes for k in cube)
            candidate_map: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
            for cube in good_cubes: 
                for candidate in _candidates(cube, bad_cubes, domain, good_frequency, bad_frequency): 
                    candidate_map[_cube_key(candidate)] = candidate
            implicants = _select_cover(list(candidate_map.values()), good_cubes, domain)
            exemplar = good_rules[0]
            for cube in implicants: 
                tids: list[str] = []
                for src in good_rules: 
                    if domain.implies(cubes[str(src["rule_id"])], cube): 
                        tids.extend(str(x) for x in src.get("source_transition_ids", []))
                row = deepcopy(exemplar)
                row["enable"] = [
                    {"basis": basis, "polarity": polarity} for basis, polarity in _cube_key(cube)
                ]
                row["materialize"] = True
                row["source_transition_ids"] = sorted(set(tids))
                output.append(row)

    output.sort(key = lambda r: (str(r["event_class"]), str(r["target"]), repr(r["outcome"]), repr(r["enable"])))
    for i, rule in enumerate(output): 
        rule["rule_id"] = f"S{i:04d}"

    # Different outcomes may overlap in raw Boolean space only when the overlap
    # is impossible in the proof-backed state domain.  Check the semantic domain
    # explicitly rather than depending on priority order.
    by_out_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rule in output: 
        by_out_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)
    cross = 0
    examples: list[tuple[str, str, str, str]] = []
    for (event, target), rules in by_out_pair.items(): 
        for i, a in enumerate(rules): 
            for b in rules[i + 1 :]: 
                if _outcome_key(a) == _outcome_key(b): 
                    continue
                if domain.overlaps(_cube(a), _cube(b)): 
                    cross += 1
                    if len(examples) < 8: 
                        examples.append((event, target, str(a["rule_id"]), str(b["rule_id"])))
    if cross: 
        raise ValueError(f"semantic guard minimization created feasible cross-outcome overlaps: {examples}")

    out = deepcopy(complete_ir)
    out["version"] = str(complete_ir.get("version", "dedicated-event-ir")) + "+semguard-v1"
    out["update_rules"] = output
    used = sorted({str(l["basis"]) for r in output for l in r.get("enable", [])})
    stats = SemanticGuardMinimizationStats(
        source_rules = len(source_rules), 
        source_materialized_rules = sum(bool(r.get("materialize")) for r in source_rules), 
        source_enable_literals = sum(len(r.get("enable", [])) for r in source_rules if r.get("materialize")), 
        minimized_rules = len(output), 
        minimized_enable_literals = sum(len(r.get("enable", [])) for r in output), 
        predicate_basis_total = len(complete_ir.get("predicate_basis", [])), 
        predicate_basis_exact_unique = len(set(domain.aliases.values())), 
        predicate_basis_used = len(used), 
        finite_components = sum(c.truth_patterns is not None for c in domain.components), 
        fallback_components = sum(c.fallback_independent for c in domain.components), 
        max_component_assignments = max((c.assignment_count for c in domain.components), default = 0), 
        max_source_cube_literals = max((len(cubes[str(r["rule_id"])]) for r in source_rules if r.get("materialize")), default = 0), 
        max_minimized_cube_literals = max((len(r.get("enable", [])) for r in output), default = 0), 
        semantic_cross_outcome_overlaps = cross, 
    )
    out["semantic_guard_minimization"] = {
        "version": 1, 
        "proof_model": "PREDICATE_COMPONENT_FINITE_DOMAIN_CARTESIAN_OVERAPPROX", 
        "complete_source_relation": True, 
        "hold_rows_used_as_offset": True, 
        "outside_source_relation": "DONT_CARE_ONLY_WITHIN_PROVEN_STATE_DOMAIN", 
        "stats": asdict(stats), 
        "components": [asdict(c) for c in domain.components], 
        "notes": [
            "Predicate relations are derived only from proof-backed per-register value domains and exact predicate expressions.", 
            "Finite connected predicate components are exhaustively enumerated; large or unsupported components fall back to independent Boolean semantics.", 
            "HOLD rows remain the OFF-set and are never emitted as update rules.", 
            "A generalized guard is rejected if it has any feasible overlap with a different source outcome over the proof-backed Cartesian domain.", 
            "No protocol name, GPIO number, source stack-state name, basic-block identity, or transition identity is used to decide a rewrite.", 
        ], 
    }
    out.setdefault("notes", []).append(
        "Dedicated update guards were re-minimized with exact finite-domain relationships among predicate-basis values; unsupported components conservatively remain Boolean-independent."
    )
    return out, stats
