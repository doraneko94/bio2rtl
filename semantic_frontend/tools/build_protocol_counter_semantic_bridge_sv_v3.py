#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_sv_emitter import _build_prefix_nodes, _build_action_groups

NONPHYSICAL = {"CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED"}


def sha256(path: Path) -> str: 
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def is_const(expr, value = None) -> bool: 
    return (
        isinstance(expr, list)
        and len(expr) >= 2
        and expr[0] == "CONST"
        and (value is None or int(expr[1]) == int(value))
    )


def is_delta(expr, op: str, rid: str) -> bool: 
    if not (isinstance(expr, list) and len(expr) >= 3 and expr[0] == "OP" and str(expr[1]) == op): 
        return False
    args = expr[2]
    if not isinstance(args, list) or len(args) != 2: 
        return False
    pairs = ((args[0], args[1]), (args[1], args[0])) if op == "ADD" else ((args[0], args[1]),)
    return any(a == ["REG", rid] and is_const(b, 1) for a, b in pairs)


def classify(rule: dict, pair: dict) -> str: 
    target = str(rule["target"])
    out = rule.get("outcome")
    inc = str(pair["inc_register"])
    dec = str(pair["dec_register"])
    maxv = int(pair["max_value"])
    terminal = int(pair["terminal_value"])

    if target == inc: 
        if is_const(out, 0): 
            return "clear"
        if is_const(out, terminal) or is_delta(out, "ADD", inc): 
            return "inc"
    elif target == dec: 
        if is_const(out, 0): 
            return "clear"
        if is_const(out, maxv): 
            return "seed"
        if is_delta(out, "SUB", dec): 
            return "dec"
    raise ValueError(
        f"unclassified counter semantic rule {rule.get('rule_id')} "
        f"target={target} outcome={out}"
    )


def or_expr(xs: list[str]) -> str: 
    xs = sorted(set(xs))
    return " | ".join(xs) if xs else "1'b0"


def range_literal(width: int, value: int) -> str: 
    return f"{width}'d{int(value)}"


def main() -> int: 
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-sv", type = Path, required = True)
    ap.add_argument("--ir", type = Path, required = True)
    ap.add_argument("--proof", type = Path, required = True)
    ap.add_argument("--output-sv", type = Path, required = True)
    ap.add_argument("--metadata", type = Path, required = True)
    ap.add_argument("--report", type = Path, required = True)
    args = ap.parse_args()

    text = args.source_sv.read_text()
    ir = json.loads(args.ir.read_text())
    proof = json.loads(args.proof.read_text())

    if proof.get("result") != "PASS": 
        raise SystemExit("FAIL counter ownership proof is not PASS")
    if proof.get('n_a'): 
        args.output_sv.parent.mkdir(parents = True, exist_ok = True)
        args.metadata.parent.mkdir(parents = True, exist_ok = True)
        args.report.parent.mkdir(parents = True, exist_ok = True)
        args.output_sv.write_text(text)
        meta = {'version': 'protocol-counter-semantic-lowering-v3-proof-driven', 'n_a': True, 'source_storage_bits': None, 'candidate_storage_bits': None, 
              'proof_sha256': sha256(args.proof), 'reason': proof.get('n_a_reason', 'no counter candidate')}
        args.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL PROTOCOL COUNTER SEMANTIC LOWERING', '='*96, 'OPTIONAL RESULT           : N/A', 'identity transform         : PASS', 'RESULT                     : CANDIDATE GENERATED']
        args.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    if not (proof.get("base") or {}).get("pass", False): 
        raise SystemExit("FAIL counter ownership base proof is not PASS")
    pair = proof.get("discovered_pair")
    if not isinstance(pair, dict): 
        raise SystemExit("FAIL proof has no discovered counter pair")

    inc = str(pair["inc_register"])
    dec = str(pair["dec_register"])
    inc_width = int(pair["inc_width"])
    width = int(pair["counter_width"])
    maxv = int(pair["max_value"])
    terminal = int(pair["terminal_value"])
    if inc_width != width + 1 or maxv != (1 << width) - 1 or terminal != (1 << width): 
        raise SystemExit(f"FAIL unsupported/non-canonical discovered counter geometry {pair}")

    # Proof sufficiency is semantic, not tied to historical I2C signature counts.
    base = proof["base"]
    expected_domain = list(range(maxv + 1))
    read_values = base.get("read_values") or {}
    if read_values.get(inc) != expected_domain or read_values.get(dec) != expected_domain: 
        raise SystemExit(
            f"FAIL non-vacuous read-domain coverage absent: {inc}={read_values.get(inc)} {dec}={read_values.get(dec)}"
        )
    read_points = base.get("read_points") or {}
    if not read_points.get(inc) or not read_points.get(dec): 
        raise SystemExit("FAIL ownership proof has vacuous read-point set")
    neg = proof.get("negative_selftests") or {}
    if len(neg) < 4 or not all(v.get("pass") is False and bool(v.get("violations")) for v in neg.values()): 
        raise SystemExit("FAIL counter proof negative selftests are not all rejected")

    priority = list(map(str, proof.get("priority") or []))
    if sorted(priority) != ["clear", "dec", "inc", "seed"]: 
        raise SystemExit(f"FAIL malformed proof priority {priority}")

    storage = {str(x["register"]): x for x in ir["storage_optimization"]["register_storage"]}
    retained = [
        r
        for r in ir["update_rules"]
        if r.get("materialize") and storage[str(r["target"])]["storage_kind"] not in NONPHYSICAL
    ]
    _, _, conds = _build_prefix_nodes(retained)
    actions, rule_to_action = _build_action_groups(retained, conds)

    declared = set(re.findall(r"^logic (act_A\d+);$", text, re.M))
    expected = {x["name"] for x in actions}
    # Source-level DCE may have removed actions made dead by earlier storage
    # reductions while preserving the original emitter numbering.  Every
    # surviving source action must still belong to the IR-derived action set;
    # operations used by this lowering are checked for presence below.
    if not declared.issubset(expected): 
        raise SystemExit(
            f"FAIL source action backend contains unknown actions: {sorted(declared-expected)}"
        )

    ops = {k: [] for k in ("seed", "dec", "inc", "clear")}
    rule_detail = []
    for r in retained: 
        target = str(r["target"])
        if target not in (inc, dec): 
            continue
        op = classify(r, pair)
        act = rule_to_action[str(r["rule_id"])]
        ops[op].append(act)
        rule_detail.append(
            {
                "rule_id": r["rule_id"], 
                "target": target, 
                "operation": op, 
                "action": act, 
                "event_class": r["event_class"], 
                "outcome": r["outcome"], 
            }
        )
    for required in ("seed", "dec", "inc", "clear"): 
        if not ops[required]: 
            raise SystemExit(f"FAIL missing semantic counter operation {required}")
    needed_actions = set().union(*(set(v) for v in ops.values()))
    if not needed_actions.issubset(declared): 
        raise SystemExit(f"FAIL counter operation actions were removed upstream: {sorted(needed_actions-declared)}")

    srcm = re.search(r"// Physical storage plan: 56 -> (\d+) bits[^\n]*", text)
    if not srcm: 
        raise SystemExit("FAIL source storage comment missing")
    srcbits = int(srcm.group(1))
    dstbits = srcbits - inc_width

    # Confirm and replace both discovered physical registers.
    inc_decl = f"logic [{inc_width-1}:0] r_{inc};"
    dec_decl = f"logic [{width-1}:0] r_{dec};"
    if inc_decl not in text or dec_decl not in text: 
        raise SystemExit(f"FAIL discovered source declarations absent: {inc_decl!r}, {dec_decl!r}")

    shared = "PCOUNT"
    if re.search(rf"\br_{shared}\b", text): 
        raise SystemExit(f"FAIL shared counter name r_{shared} already exists")
    text = text.replace(
        inc_decl, 
        f"// Proof-backed shared protocol counter discovered from semantic ownership.\n"
        f"logic [{width-1}:0] r_{shared};\n"
        f"wire [{inc_width-1}:0] r_{inc} = {{1'b0, r_{shared}}};", 
        1, 
    )
    text = text.replace(dec_decl, f"wire [{width-1}:0] r_{dec} = r_{shared};", 1)

    # Remove original D cones.  The source emitter uses one assign per bit.
    for rid, w in ((inc, inc_width), (dec, width)): 
        pat = rf"^wire \[{w-1}:0\] d_{re.escape(rid)};\n(?:assign d_{re.escape(rid)}\[\d+\] = .*?;\n){{{w}}}"
        text, n = re.subn(pat, "", text, count = 1, flags = re.M)
        if n != 1: 
            raise SystemExit(f"FAIL could not remove d_{rid} cone")

    op_signal = {}
    op_lines = ["// Proof-backed shared-counter update predicates recovered from current IR."]
    for op in ("seed", "dec", "inc", "clear"): 
        sig = f"pc_{op}"
        op_signal[op] = sig
        op_lines.append(f"wire {sig} = {or_expr(ops[op])};")

    value_expr = {
        "seed": range_literal(width, maxv), 
        "dec": f"(r_{shared} - {range_literal(width, 1)})", 
        "inc": f"((r_{shared} == {range_literal(width, maxv)}) ? {range_literal(width, maxv)} : (r_{shared} + {range_literal(width, 1)}))", 
        "clear": range_literal(width, 0), 
    }
    # Emit the exact total order selected by the proof search.  The proof may
    # leave some pairwise orders unconstrained; any passing deterministic order
    # is legal and is recorded in metadata.
    expr = f"r_{shared}"
    for op in reversed(priority): 
        expr = f"{op_signal[op]} ? {value_expr[op]} : ({expr})"
    op_lines.append(f"wire [{width-1}:0] d_{shared} = {expr};")
    block = "\n".join(op_lines) + "\n\n"

    # Operation predicates depend on the emitter's act_A* helpers, so insert
    # after the final action assignment rather than near the early storage
    # declarations (which would risk implicit-net/redeclaration behavior).
    action_assigns = list(re.finditer(r"^assign act_A\d+ = .*?;\s*$", text, re.M))
    if not action_assigns: 
        raise SystemExit("FAIL no source action assignments found")
    marker_end = action_assigns[-1].end()
    text = text[:marker_end] + "\n\n" + block + text[marker_end:]

    # Replace sequential reset and update using exact discovered register names.
    reset_pat_inc = rf"^\s*r_{re.escape(inc)} <= {inc_width}'d0;\s*$"
    reset_pat_dec = rf"^\s*r_{re.escape(dec)} <= {width}'d0;\s*$"
    m_inc = re.search(reset_pat_inc, text, re.M)
    m_dec = re.search(reset_pat_dec, text, re.M)
    if not m_inc or not m_dec: 
        raise SystemExit("FAIL discovered counter reset lines absent")
    indent = re.match(r"\s*", m_inc.group(0)).group(0)
    text = text[: m_inc.start()] + f"{indent}r_{shared} <= {range_literal(width, 0)};" + text[m_inc.end() :]
    text = re.sub(reset_pat_dec, "", text, count = 1, flags = re.M)

    upd_pat_inc = rf"^\s*r_{re.escape(inc)} <= d_{re.escape(inc)};\s*$"
    upd_pat_dec = rf"^\s*r_{re.escape(dec)} <= d_{re.escape(dec)};\s*$"
    m_inc = re.search(upd_pat_inc, text, re.M)
    m_dec = re.search(upd_pat_dec, text, re.M)
    if not m_inc or not m_dec: 
        raise SystemExit("FAIL discovered counter sequential update lines absent")
    indent = re.match(r"\s*", m_inc.group(0)).group(0)
    text = text[: m_inc.start()] + f"{indent}r_{shared} <= d_{shared};" + text[m_inc.end() :]
    text = re.sub(upd_pat_dec, "", text, count = 1, flags = re.M)

    if re.search(rf"\bd_{re.escape(inc)}\b|\bd_{re.escape(dec)}\b", text): 
        raise SystemExit("FAIL old discovered counter D-cone consumer remains")

    text = re.sub(
        r"// Physical storage plan: 56 -> \d+ bits[^\n]*", 
        f"// Physical storage plan: 56 -> {dstbits} bits via proof-backed generic protocol-counter ownership lowering.", 
        text, 
        count = 1, 
    )

    args.output_sv.parent.mkdir(parents = True, exist_ok = True)
    args.metadata.parent.mkdir(parents = True, exist_ok = True)
    args.report.parent.mkdir(parents = True, exist_ok = True)
    args.output_sv.write_text(text)

    meta = {
        "version": "protocol-counter-semantic-lowering-v3-proof-driven", 
        "source_storage_bits": srcbits, 
        "candidate_storage_bits": dstbits, 
        "physical_counter_bits": width, 
        "discovered_pair": pair, 
        "shared_register": shared, 
        "source_sv_sha256": sha256(args.source_sv), 
        "ir_sha256": sha256(args.ir), 
        "proof_sha256": sha256(args.proof), 
        "proof_product": {
            "states": int(base.get("states", 0)), 
            "edges": int(base.get("edges", 0)), 
            "read_points": read_points, 
            "read_values": read_values, 
            "frame_open_event": proof.get("frame_open_event"), 
            "frame_close_event": proof.get("frame_close_event"), 
            "passing_policy_count": proof.get("passing_policy_count"), 
        }, 
        "semantic_rules": sorted(rule_detail, key = lambda x: str(x["rule_id"])), 
        "operation_actions": {k: sorted(set(v)) for k, v in ops.items()}, 
        "priority": priority, 
        "old_source_D_cones_retained": False, 
        "register_ids_hardcoded": False, 
        "event_ids_hardcoded": False, 
        "action_ids_hardcoded": False, 
    }
    args.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True) + "\n")

    lines = [
        "BIO2RTL PROOF-DRIVEN GENERIC PROTOCOL COUNTER LOWERING", 
        "=" * 96, 
        f"discovered pair          : {inc}[{inc_width}] + {dec}[{width}] -> {shared}[{width}]", 
        f"storage                  : {srcbits} -> {dstbits} bits", 
        f"proof states/edges        : {base.get('states')}/{base.get('edges')}", 
        f"read domain {inc:>10s} : {read_values.get(inc)}", 
        f"read domain {dec:>10s} : {read_values.get(dec)}", 
        f"priority                 : {priority}", 
        f"semantic rules           : {len(rule_detail)}", 
        "register IDs hardcoded : NO", 
        "event IDs hardcoded    : NO", 
        "action IDs hardcoded   : NO", 
        "old source D cones     : REMOVED", 
        "RESULT: CANDIDATE GENERATED", 
    ]
    args.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__": 
    raise SystemExit(main())
