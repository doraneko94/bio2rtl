#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_derived_state import _group_transitions, _refs
from bio2rtl.dedicated_event_reachability import _eval_expr


def subst(x: Any, env: dict[str, int]) -> Any: 
    if isinstance(x, list): 
        if len(x) >= 2 and x[0] == "REG" and str(x[1]) in env: 
            return ["CONST", int(env[str(x[1])])]
        return [subst(y, env) for y in x]
    if isinstance(x, dict): 
        return {k: subst(v, env) for k, v in x.items()}
    return x


def collect_atoms(expr: Any, tracked: set[str], reg_widths: dict[str, int]) -> set[tuple[str, str, int]]: 
    out: set[tuple[str, str, int]] = set()

    def walk(x: Any) -> None: 
        if not isinstance(x, list) or not x: 
            return
        tag = x[0]
        if tag == "BIT_VALUE": 
            base = x[1]
            bit = int(x[2])
            if base[0] == "REG" and str(base[1]) not in tracked: 
                out.add(("REG", str(base[1]), bit))
                return
            if base[0] == "SCHED_REG": 
                out.add(("SCHED", str(base[1]), bit))
                return
            if base[0] == "GPIO_INPUT": 
                out.add(("GPIO", "GPIO_INPUT", bit))
                return
        if tag == "REG" and str(x[1]) not in tracked: 
            rid = str(x[1])
            for b in range(int(reg_widths[rid])): 
                out.add(("REG", rid, b))
            return
        if tag == "SCHED_REG": 
            out.add(("SCHED", str(x[1]), 0))
            return
        # A full GPIO_INPUT value is legal when the expression itself proves that
        # only a constant bit-mask can influence the tracked next state.  Preserve
        # the original expression for evaluation, but expose only the referenced
        # input bits as direct-table atoms.  This covers stack/memory histories
        # such as (GPIO_INPUT & (1 << n)) without inventing a scheduler phase.
        if tag == "OP" and len(x) >= 3 and str(x[1]).upper() == "AND": 
            args = x[2] if isinstance(x[2], list) else []
            gpio_terms = [a for a in args if isinstance(a, list) and a and a[0] == "GPIO_INPUT"]
            const_terms = [a for a in args if isinstance(a, list) and len(a) >= 2 and a[0] == "CONST"]
            if gpio_terms and const_terms: 
                mask = int(const_terms[0][1])
                if mask < 0: 
                    raise ValueError(f"negative GPIO_INPUT mask is unsupported: {mask}")
                for b in range(max(1, mask.bit_length())): 
                    if (mask >> b) & 1: 
                        out.add(("GPIO", "GPIO_INPUT", b))
                # Other operands, if any, still need normal atom discovery.
                for a in args: 
                    if a in gpio_terms or a is const_terms[0]: 
                        continue
                    walk(a)
                return
        if tag == "GPIO_INPUT": 
            raise ValueError(
                "direct full GPIO_INPUT dependency in tracked next-state is unsupported; "
                "use a constant-mask/bit-select expression before direct-table lowering"
            )
        for y in x[1:]: 
            if isinstance(y, list) and y and isinstance(y[0], list): 
                for z in y: 
                    walk(z)
            else: 
                walk(y)

    walk(expr)
    return out


def main() -> int: 
    ap = argparse.ArgumentParser(
        description = (
            "Build only the generic behavioral direct-state proof table. "
            "No candidate RTL is emitted, so startup/detector implementation details "
            "cannot leak into the production lineage."
        )
    )
    ap.add_argument("--complete-ir", type = Path, required = True)
    ap.add_argument("--storage-ir", type = Path, required = True)
    ap.add_argument("--domain", type = Path, required = True)
    ap.add_argument("--quotient", type = Path, required = True)
    ap.add_argument("--output", type = Path, required = True)
    ap.add_argument("--report", type = Path, required = True)
    a = ap.parse_args()

    ir = json.loads(a.complete_ir.read_text())
    sir = json.loads(a.storage_ir.read_text())
    dom = json.loads(a.domain.read_text())
    quo = json.loads(a.quotient.read_text())

    tracked = tuple(map(str, dom["tracked_registers"]))
    tracked_set = set(tracked)
    classes = sorted(quo["classes"], key = lambda c: int(c["code"]))
    codew = max(1, (len(classes) - 1).bit_length())
    state_to_code = {
        tuple(map(int, s)): int(c["code"])
        for c in classes
        for s in c["states"]
    }

    arch = {str(x["id"]): x for x in ir["architectural_registers"]}
    widths = {r: int(x["width"]) for r, x in arch.items()}
    plans = {str(x["register"]): x for x in sir["storage_optimization"]["register_storage"]}
    derived = [
        r for r in arch
        if r not in tracked_set and plans.get(r, {}).get("storage_kind") == "DERIVED_EXPR"
    ]
    preserved = [r for r in arch if r not in tracked_set and r not in derived]
    pred = {str(p["id"]): p["expression"] for p in ir["predicate_basis"]}
    trs = _group_transitions(ir)
    startup = {str(x["register"]): int(x["value"][1]) for x in ir["startup"]["register_values"]}
    reset_tuple = tuple(startup[r] for r in tracked)
    if reset_tuple not in state_to_code: 
        raise SystemExit(f"FAIL: reset tuple outside quotient domain: {reset_tuple}")
    reset_code = state_to_code[reset_tuple]

    table = {
        "version": "behavioral-direct-fsm-v1", 
        "tracked_registers": list(tracked), 
        "preserved_registers": preserved, 
        "classes": len(classes), 
        "code_bits": codew, 
        "reset_code": reset_code, 
        "rows": {}, 
    }
    total_branches = 0
    dynamic_next = 0
    max_atoms = 0

    for c in classes: 
        code = int(c["code"])
        env = dict(zip(tracked, map(int, c["representative"])))
        byevent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tr in trs: 
            residual = []
            dead = False
            for g in tr["guard"]: 
                ex = subst(pred[str(g["basis"])], env)
                rr = _refs(ex)
                if not rr: 
                    val = bool(_eval_expr(ex, {}, {}, 0))
                    if val != bool(g["polarity"]): 
                        dead = True
                        break
                residual.append({"expression": ex, "polarity": bool(g["polarity"])})
            if dead: 
                continue

            po = {rid: subst(tr["outcomes"][rid], env) for rid in preserved}
            nx = [subst(tr["outcomes"][rid], env) for rid in tracked]
            atoms: set[tuple[str, str, int]] = set()
            for ex in nx: 
                atoms |= collect_atoms(ex, tracked_set, widths)
            atoms_sorted = sorted(atoms)
            max_atoms = max(max_atoms, len(atoms_sorted))
            if len(atoms_sorted) > 8: 
                raise SystemExit(
                    f"FAIL: too many direct next-state atoms {atoms_sorted} "
                    f"in {tr['transition_id']}"
                )

            mapping = []
            combos = product([0, 1], repeat = len(atoms_sorted)) if atoms_sorted else [()]
            for bits in combos: 
                regs: dict[str, int] = {}
                sched: dict[str, int] = {}
                gpio = 0
                for (k, n, b), v in zip(atoms_sorted, bits): 
                    if k == "REG": 
                        regs[n] = regs.get(n, 0) | (int(v) << b)
                    elif k == "SCHED": 
                        sched[n] = sched.get(n, 0) | (int(v) << b)
                    elif k == "GPIO": 
                        gpio |= int(v) << b
                    else: 
                        raise AssertionError((k, n, b))
                tup = tuple(int(_eval_expr(ex, regs, sched, gpio)) for ex in nx)
                if tup not in state_to_code: 
                    raise SystemExit(
                        f"FAIL: next tuple outside quotient domain {tup} "
                        f"tr={tr['transition_id']} class={code}"
                    )
                mapping.append({"bits": list(bits), "code": state_to_code[tup]})
            if len({m["code"] for m in mapping}) > 1: 
                dynamic_next += 1
            row = {
                "transition_id": tr["transition_id"], 
                "guard": residual, 
                "preserved_outcomes": po, 
                "next_atoms": [list(x) for x in atoms_sorted], 
                "next_map": mapping, 
            }
            byevent[str(tr["event_class"])].append(row)
            total_branches += 1
        table["rows"][str(code)] = {ev: rows for ev, rows in sorted(byevent.items())}

    source_control_storage_bits = sum(int(plans[r]["storage_bits"]) for r in tracked)
    table["stats"] = {
        "specialized_branches": total_branches, 
        "dynamic_next_branches": dynamic_next, 
        "max_next_atoms": max_atoms, 
        "source_control_storage_bits": source_control_storage_bits, 
        "new_control_storage_bits": codew, 
        "candidate_total_storage_bits": (
            int(sir["storage_optimization"]["natural_storage_bits"])
            - source_control_storage_bits
            + codew
        ), 
    }

    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(table, indent = 2, sort_keys = True) + "\n")
    lines = [
        "BIO2RTL GENERIC BEHAVIORAL DIRECT TABLE", 
        "=" * 88, 
        f"tracked registers       : {len(tracked)}", 
        f"preserved registers     : {len(preserved)}", 
        f"behavioral classes      : {len(classes)}", 
        f"code bits               : {codew}", 
        f"branches                : {total_branches}", 
        f"dynamic next branches   : {dynamic_next}", 
        f"max direct atoms        : {max_atoms}", 
        "candidate RTL emitted   : NO", 
        "binary-specific fixture: NO", 
        "RESULT                  : PASS", 
    ]
    a.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__": 
    raise SystemExit(main())
