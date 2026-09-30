#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_reachability import _eval_expr

NONPHYSICAL = {"CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED"}


def sha256(path: Path) -> str: 
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def refs(expr): 
    out = set()
    if isinstance(expr, list): 
        if len(expr) >= 2 and expr[0] == "REG": 
            out.add(str(expr[1]))
            return out
        for x in expr: 
            out |= refs(x)
    elif isinstance(expr, dict): 
        for x in expr.values(): 
            out |= refs(x)
    return out


def build_derived_rows(ir: dict) -> dict[str, object]: 
    out = {}
    opt = ir.get("storage_optimization") or {}
    for row in opt.get("register_storage", []): 
        if str(row.get("storage_kind")) == "DERIVED_EXPR" and row.get("derived_expression") is not None: 
            out[str(row["register"])] = row["derived_expression"]
    d = opt.get("derived_state_elimination")
    if isinstance(d, dict) and d.get("target") and d.get("expression"): 
        out.setdefault(str(d["target"]), d["expression"])
    return out


def augment_derived(regs: dict[str, int], derived: dict[str, object], sched: dict[str, int], gpio: int): 
    regs = dict(regs)
    pending = dict(derived)
    while pending: 
        progress = False
        for rid, expr in list(pending.items()): 
            if refs(expr).issubset(regs): 
                regs[rid] = int(_eval_expr(expr, regs, sched, gpio))
                del pending[rid]
                progress = True
        if not progress: 
            break
    return regs


def possible_bit(expr, bit: int, storage: dict[str, dict]): 
    if not isinstance(expr, list) or not expr: 
        return {0, 1}
    tag = expr[0]
    if tag == "CONST": 
        return {(int(expr[1]) >> bit) & 1}
    if tag == "REG": 
        rid = str(expr[1])
        row = storage.get(rid, {})
        vals = row.get("reachable_values_upper_bound")
        if vals is not None: 
            return {(int(v) >> bit) & 1 for v in vals}
        sw = int(row.get("semantic_width", 32))
        return {0} if bit >= sw else {0, 1}
    if tag in ("GPIO_INPUT", "SCHED_REG"): 
        return {0, 1}
    if tag == "OP": 
        op = str(expr[1])
        args = expr[2] if isinstance(expr[2], list) else []
        if op in ("SHL", "SHR") and len(args) == 2 and isinstance(args[1], list) and args[1][0] == "CONST": 
            n = int(args[1][1])
            sb = bit - n if op == "SHL" else bit + n
            return {0} if sb < 0 else possible_bit(args[0], sb, storage)
        if op in ("AND", "OR", "XOR") and len(args) == 2: 
            aa = possible_bit(args[0], bit, storage)
            bb = possible_bit(args[1], bit, storage)
            if op == "AND": 
                return {x & y for x in aa for y in bb}
            if op == "OR": 
                return {x | y for x in aa for y in bb}
            return {x ^ y for x in aa for y in bb}
        return {0, 1}
    return {0, 1}


def main() -> int: 
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", type = Path, required = True)
    ap.add_argument("--quotient", type = Path, required = True)
    ap.add_argument("--direct-table", type = Path, required = True)
    ap.add_argument("--legal-product", type = Path, required = True)
    ap.add_argument("--phase-product", type = Path, required = True)
    ap.add_argument("--output", type = Path, required = True)
    ap.add_argument("--report", type = Path, required = True)
    args = ap.parse_args()

    ir = json.loads(args.ir.read_text())
    quotient = json.loads(args.quotient.read_text())
    table = json.loads(args.direct_table.read_text())
    legal = json.loads(args.legal_product.read_text())
    phase_product = json.loads(args.phase_product.read_text())
    if legal.get("proof_result") != "PASS" or not legal.get("states"): 
        raise SystemExit("FAIL legal ownership product is not nonempty PASS")
    if phase_product.get("proof_result") != "PASS": 
        raise SystemExit("FAIL recovered phase product is not PASS")

    state_fields = list(map(str, legal.get("state_tuple", [])))
    edge_fields = list(map(str, legal.get("edge_tuple", [])))
    required_state = {"class", "phase", "data"}
    required_edge = {"class", "phase", "data", "event", "next_class", "next_phase", "next_data"}
    if not required_state.issubset(state_fields) or not required_edge.issubset(edge_fields): 
        payload = {'version': 'observation-local-packed-bit-specialization-v2', 'proof_model': 'SCHEMA_DRIVEN_LEGAL_PRODUCT', 'results': [], 
                 'n_a': True, 'n_a_reason': 'legal product has no phase/data scheduler tuple; packed observation specialization is not applicable', 
                 'legal_product_schema': {'state_tuple': state_fields, 'edge_tuple': edge_fields}}
        args.output.parent.mkdir(parents = True, exist_ok = True)
        args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL OBSERVATION-LOCAL PACKED-BIT SPECIALIZATION', '='*96, 'OPTIONAL RESULT: N/A', 'reason: detector topology has no phase/data product', 'RESULT: PASS']
        args.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    si = {n: state_fields.index(n) for n in state_fields}
    ei = {n: edge_fields.index(n) for n in edge_fields}

    topo = phase_product.get("topology") or {}
    phase_source = str(topo["phase_source"])
    data_history_source = str(topo["data_history_source"])
    phase_input_bit = int(topo["phase_input_bit"])
    data_input_bit = int(topo["data_input_bit"])
    active_phase = int(topo["active_phase_level"])

    tracked = list(map(str, quotient["tracked_registers"]))
    ti = {r: i for i, r in enumerate(tracked)}
    reps = {int(c["code"]): tuple(map(int, c["representative"])) for c in quotient["classes"]}
    storage = {str(x["register"]): x for x in ir["storage_optimization"]["register_storage"]}
    derived = build_derived_rows(ir)
    arch = {str(x["id"]): int(x["width"]) for x in ir["architectural_registers"]}

    physical = [
        r
        for r in tracked
        if str(storage.get(r, {}).get("storage_kind")) not in NONPHYSICAL
        and int(storage.get(r, {}).get("storage_bits", 0)) > 0
    ]
    packed = []
    for rid, row in storage.items(): 
        if str(row.get("storage_kind")) == "PACKED_MASK_BITS" and row.get("stored_bits"): 
            packed.append((rid, [int(x) for x in row["stored_bits"]]))

    # Schema-driven class/event graph and exact source-state outgoing edges.
    class_edges = sorted(
        set(
            (
                int(e[ei["class"]]), 
                str(e[ei["event"]]), 
                int(e[ei["next_class"]]), 
            )
            for e in legal["unique_edges"]
        )
    )
    outgoing_class = collections.defaultdict(list)
    for ce in class_edges: 
        outgoing_class[ce[0]].append(ce)

    # Match a stored legal state to concrete cached edges using all state fields
    # also present in the edge source schema. This remains valid if the counter
    # pair names or auxiliary state fields change.
    common_source_fields = [n for n in state_fields if n in ei]
    outgoing_state = collections.defaultdict(list)
    for e in legal["unique_edges"]: 
        key = tuple(e[ei[n]] for n in common_source_fields)
        outgoing_state[key].append(e)

    def state_key(st): 
        return tuple(st[si[n]] for n in common_source_fields)

    def cval(code: int, rid: str) -> int: 
        return int(reps[int(code)][ti[rid]])

    def edge_environment(e): 
        phase = int(e[ei["phase"]])
        data = int(e[ei["data"]])
        nphase = int(e[ei["next_phase"]])
        ndata = int(e[ei["next_data"]])
        sampled_data = ndata if phase != active_phase and nphase == active_phase else data
        sched = {phase_source: phase, data_history_source: sampled_data}
        gpio = ((nphase & 1) << phase_input_bit) | ((ndata & 1) << data_input_bit)
        return sched, gpio

    def concrete_regs(st, code: int): 
        regs = {r: cval(code, r) for r in tracked}
        for rid in arch: 
            if rid in si: 
                regs[rid] = int(st[si[rid]])
        return regs

    def atom_eval(atom, code): 
        return cval(code, atom["register"]) == int(atom["value"])

    def formula_eval(form, code): 
        if form["kind"] == "ATOM": 
            return atom_eval(form["atom"], code)
        x = atom_eval(form["left"], code)
        y = atom_eval(form["right"], code)
        return (x and y) if form["kind"] == "AND" else (x or y)

    def formula_text(form): 
        if form["kind"] == "ATOM": 
            return f"({form['atom']['register']}=={form['atom']['value']})"
        return (
            f"(({form['left']['register']}=={form['left']['value']}) "
            f"{form['kind']} ({form['right']['register']}=={form['right']['value']}))"
        )

    startup = {}
    for row in ir.get("startup", {}).get("register_values", []): 
        val = row.get("value")
        if isinstance(val, list) and len(val) >= 2 and val[0] == "CONST": 
            startup[str(row["register"])] = int(val[1])

    results = []
    for rid, bits in packed: 
        # Direct-table guard consumers are discovered from semantic register refs.
        reader_rows = []
        reader_classes_all = set()
        for cs, evs in table["rows"].items(): 
            for ev, entries in evs.items(): 
                for entry in entries: 
                    if any(rid in refs(g.get("expression")) for g in entry.get("guard", [])): 
                        reader_rows.append((int(cs), str(ev), entry))
                        reader_classes_all.add(int(cs))
        if not reader_rows: 
            continue

        obs_classes = set()
        obs_instances = set()
        for st in legal["states"]: 
            code = int(st[si["class"]])
            edges = outgoing_state.get(state_key(st), [])
            if not edges: 
                continue
            for rc, event, entry in reader_rows: 
                if rc != code: 
                    continue
                # A reader context is live if at least one concrete legal edge of
                # the same semantic event satisfies all known non-target guards.
                live = False
                for edge in edges: 
                    if str(edge[ei["event"]]) != event: 
                        continue
                    sched, gpio = edge_environment(edge)
                    regs = augment_derived(concrete_regs(st, code), derived, sched, gpio)
                    ok = True
                    for g in entry.get("guard", []): 
                        rr = refs(g.get("expression"))
                        if rid in rr: 
                            continue
                        if rr.issubset(regs): 
                            if bool(_eval_expr(g["expression"], regs, sched, gpio)) != bool(g["polarity"]): 
                                ok = False
                                break
                        elif not rr: 
                            if bool(_eval_expr(g["expression"], regs, sched, gpio)) != bool(g["polarity"]): 
                                ok = False
                                break
                        # Unknown unrelated state remains existentially widened.
                    if ok: 
                        live = True
                        break
                if live: 
                    obs_classes.add(code)
                    aux = tuple(int(st[si[n]]) for n in state_fields if n in arch and n not in tracked)
                    obs_instances.add((code,) + aux)

        atoms = []
        for ar in physical: 
            vals = sorted({cval(c, ar) for c in obs_classes}) if obs_classes else []
            for v in vals: 
                atoms.append({"register": ar, "value": v})
        forms = [{"kind": "ATOM", "atom": x} for x in atoms]
        for i, x in enumerate(atoms): 
            for y in atoms[i + 1 :]: 
                forms.append({"kind": "AND", "left": x, "right": y})
                forms.append({"kind": "OR", "left": x, "right": y})

        for bit in bits: 
            tid_effect = {}
            updating = []
            for rule in ir["update_rules"]: 
                if str(rule.get("target")) != rid: 
                    continue
                vals = sorted(possible_bit(rule["outcome"], bit, storage))
                for tid in rule.get("source_transition_ids", []): 
                    tid_effect[str(tid)] = tuple(vals)
                updating.append(
                    {
                        "rule_id": str(rule["rule_id"]), 
                        "source_transition_ids": list(map(str, rule.get("source_transition_ids", []))), 
                        "possible_bit_values": vals, 
                    }
                )

            trans = collections.defaultdict(set)
            for c, ev, nc in class_edges: 
                for entry in table["rows"].get(str(c), {}).get(ev, []): 
                    if not any(int(x["code"]) == nc for x in entry.get("next_map", [])): 
                        continue
                    tid = str(entry["transition_id"])
                    trans[(c, ev, nc)].add(tid_effect.get(tid, ("HOLD",)))

            reset_class = int(table["reset_code"])
            reset_flag = (startup.get(rid, 0) >> bit) & 1
            reset = (reset_class, reset_flag)
            seen = {reset}
            queue = collections.deque([reset])
            while queue: 
                c, flag = queue.popleft()
                for _, ev, nc in outgoing_class.get(c, []): 
                    for eff in trans[(c, ev, nc)]: 
                        vals = {flag} if eff == ("HOLD",) else set(map(int, eff))
                        for nf in vals: 
                            ns = (nc, nf)
                            if ns not in seen: 
                                seen.add(ns)
                                queue.append(ns)

            best = None
            checked = 0
            for form in forms: 
                checked += 1
                good = True
                for c in obs_classes: 
                    want = bool(formula_eval(form, c))
                    flags = {f for cc, f in seen if cc == c}
                    if not flags or any(bool(f) != want for f in flags): 
                        good = False
                        break
                if not good: 
                    continue
                regs_used = (
                    {form["atom"]["register"]}
                    if form["kind"] == "ATOM"
                    else {form["left"]["register"], form["right"]["register"]}
                )
                rank = (
                    {"ATOM": 0, "AND": 1, "OR": 2}[form["kind"]], 
                    len(regs_used), 
                    formula_text(form), 
                )
                if best is None or rank < best[0]: 
                    best = (rank, form)

            row = {
                "register": rid, 
                "semantic_bit": bit, 
                "abstract_reachable_class_flag_states": len(seen), 
                "all_guard_reader_classes": sorted(reader_classes_all), 
                "observation_classes": sorted(obs_classes), 
                "observation_product_instances": len(obs_instances), 
                "formulas_checked": checked, 
                "update_bit_abstraction": updating, 
                "reset_flag": reset_flag, 
            }
            if best is None: 
                row["classification"] = "NO_BOUNDED_FORMULA"
            else: 
                row.update(
                    {
                        "classification": "PASS_OBSERVATION_LOCAL_BIT_ELIMINATION", 
                        "formula": best[1], 
                        "formula_text": formula_text(best[1]), 
                        "read_context_mismatches": 0, 
                    }
                )
            results.append(row)

    payload = {
        "version": "observation-local-packed-bit-specialization-v2-schema-driven", 
        "inputs": {
            "ir_sha256": sha256(args.ir), 
            "quotient_sha256": sha256(args.quotient), 
            "direct_table_sha256": sha256(args.direct_table), 
            "legal_product_sha256": sha256(args.legal_product), 
            "phase_product_sha256": sha256(args.phase_product), 
        }, 
        "proof_model": "SCHEMA_DRIVEN_CLASS_FLAG_REACHABILITY_PLUS_RECOVERED_PHASE_LEGAL_PRODUCT", 
        "legal_product": {
            "states": len(legal["states"]), 
            "raw_edges": legal.get("raw_edge_count"), 
            "stored_unique_edges": len(legal["unique_edges"]), 
            "class_event_edges": len(class_edges), 
            "state_tuple": state_fields, 
            "edge_tuple": edge_fields, 
        }, 
        "phase_topology": topo, 
        "results": results, 
        "notes": [
            "No counter register, event, scheduler-source, GPIO-bit, or tuple-position identity is hard-coded.", 
            "Concrete guard environments are reconstructed from legal-product edge schema and recovered phase topology.", 
            "Unknown unrelated guard state is existentially widened.", 
            "Alternative formulas use bounded one/two-atom equalities over other physical canonical tracked state.", 
        ], 
    }
    args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True) + "\n")
    lines = [
        "BIO2RTL SCHEMA-DRIVEN OBSERVATION-LOCAL PACKED-BIT SPECIALIZATION", 
        "=" * 96, 
        f"legal product             : {len(legal['states'])} states / {legal.get('raw_edge_count')} raw edges", 
    ]
    for r in results: 
        suffix = f" -> {r.get('formula_text')}" if r.get("formula_text") else ""
        lines.append(
            f"{r['register']}[{r['semantic_bit']}]: states={r['abstract_reachable_class_flag_states']} "
            f"obs={r['observation_classes']} {r['classification']}{suffix}"
        )
    lines.append(
        "RESULT: PASS"
        if any(r["classification"].startswith("PASS") for r in results)
        else "RESULT: NO CANDIDATE"
    )
    args.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__": 
    raise SystemExit(main())
