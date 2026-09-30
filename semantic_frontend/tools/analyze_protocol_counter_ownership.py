#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_reachability import _eval_expr
from tools.analyze_phase_local_state_elision import legal_product


def refs(x): 
    out = set()
    if isinstance(x, list): 
        if len(x) >= 2 and x[0] == "REG": 
            out.add(str(x[1]))
            return out
        for y in x: 
            out |= refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            out |= refs(y)
    return out


def is_const(x, value): 
    return isinstance(x, list) and len(x) >= 2 and x[0] == "CONST" and int(x[1]) == int(value)


def is_op(x, op): 
    return isinstance(x, list) and len(x) >= 2 and x[0] == "OP" and str(x[1]) == str(op)


def op_const_delta(x, op, reg): 
    """Recognize REG +/- CONST1."""
    if not is_op(x, op): 
        return False
    args = x[2]
    if not isinstance(args, list) or len(args) != 2: 
        return False
    pairs = ((args[0], args[1]), (args[1], args[0])) if op == "ADD" else ((args[0], args[1]),)
    for a, b in pairs: 
        if isinstance(a, list) and len(a) >= 2 and a[0] == "REG" and str(a[1]) == reg and is_const(b, 1): 
            return True
    return False


def event_environment(event: str, sampled_sda: int): 
    """Concrete scheduler/GPIO values for one legal sampled I2C event."""
    sampled_sda = int(sampled_sda) & 1
    if event == "PHEVT_FALL": 
        return 0, {"PH00": 1, "H00": sampled_sda}, sampled_sda
    if event == "PHEVT_RISE": 
        return 1, {"PH00": 0, "H00": sampled_sda}, sampled_sda
    if event == "HEVT001": 
        return 1, {"PH00": 1, "H00": 1}, 0
    if event == "HEVT002": 
        return 1, {"PH00": 1, "H00": 0}, 1
    raise ValueError(event)


def legal_actions(phase: str, sda: int, busy: bool): 
    """Non-stuttering legal sampled-I2C actions."""
    sda = int(sda) & 1
    if not busy: 
        return [("HEVT001", 0)]
    if phase == "H": 
        return [("PHEVT_FALL", sda)] + ([("HEVT001", 0)] if sda else [("HEVT002", 1)])
    return [("PHEVT_RISE", 0), ("PHEVT_RISE", 1)]


def build_derived_rows(ir: dict): 
    """Return globally proof-backed derived semantic registers."""
    plan = ir.get("storage_optimization", {})
    rows = {}
    for sp in plan.get("register_storage", []): 
        if str(sp.get("storage_kind")) != "DERIVED_EXPR": 
            continue
        rid = str(sp["register"])
        expr = sp.get("derived_expression")
        if expr is not None: 
            rows[rid] = {
                "expression": expr, 
                "derived_from": list(map(str, sp.get("derived_from", []))), 
            }
    d = plan.get("derived_state_elimination")
    if isinstance(d, dict) and d.get("target") and d.get("expression"): 
        rows.setdefault(
            str(d["target"]), 
            {
                "expression": d["expression"], 
                "derived_from": [str(d.get("source"))] if d.get("source") else [], 
            }, 
        )
    return rows


def augment_derived(base_regs: dict[str, int], derived_rows: dict[str, dict], sched: dict[str, int], gpio: int): 
    regs = dict(base_regs)
    pending = dict(derived_rows)
    while pending: 
        progress = False
        for rid, row in list(pending.items()): 
            if refs(row["expression"]).issubset(regs.keys()): 
                regs[rid] = int(_eval_expr(row["expression"], regs, sched, gpio))
                del pending[rid]
                progress = True
        if not progress: 
            break
    return regs


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument("--direct-table", type = Path, required = True)
    ap.add_argument("--quotient", type = Path, required = True)
    ap.add_argument("--ir", type = Path, required = True)
    ap.add_argument("--output", type = Path, required = True)
    ap.add_argument("--report", type = Path, required = True)
    ap.add_argument("--edge-cache", type = Path)
    args = ap.parse_args()

    table = json.loads(args.direct_table.read_text())
    quotient = json.loads(args.quotient.read_text())
    ir = json.loads(args.ir.read_text())

    rows = {int(k): v for k, v in table["rows"].items()}
    reset_code = int(table["reset_code"])
    tracked = list(map(str, quotient["tracked_registers"]))
    members = {
        int(c["code"]): [dict(zip(tracked, map(int, s))) for s in c["states"]]
        for c in quotient["classes"]
    }
    derived_rows = build_derived_rows(ir)

    broad_seen, _broad_edges, _ = legal_product(table)
    legal_class_phase_pairs = {(c, ph) for c, ph, _ in broad_seen}

    arch = {str(x["id"]): int(x["width"]) for x in ir["architectural_registers"]}
    if "P06" not in arch or "P10" not in arch: 
        raise SystemExit("FAIL ownership source registers absent")
    w6, w10 = arch["P06"], arch["P10"]
    pc_width = w10
    pc_max = (1 << pc_width) - 1
    terminal_value = 1 << pc_width
    counter_regs = {"P06", "P10"}

    read6 = sorted(
        (c, "L")
        for c, ph in legal_class_phase_pairs
        if ph == "L"
        and any(
            "P06" in refs(g["expression"])
            for e in rows.get(c, {}).get("PHEVT_RISE", [])
            for g in e.get("guard", [])
        )
    )
    read10 = sorted(
        (c, "H")
        for c, ph in legal_class_phase_pairs
        if ph == "H"
        and any(
            "P10" in refs(g["expression"])
            for e in rows.get(c, {}).get("PHEVT_FALL", [])
            for g in e.get("guard", [])
        )
    )
    read6_set, read10_set = set(read6), set(read10)
    derived_targets = set(derived_rows)

    def guard_feasible(entry, code, p6, p10, event, sampled_sda): 
        scl, sched, nsda = event_environment(event, sampled_sda)
        gpio = (scl << 16) | (nsda << 17)
        concrete_counter = {"P06": int(p6), "P10": int(p10)}

        deferred = []
        for g in entry.get("guard", []): 
            rr = refs(g["expression"])
            if rr and rr.issubset(counter_regs): 
                if bool(_eval_expr(g["expression"], concrete_counter, sched, gpio)) != bool(g["polarity"]): 
                    return False
            elif rr and rr.issubset(counter_regs | derived_targets) and (rr & derived_targets): 
                deferred.append(g)

        if deferred: 
            for member in members.get(code, []): 
                regs = dict(member)
                regs.update(concrete_counter)
                regs = augment_derived(regs, derived_rows, sched, gpio)
                if all(
                    refs(g["expression"]).issubset(regs.keys())
                    and bool(_eval_expr(g["expression"], regs, sched, gpio)) == bool(g["polarity"])
                    for g in deferred
                ): 
                    return True
            return False
        return True

    def eval_outcome(expr, p6, p10, event, sampled_sda): 
        scl, sched, nsda = event_environment(event, sampled_sda)
        gpio = (scl << 16) | (nsda << 17)
        return int(_eval_expr(expr, {"P06": int(p6), "P10": int(p10)}, sched, gpio))

    def possible_rows(code, event, p6, p10, sampled_sda): 
        out = []
        for entry in rows.get(code, {}).get(event, []): 
            if not guard_feasible(entry, code, p6, p10, event, sampled_sda): 
                continue
            values = []
            for rid, width in (("P06", w6), ("P10", w10)): 
                expr = entry["preserved_outcomes"][rid]
                rr = refs(expr)
                if rr.issubset(counter_regs): 
                    values.append([eval_outcome(expr, p6, p10, event, sampled_sda) & ((1 << width) - 1)])
                else: 
                    values.append(list(range(1 << width)))
            for nm in entry.get("next_map", []): 
                for np6, np10 in itertools.product(*values): 
                    out.append((int(nm["code"]), np6, np10, entry))
        return out

    def classify(entry): 
        o6 = entry["preserved_outcomes"]["P06"]
        o10 = entry["preserved_outcomes"]["P10"]
        return {
            "seed": is_const(o10, pc_max), 
            "inc": op_const_delta(o6, "ADD", "P06"), 
            "dec": op_const_delta(o10, "SUB", "P10"), 
            "clear": is_const(o6, 0) or is_const(o10, 0), 
            "terminal": is_const(o6, terminal_value), 
        }

    def counter_next(count, entry, mode = "base", seed_override = None): 
        k = classify(entry)
        if mode == "wrong_priority": 
            if k["clear"]: 
                return 0
            if k["seed"]: 
                return pc_max if seed_override is None else seed_override
        elif k["seed"]: 
            return pc_max if seed_override is None else seed_override
        if mode != "rx_no_increment" and k["inc"]: 
            return min(pc_max, count + 1)
        if mode != "tx_no_decrement" and k["dec"]: 
            return max(0, count - 1)
        if k["clear"]: 
            return 0
        if k["terminal"]: 
            return count
        return count

    def run(mode = "base", seed_override = None): 
        start = (reset_code, "H", 1, False, 0, 0, 0)
        q = collections.deque([start])
        visited = {start}
        edges = 0
        unique_edges = set()
        violations = []
        p6_values, p10_values = set(), set()

        while q and not violations: 
            code, phase, sda, busy, p6, p10, count = q.popleft()
            if (code, phase) in read6_set: 
                p6_values.add(p6)
                if p6 > pc_max or count != p6: 
                    violations.append(("P06_READ_MISMATCH", (code, phase, sda, busy, p6, p10, count)))
                    break
            if (code, phase) in read10_set: 
                p10_values.add(p10)
                if count != p10: 
                    violations.append(("P10_READ_MISMATCH", (code, phase, sda, busy, p6, p10, count)))
                    break

            for event, sampled in legal_actions(phase, sda, busy): 
                event_sda = sampled if event == "PHEVT_RISE" else sda
                _scl, _sched, next_sda = event_environment(event, event_sda)
                for nc, np6, np10, entry in possible_rows(code, event, p6, p10, event_sda): 
                    next_phase = "L" if event == "PHEVT_FALL" else ("H" if event == "PHEVT_RISE" else phase)
                    next_busy = True if event == "HEVT001" else (False if event == "HEVT002" else busy)
                    next_count = counter_next(count, entry, mode = mode, seed_override = seed_override)
                    st = (nc, next_phase, next_sda, next_busy, np6, np10, next_count)
                    edges += 1
                    unique_edges.add((code, phase, sda, busy, p6, p10, count, event, event_sda, nc, next_phase, next_sda, next_busy, np6, np10, next_count))
                    if st not in visited: 
                        visited.add(st)
                        q.append(st)

        return {
            "pass": not violations, 
            "states": len(visited), 
            "edges": edges, 
            "queue_exhausted": not q, 
            "violations": violations, 
            "p06_read_values": sorted(p6_values), 
            "p10_read_values": sorted(p10_values), 
            "max_p06_read_value": max(p6_values) if p6_values else None, 
            "_visited": visited, 
            "_unique_edges": unique_edges, 
        }

    base = run()
    negatives = {
        "wrong_priority": run("wrong_priority"), 
        "rx_no_increment": run("rx_no_increment"), 
        "tx_no_decrement": run("tx_no_decrement"), 
        "tx_seed_6": run("base", seed_override = max(0, pc_max - 1)), 
    }

    terminal_signatures = set()
    transfer_clear_signatures = set()

    def entry_matches_member(entry, member, p6, p10, event, sampled_sda): 
        scl, sched, next_sda = event_environment(event, sampled_sda)
        gpio = (scl << 16) | (next_sda << 17)
        regs = dict(member)
        regs.update({"P06": p6, "P10": p10})
        regs = augment_derived(regs, derived_rows, sched, gpio)
        for g in entry.get("guard", []): 
            rr = refs(g["expression"])
            if rr.issubset(regs.keys()): 
                if bool(_eval_expr(g["expression"], regs, sched, gpio)) != bool(g["polarity"]): 
                    return False
        return True

    if base["pass"]: 
        for state in base["_visited"]: 
            code, phase, sda, busy, p6, p10, count = state
            for event, sampled in legal_actions(phase, sda, busy): 
                event_sda = sampled if event == "PHEVT_RISE" else sda
                for mi, member in enumerate(members.get(code, [])): 
                    has_terminal = False
                    has_seed = False
                    has_clear = False
                    for entry in rows.get(code, {}).get(event, []): 
                        if not entry_matches_member(entry, member, p6, p10, event, event_sda): 
                            continue
                        k = classify(entry)
                        has_terminal |= k["terminal"]
                        has_seed |= k["seed"]
                        has_clear |= k["clear"]
                    key = (state, event, event_sda, mi)
                    if has_terminal: 
                        terminal_signatures.add(key)
                    if has_seed and has_clear: 
                        transfer_clear_signatures.add(key)

    base_public = {k: v for k, v in base.items() if not k.startswith("_")}
    neg_public = {
        name: {k: v for k, v in row.items() if not k.startswith("_")}
        for name, row in negatives.items()
    }
    result = base_public["pass"] and all(not row["pass"] for row in neg_public.values())

    payload = {
        "version": "protocol-counter-ownership-proof-v3-correlated-legal-product", 
        "inputs": {
            "direct_table": str(args.direct_table), 
            "quotient": str(args.quotient), 
            "ir": str(args.ir), 
        }, 
        "legal_class_phase_pairs": len(legal_class_phase_pairs), 
        "ownership_product": {
            "state_tuple": ["class", "phase", "sda", "busy", "P06", "P10", "PCOUNT"], 
            "global_derived_correlations": sorted(derived_targets), 
            "non_counter_non_derived_guards": "existentially dropped", 
        }, 
        "p06_readpoints": read6, 
        "p10_readpoints": read10, 
        "base": base_public, 
        "terminal_signatures": len(terminal_signatures), 
        "transfer_seed_clear_signatures": len(transfer_clear_signatures), 
        "negative_selftests": neg_public, 
        "result": "PASS" if result else "FAIL", 
        "proof_statement": (
            "Fresh current direct-FSM sampled-I2C product preserves transaction-busy/SDA/scheduler ordering, "
            "original P06/P10 and proposed PCOUNT, while restoring only globally proof-backed derived-state "
            "correlations from current IR. Equality is required at structurally discovered read points; "
            "terminal P06=8 may exist internally but is never live at a P06 read point."
        ), 
    }
    args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True) + "\n")
    if args.edge_cache: 
        cache = {
            "version": "legal-i2c-correlated-product-v1", 
            "state_tuple": ["class", "phase", "sda", "busy", "P06", "P10", "PCOUNT"], 
            "states": [list(x) for x in sorted(base["_visited"])], 
            "edge_tuple": ["class", "phase", "sda", "busy", "P06", "P10", "PCOUNT", "event", "event_sda", "next_class", "next_phase", "next_sda", "next_busy", "next_P06", "next_P10", "next_PCOUNT"], 
            "unique_edges": [list(x) for x in sorted(base["_unique_edges"])], 
            "raw_edge_count": base["edges"], 
            "proof_result": payload["result"], 
        }
        args.edge_cache.parent.mkdir(parents = True, exist_ok = True)
        args.edge_cache.write_text(json.dumps(cache, indent = 2, sort_keys = True) + "\n")

    lines = [
        "BIO2RTL GENERATOR-NATIVE PROTOCOL COUNTER OWNERSHIP PROOF", 
        "=" * 96, 
        f"legal class/phase pairs : {len(legal_class_phase_pairs)}", 
        f"p06 read points         : {len(read6)} {read6}", 
        f"p10 read points         : {len(read10)} {read10}", 
        f"base states/edges       : {base_public['states']}/{base_public['edges']}", 
        f"P06 read values         : {base_public['p06_read_values']}", 
        f"P10 read values         : {base_public['p10_read_values']}", 
        f"terminal signatures     : {len(terminal_signatures)}", 
        f"seed+clear signatures   : {len(transfer_clear_signatures)}", 
        f"derived correlations    : {sorted(derived_targets)}", 
        f"base pass               : {base_public['pass']}", 
    ]
    for name, row in neg_public.items(): 
        lines.append(
            f"negative {name:16s}: rejected={not row['pass']} violation={row.get('violations', [])[:1]}"
        )
    lines.append("RESULT: " + payload["result"])
    args.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not result: 
        raise SystemExit(1)


if __name__ == "__main__": 
    main()
