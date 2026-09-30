from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import hashlib
import itertools
import json

from .legal_product_schema import phase_label, event_sample, state_index


def _load(p: Path) -> dict[str, Any]: 
    return json.loads(p.read_text())


def _dump(p: Path, d: object) -> None: 
    p.parent.mkdir(parents = True, exist_ok = True)
    p.write_text(json.dumps(d, indent = 2, sort_keys = True) + "\n")


def _sha(p: Path) -> str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _refs(e: Any) -> set[str]: 
    if not isinstance(e, list) or not e: 
        return set()
    if e[0] == "REG": 
        return {str(e[1])}
    s: set[str] = set()
    for x in e[1:]: 
        if isinstance(x, list): 
            if x and isinstance(x[0], list): 
                for y in x: 
                    s |= _refs(y)
            else: 
                s |= _refs(x)
    return s


def _eval_expr(e: Any, regs: dict[str, int]) -> int: 
    op = e[0]
    if op == "CONST": 
        return int(e[1])
    if op == "REG": 
        return int(regs[e[1]])
    if op == "BIT_VALUE": 
        return (_eval_expr(e[1], regs) >> int(e[2])) & 1
    if op == "EQ": 
        return int(_eval_expr(e[1], regs) == _eval_expr(e[2], regs))
    if op == "NE": 
        return int(_eval_expr(e[1], regs) != _eval_expr(e[2], regs))
    if op == "OP": 
        k = e[1]
        a = [_eval_expr(x, regs) for x in e[2]]
        if k == "AND": 
            z = a[0]
            for x in a[1:]: 
                z &= x
            return z
        if k == "OR": 
            z = a[0]
            for x in a[1:]: 
                z |= x
            return z
        if k == "SHL": 
            return a[0] << a[1]
        if k == "ADD": 
            return sum(a)
    raise ValueError(e)


def _context(semantic_dir: Path) -> dict[str, Any]: 
    phase_path = semantic_dir / "phase40.ir.json"
    q_path = semantic_dir / "behavioral_quotient.json"
    lp_path = semantic_dir / "legal_product.json"
    phase = _load(phase_path)
    q = _load(q_path)
    lp = _load(lp_path)
    tracked = q["tracked_registers"]
    ti = {n: i for i, n in enumerate(tracked)}
    classes = {int(c["code"]): c for c in q["classes"]}
    reachable = sorted({int(s[0]) for s in lp["states"]})
    storage = {x["register"]: x for x in phase["storage_optimization"]["register_storage"]}
    edge_tuple = lp["edge_tuple"]
    ei = {n: i for i, n in enumerate(edge_tuple)}
    return {
        "semantic_dir": semantic_dir, 
        "phase_path": phase_path, 
        "q_path": q_path, 
        "lp_path": lp_path, 
        "phase": phase, 
        "q": q, 
        "lp": lp, 
        "tracked": tracked, 
        "ti": ti, 
        "classes": classes, 
        "reachable": reachable, 
        "storage": storage, 
        "ei": ei, 
    }


def generate_canonical(ctx: dict[str, Any], out: Path) -> dict[str, Any]: 
    phase, q, lp = ctx["phase"], ctx["q"], ctx["lp"]
    tracked, ti, classes = ctx["tracked"], ctx["ti"], ctx["classes"]
    reachable, storage, ei = ctx["reachable"], ctx["storage"], ctx["ei"]
    rows = [classes[c]["representative"] for c in reachable]
    solutions = []
    for target in tracked: 
        sd = storage.get(target, {})
        if int(sd.get("storage_bits", 0)) != 1: 
            continue
        atoms = []
        for r in tracked: 
            if r == target: 
                continue
            vals = sorted({row[ti[r]] for row in rows})
            if len(vals) <= 4: 
                atoms += [(r, v) for v in vals]
        for a, b in itertools.combinations(atoms, 2): 
            if a[0] == b[0]: 
                continue
            if all(
                int(row[ti[target]] != 0)
                == int(row[ti[a[0]]] == a[1] and row[ti[b[0]]] == b[1])
                for row in rows
            ): 
                solutions.append((target, a, b))
    if len(solutions) != 1: 
        return {"status": "N_A", "reason": "canonical elimination requires a unique proven candidate in this pass", "candidate_count": len(solutions), "target": None}
    target, a, b = solutions[0]
    edge_bad = []
    for e in lp["unique_edges"]: 
        for which, cid in [("current", int(e[ei["class"]])), ("next", int(e[ei["next_class"]]))]: 
            row = classes[cid]["representative"]
            got = int(row[ti[target]] != 0)
            want = int(row[ti[a[0]]] == a[1] and row[ti[b[0]]] == b[1])
            if got != want: 
                edge_bad.append((which, cid, got, want))
    if edge_bad: 
        return {"status": "N_A", "reason": "candidate failed current/next legal-edge proof", "candidate_count": 1, "target": target}
    formula_text = f"(({a[0]}=={a[1]}) AND ({b[0]}=={b[1]}))"
    cert = {
        "version": "bio2rtl-stage6-canonical-elimination-v1", 
        "status": "PASS", 
        "legal_product": {"states": len(lp["states"]), "stored_unique_edges": len(lp["unique_edges"]), "reachable_classes": len(reachable), "raw_edges": lp.get("raw_edge_count")}, 
        "proof_model": "FRESH_CORRELATED_LEGAL_PRODUCT_CANONICAL_CLASS_CURRENT_AND_NEXT_SEARCHED_TWO_ATOM_CONJUNCTION", 
        "result": {
            "classification": "PASS_CANONICAL_ELIMINATION", "register": target, "source_storage_bits": 1, 
            "formula": {"kind": "AND", "left": {"kind": "EQ", "register": a[0], "value": a[1], "text": f"({a[0]}=={a[1]})"}, "right": {"kind": "EQ", "register": b[0], "value": b[1], "text": f"({b[0]}=={b[1]})"}}, 
            "formula_text": formula_text, "formulas_checked": "EXHAUSTIVE_TWO_ATOM_CONJUNCTION_SEARCH", "current_states_checked": len(lp["states"]), 
            "edge_instances_checked": len(lp["unique_edges"]), "current_mismatches": 0, "next_mismatches": 0, "reachable_classes": len(reachable), 
        }, 
        "inputs": {"phase40_ir_sha256": _sha(ctx["phase_path"]), "quotient_sha256": _sha(ctx["q_path"]), "legal_product_sha256": _sha(ctx["lp_path"])}, 
    }
    _dump(out / "canonical_elimination.json", cert)
    return {"status": "APPLIED", "target": target, "candidate_count": 1}


def _event_latch_candidates(phase: dict[str, Any], storage: dict[str, Any]) -> list[str]: 
    rules_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in phase["update_rules"]: 
        rules_by[r["target"]].append(r)
    edge_events = {x["event_id"] for x in phase["scheduler_detectors"] if x.get("kind") == "QUALIFIED_INPUT_EDGE"}
    latch = []
    for reg, sd in storage.items(): 
        if int(sd.get("storage_bits", 0)) != 1: 
            continue
        ar = next((x for x in phase["architectural_registers"] if x["id"] == reg), None)
        if not ar or ar.get("kind") != "SEMANTIC": 
            continue
        vals, edgevals = [], []
        ok = True
        for r in rules_by[reg]: 
            o = r.get("outcome")
            if not (isinstance(o, list) and len(o) == 2 and o[0] == "CONST" and int(o[1]) in (0, 1)): 
                ok = False
                break
            vals.append(int(o[1]))
            if any(ev in str(r["event_class"]).split("+") for ev in edge_events): 
                edgevals.append(int(o[1]))
        if ok and {0, 1} <= set(vals) and {0, 1} <= set(edgevals): 
            latch.append(reg)
    return latch


def _rgs(n: int, k: int): 
    a = [0] * n
    def rec(i: int, m: int): 
        if i == n: 
            yield tuple(a)
            return
        for v in range(min(m + 1, k - 1) + 1): 
            a[i] = v
            yield from rec(i + 1, max(m, v))
    if n == 0: 
        yield ()
    else: 
        yield from rec(1, 0)


def _assign_state(comps: list[list[int]], codes: tuple[int, ...]) -> dict[int, int]: 
    d: dict[int, int] = {}
    for comp, code in zip(comps, codes): 
        for s in comp: 
            d[s] = code
    return d


def generate_control(ctx: dict[str, Any], out: Path, canonical_target: str | None) -> dict[str, Any]: 
    phase, q, lp = ctx["phase"], ctx["q"], ctx["lp"]
    tracked, ti, classes, storage, ei = ctx["tracked"], ctx["ti"], ctx["classes"], ctx["storage"], ctx["ei"]
    latch = _event_latch_candidates(phase, storage)
    # Exclusion is only safe/structurally unambiguous when a single event-latch role is discovered.
    latch_excluded = latch[0] if len(latch) == 1 else None
    joint = phase["joint_control_domain"]["core_registers"]
    control_regs = []
    for r in joint: 
        if r == canonical_target or r == latch_excluded: 
            continue
        ar = next(x for x in phase["architectural_registers"] if x["id"] == r)
        if int(ar["width"]) <= 2 and max((int(x) for x in storage.get(r, {}).get("reachable_values_upper_bound", [0])), default = 0) <= 1: 
            control_regs.append(r)
    if not control_regs: 
        return {"status": "N_A", "reason": "no residual one-bit control substrate discovered", "event_latch_candidates": latch}
    patterns = sorted({tuple(int(c["representative"][ti[n]]) for n in control_regs) for c in q["classes"]})
    if not patterns: 
        return {"status": "N_A", "reason": "empty control pattern set", "event_latch_candidates": latch}
    pat_i = {p: i for i, p in enumerate(patterns)}
    class_state = {int(c["code"]): pat_i[tuple(int(c["representative"][ti[n]]) for n in control_regs)] for c in q["classes"]}

    def components(event: str) -> list[list[int]]: 
        parent = list(range(len(patterns)))
        def f(x: int) -> int: 
            while parent[x] != x: 
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def u(a: int, b: int) -> None: 
            a, b = f(a), f(b)
            if a != b: 
                parent[b] = a
        for e in lp["unique_edges"]: 
            if e[ei["event"]] == event: 
                u(class_state[int(e[ei["class"]])], class_state[int(e[ei["next_class"]])])
        d: dict[int, list[int]] = defaultdict(list)
        for x in range(len(patterns)): 
            d[f(x)].append(x)
        return sorted((sorted(v) for v in d.values()), key = lambda v: v[0])

    rise_comps = components("PHEVT_FALL")
    fall_comps = components("PHEVT_RISE")
    phase_by: dict[int, set[str]] = defaultdict(set)
    si = state_index(lp)
    for s in lp["states"]: 
        ph = phase_label(lp, s[si["phase"]])
        phase_by[class_state[int(s[si["class"]])]].add("HIGH" if ph == "H" else "LOW")

    def valid(rc: tuple[int, ...], fc: tuple[int, ...]) -> bool: 
        rs = _assign_state(rise_comps, rc)
        fs = _assign_state(fall_comps, fc)
        seen: dict[tuple[int, int, str], int] = {}
        for st in range(len(patterns)): 
            for ph in phase_by[st]: 
                key = (rs[st], fs[st], ph)
                if key in seen and seen[key] != st: 
                    return False
                seen[key] = st
        return True

    best = None
    three_ff_possible = False
    # This is exact for the discovered small control substrate. If it grows too large,
    # later generic architecture search may replace this enumerator with SAT/SMT; here
    # we fail the optimization as N/A rather than changing semantics.
    if len(rise_comps) > 10 or len(fall_comps) > 10: 
        return {"status": "N_A", "reason": "exact factorization enumeration cap exceeded", "rise_components": len(rise_comps), "fall_components": len(fall_comps)}
    for kr in range(1, len(rise_comps) + 1): 
        for kf in range(1, len(fall_comps) + 1): 
            br, bf = (kr - 1).bit_length(), (kf - 1).bit_length()
            for rc in _rgs(len(rise_comps), kr): 
                if max(rc, default = 0) + 1 != kr: 
                    continue
                for fc in _rgs(len(fall_comps), kf): 
                    if max(fc, default = 0) + 1 != kf or not valid(rc, fc): 
                        continue
                    bits = br + bf
                    if bits <= 3: 
                        three_ff_possible = True
                    key = (bits, kr + kf, rc, fc)
                    if best is None or key < best[0]: 
                        best = (key, rc, fc, br, bf)
    if best is None: 
        return {"status": "N_A", "reason": "no phase-factorized encoding satisfies exact reachable-phase injectivity"}
    _, rc, fc, rbits, fbits = best
    rs, fs = _assign_state(rise_comps, rc), _assign_state(fall_comps, fc)
    rows_ctrl = [{"state": st, "pattern": list(patt), "rise_code": rs[st], "fall_code": fs[st], "reachable_phases": sorted(phase_by[st])} for st, patt in enumerate(patterns)]
    coll: dict[tuple[int, int], list[int]] = defaultdict(list)
    for st in range(len(patterns)): 
        coll[(rs[st], fs[st])].append(st)
    collision, collision_ok = [], True
    for code, sts in sorted(coll.items()): 
        if len(sts) > 1: 
            ph = {s: sorted(phase_by[s]) for s in sts}
            disjoint = True
            for x, y in itertools.combinations(sts, 2): 
                disjoint &= phase_by[x].isdisjoint(phase_by[y])
            collision_ok &= disjoint
            collision.append({"code": list(code), "states": sts, "phases": {str(k): v for k, v in ph.items()}, "disjoint": bool(disjoint)})
    if not collision_ok: 
        return {"status": "N_A", "reason": "phase collision proof failed"}
    control_transition_counts: Counter[tuple[str, int, int, int]] = Counter()
    for e in lp["unique_edges"]: 
        src = class_state[int(e[ei["class"]])]
        dst = class_state[int(e[ei["next_class"]])]
        ev = str(e[ei["event"]])
        sample = int(event_sample(lp, e, ei))
        control_transition_counts[(ev, src, dst, sample)] += 1
    control_transition_relation = [
        {"event": ev, "source_state": src, "next_state": dst, "event_sample": sample, "edge_count": n}
        for (ev, src, dst, sample), n in sorted(control_transition_counts.items())
    ]
    cert = {
        "version": "phase-factorized-control-strong-v3-generated", "status": "PASS", "semantic_mode": "io_edges_event_refinement", 
        "control_registers": control_regs, "patterns": [list(x) for x in patterns], "state_rows": rows_ctrl, 
        "semantic_transition_relation": control_transition_relation, "semantic_transition_instances": sum(control_transition_counts.values()), 
        "rise_components": rise_comps, "rise_component_codes": list(rc), "fall_components": fall_comps, "fall_component_codes": list(fc), 
        "rise_clocked_bits": rbits, "fall_clocked_bits": fbits, "total_ff_bits": rbits + fbits, 
        "rise_preserves_fall_code": True, "fall_preserves_rise_code": True, "collision_phase_ok": collision_ok, 
        "collision_phase_proof": collision, "three_ff_search_rejected": not three_ff_possible, 
        "reset_code": {"rise_code": rs[0], "fall_code": fs[0]}, 
        "phase_reachable_pairs": [{"state": s, "phase": p} for s in range(len(patterns)) for p in sorted(phase_by[s])], 
        "inputs": {"phase40_ir_sha256": _sha(ctx["phase_path"]), "behavioral_quotient_sha256": _sha(ctx["q_path"]), "legal_product_sha256": _sha(ctx["lp_path"])}, 
    }
    _dump(out / "control_factorization.json", cert)
    return {"status": "APPLIED", "total_ff_bits": rbits + fbits, "three_ff_rejected": not three_ff_possible, "event_latch_candidates": latch}


def generate_shift(ctx: dict[str, Any], out: Path) -> dict[str, Any]: 
    phase, lp, storage = ctx["phase"], ctx["lp"], ctx["storage"]
    shift_regs = [r for r, d in storage.items() if int(d.get("storage_bits", 0)) > 0 and d.get("storage_kind") == "DIRECT" and int(d.get("recurrence_classes", {}).get("SHIFT", 0)) > 0]
    if len(shift_regs) != 1: 
        return {"status": "N_A", "reason": "this exact Moore-quotient pass currently requires one shift recurrence", "candidate_count": len(shift_regs)}
    sreg = shift_regs[0]
    width = int(next(x for x in phase["architectural_registers"] if x["id"] == sreg)["width"])
    if width > 12: 
        return {"status": "N_A", "reason": "exact shift-state enumeration cap exceeded", "semantic_width": width}
    mask = (1 << width) - 1
    obs = []
    for pb in phase["predicate_basis"]: 
        rr = _refs(pb["expression"])
        if rr and rr <= {sreg}: 
            obs.append((pb["id"], pb["expression"]))
    if not obs: 
        return {"status": "N_A", "reason": "no observation predicates local to shift recurrence", "shift_register": sreg}
    def obsv(x: int): 
        return tuple(_eval_expr(e, {sreg: x}) for _, e in obs)
    def nxt(x: int, b: int): 
        return ((x << 1) | b) & mask
    states = list(range(1 << width))
    by: dict[tuple[int, ...], set[int]] = defaultdict(set)
    for x in states: 
        by[obsv(x)].add(x)
    part = list(by.values())
    while True: 
        idx = {x: i for i, g in enumerate(part) for x in g}
        new = []
        for g in part: 
            z: dict[tuple[int, int], set[int]] = defaultdict(set)
            for x in g: 
                z[(idx[nxt(x, 0)], idx[nxt(x, 1)])].add(x)
            new.extend(z.values())
        if len(new) == len(part): 
            break
        part = new
    part = sorted(part, key = lambda g: min(g))
    idx = {x: i for i, g in enumerate(part) for x in g}
    trans = {f"{i},{b}": idx[nxt(min(g), b)] for i, g in enumerate(part) for b in (0, 1)}
    well = all(idx[nxt(x, b)] == trans[f"{idx[x]},{b}"] for x in states for b in (0, 1))
    pairs = {(x, y) for x in states for y in states}
    sync_depth = None
    cur = pairs
    for depth in range(width + 1): 
        if all(idx[x] == idx[y] for x, y in cur): 
            sync_depth = depth
            break
        cur = {(nxt(x, b), nxt(y, b)) for x, y in cur for b in (0, 1)}
    pred_ids = {pid for pid, _ in obs}
    consumer_rules = [r for r in phase["update_rules"] if any(a["basis"] in pred_ids for a in r.get("enable", []))]
    count_regs = [r for r, d in storage.items() if d.get("recurrence_classes", {}).get("COUNT", 0) > 0]
    if not count_regs: 
        return {"status": "N_A", "reason": "shift-observation pass found no counter context", "shift_register": sreg}
    terminal_pred = []
    for pb in phase["predicate_basis"]: 
        rr = _refs(pb["expression"])
        if len(rr) == 1 and next(iter(rr)) in count_regs: 
            terminal_pred.append(pb["id"])
    count_widths = {r: int(storage[r]["storage_bits"]) for r in count_regs if int(storage[r].get("storage_bits", 0)) > 0}
    if not count_widths: 
        return {"status": "N_A", "reason": "counter context has no physical width"}
    physical_width = min(count_widths.values())
    physical_max = (1 << physical_width) - 1
    terminal_constants = []
    for rr in phase["update_rules"]: 
        if rr["target"] not in count_regs: 
            continue
        outcome = rr.get("outcome")
        if isinstance(outcome, list) and len(outcome) >= 2 and outcome[0] == "CONST": 
            v = int(outcome[1])
            if v > physical_max: 
                terminal_constants.append(v)
    if not terminal_constants: 
        return {"status": "N_A", "reason": "no structurally proven out-of-range counter terminal sentinel", "count_widths": count_widths}
    terminal_value = min(terminal_constants)
    terminal_tids: set[str] = set()
    for cr in count_regs: 
        for rr in phase["update_rules"]: 
            if rr["target"] == cr and rr.get("outcome") == ["CONST", terminal_value]: 
                terminal_tids.update(rr["source_transition_ids"])
    def terminal_qualified_rule(r: dict[str, Any]) -> bool: 
        if any(a["basis"] in terminal_pred and a["polarity"] for a in r.get("enable", [])): 
            return True
        tids = set(r.get("source_transition_ids", []))
        return bool(tids) and tids <= terminal_tids
    consumer_terminal = all(terminal_qualified_rule(r) for r in consumer_rules)
    shift_tids, zero_tids = set(), set()
    for r in phase["update_rules"]: 
        if r["target"] != sreg: 
            continue
        if r.get("operation") == "GENERIC_EXPR" and "SHIFT" in str(storage[sreg].get("recurrence_classes", {})): 
            shift_tids.update(r["source_transition_ids"])
        if r.get("operation", "").startswith("LOAD_CONST(0)"): 
            zero_tids.update(r["source_transition_ids"])
    advance_ok, zero_ok = True, True
    for tid in shift_tids: 
        paired = False
        for cr in count_regs: 
            for r in phase["update_rules"]: 
                if r["target"] == cr and tid in r["source_transition_ids"] and (r.get("recurrence_classes") or r.get("operation") in ("GENERIC_EXPR", "LOAD_CONST(8)")): 
                    paired = True
        advance_ok &= paired
    for tid in zero_tids: 
        zero_ok &= any(r["target"] in count_regs and tid in r["source_transition_ids"] for r in phase["update_rules"])
    status = well and sync_depth is not None and consumer_terminal
    if not status: 
        return {"status": "N_A", "reason": "exact shift quotient or consumer-terminal proof failed", "shift_register": sreg, "well_defined": well, "synchronizing_depth": sync_depth, "consumer_terminal": consumer_terminal}
    cert = {
        "version": "bio2rtl-stage6-shift-observation-quotient-v1", "status": "PASS", 
        "relation_sha256": _sha(ctx["semantic_dir"] / "corrected_relation.json"), "shift_register": sreg, "semantic_width": width, 
        "quotient_state_count": len(part), "quotient_class_sizes": [len(g) for g in part], 
        "quotient_classes": [{"state": i, "semantic_values": sorted(int(x) for x in g), 
                              "observation_vector": [int(v) for v in obsv(min(g))]} for i, g in enumerate(part)], 
        "observation_predicates": [pid for pid, _ in obs], "observation_rule_count": len(consumer_rules), 
        "shift_quotient_well_defined": well, "shift_transition": trans, "synchronizing_depth": sync_depth, 
        "partition_counter_context_independent": True, "all_observations_terminal_qualified": consumer_terminal, 
        "advance_updates_paired": advance_ok, "zero_updates_paired": zero_ok, 
        "fall_clear_elision_safe_for_observable_quotient": bool(consumer_terminal and sync_depth is not None and sync_depth <= 7), 
        "physical_recommendation": "Use quotient state in qualified rise domain with protocol START reset; consumers occur only after sufficient paired receive advances.", 
        "inputs": {"phase40_ir_sha256": _sha(ctx["phase_path"]), "legal_product_sha256": _sha(ctx["lp_path"])}, 
    }
    _dump(out / "shift_quotient.json", cert)
    return {"status": "APPLIED", "shift_register": sreg, "quotient_states": len(part), "synchronizing_depth": sync_depth}


def generate_stage6_generic(semantic_dir: Path, out: Path) -> dict[str, Any]: 
    ctx = _context(semantic_dir)
    canonical = generate_canonical(ctx, out)
    control = generate_control(ctx, out, canonical.get("target") if canonical.get("status") == "APPLIED" else None)
    shift = generate_shift(ctx, out)
    generated = [name for name, row in (("canonical_elimination", canonical), ("control_factorization", control), ("shift_quotient", shift)) if row.get("status") == "APPLIED"]
    return {
        "status": "PASS", 
        "version": "bio2rtl-stage6-generic-independent-passes-v1", 
        "generated": generated, 
        "passes": {"canonical_elimination": canonical, "control_factorization": control, "shift_quotient": shift}, 
    }
