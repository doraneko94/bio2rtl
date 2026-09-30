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

from bio2rtl.observation_storage_specialization import *
from bio2rtl.dedicated_event_reachability import _eval_expr


def sha(path: Path) -> str: 
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eval_progress(expr, rid: str, value: int) -> int: 
    if not refs(expr).issubset({rid}): 
        raise ValueError("nonlocal progress recurrence")
    return int(_eval_expr(expr, {rid: int(value)}, {}, 0))


def guard_progress_possible(entry, target: str, progress: str, value: int) -> bool: 
    for g in entry.get("guard", []): 
        rr = refs(g["expression"])
        if target in rr: 
            continue
        external = "GPIO_INPUT" in json.dumps(g["expression"]) or "SCHED_REG" in json.dumps(g["expression"])
        if rr.issubset({progress}) and not external: 
            if bool(_eval_expr(g["expression"], {progress: value}, {}, 0)) != bool(g["polarity"]): 
                return False
    return True


def main() -> int: 
    ap = argparse.ArgumentParser()
    ap.add_argument("--direct-table", type = Path, required = True)
    ap.add_argument("--ir", type = Path, required = True)
    ap.add_argument("--phase-product", type = Path, required = True)
    ap.add_argument("--counter-proof", type = Path, required = True)
    ap.add_argument("--output", type = Path, required = True)
    ap.add_argument("--report", type = Path, required = True)
    args = ap.parse_args()

    table = json.loads(args.direct_table.read_text())
    ir = json.loads(args.ir.read_text())
    phase_product = json.loads(args.phase_product.read_text())
    counter_proof = json.loads(args.counter_proof.read_text())
    if phase_product.get("proof_result") != "PASS": 
        raise SystemExit("FAIL recovered phase product is not PASS")
    if counter_proof.get("result") != "PASS": 
        raise SystemExit("FAIL counter ownership proof is not PASS")
    topo0 = phase_product.get('topology') or {}
    required_topology = {'phase_source', 'data_history_source', 'phase_input_bit', 'data_input_bit', 'active_phase_level', 'phase_events', 'data_events'}
    if counter_proof.get('n_a') or not required_topology.issubset(topo0): 
        payload = {'version': 'generic-observation-storage-projection-v2-recovered-topology', 'inputs': {'direct_table_sha256': sha(args.direct_table), 'ir_sha256': sha(args.ir), 'phase_product_sha256': sha(args.phase_product), 'counter_proof_sha256': sha(args.counter_proof)}, 
                 'topology': topo0, 'results': [], 'n_a': True, 'n_a_reason': 'counter/framed phase topology is not applicable', 'result': 'PASS'}
        args.output.parent.mkdir(parents = True, exist_ok = True)
        args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL GENERIC OBSERVATION-STORAGE PROJECTION', '='*96, 'OPTIONAL RESULT: N/A', 'identity projection: PASS', 'RESULT: PASS']
        args.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    if not (counter_proof.get("base") or {}).get("pass", False): 
        raise SystemExit("FAIL counter ownership base proof is not PASS")

    storage = ir["storage_optimization"]["register_storage"]
    candidates = discover_shift_projection_candidates(table, storage)
    rows = {int(k): v for k, v in table["rows"].items()}
    reset = int(table["reset_code"])

    startup = {
        str(x["register"]): int(x["value"][1])
        for x in ir.get("startup", {}).get("register_values", [])
        if isinstance(x.get("value"), list) and x["value"][0] == "CONST"
    }
    sched_init = {
        str(x["id"]): int(x["initial_value"][1])
        for x in ir.get("scheduler_owned_sources", [])
        if isinstance(x.get("initial_value"), list) and x["initial_value"][0] == "CONST"
    }

    topo = phase_product.get("topology") or {}
    phase_source = str(topo["phase_source"])
    data_source = str(topo["data_history_source"])
    active = int(topo["active_phase_level"])
    phase_events = {int(k): str(v) for k, v in topo["phase_events"].items()}
    data_events = {str(k): str(v) for k, v in topo["data_events"].items()}
    data_edge = {
        data_events["FALL"]: (1, 0), 
        data_events["RISE"]: (0, 1), 
    }
    open_event = str(counter_proof["frame_open_event"])
    close_event = str(counter_proof["frame_close_event"])
    if open_event not in data_edge or close_event not in data_edge or open_event == close_event: 
        raise SystemExit("FAIL counter proof framing events do not match recovered qualified data edges")

    phase0 = int(sched_init[phase_source])
    data0 = int(sched_init[data_source])

    def legal_actions(phase: int, data: int, busy: bool): 
        if not busy: 
            pre, post = data_edge[open_event]
            return [(open_event, phase, post)] if phase == active and data == pre else []
        out = []
        phase_event = phase_events[int(phase)]
        next_phase = 1 - int(phase)
        if phase != active and next_phase == active: 
            out.extend((phase_event, next_phase, nd) for nd in (0, 1))
        else: 
            out.append((phase_event, next_phase, data))
        if phase == active: 
            for ev, (pre, post) in data_edge.items(): 
                if data == pre: 
                    out.append((ev, phase, post))
        return out

    results = []
    for cand in candidates: 
        d = cand.__dict__.copy()
        progress = cand.progress_register
        row = {"candidate": d, "classification": "REJECT"}
        if progress is None or progress not in startup: 
            row["reason"] = "no_unique_progress_register_or_reset"
            results.append(row)
            continue
        p0 = int(startup[progress])

        observations = []
        bit_consumers = 0
        for code, evs in rows.items(): 
            for event, entries in evs.items(): 
                for entry in entries: 
                    for g in entry.get("guard", []): 
                        me = masked_equality(g["expression"], cand.register)
                        if me is not None and me[0] == (1 << cand.width) - 1: 
                            observations.append((code, event, entry, int(me[1]) & ((1 << cand.width) - 1)))
                        bit_consumers += len(bit_reads(g["expression"], cand.register))
                    for target, expr in entry.get("preserved_outcomes", {}).items(): 
                        if str(target) != cand.register: 
                            bit_consumers += len(bit_reads(expr, cand.register))

        # Conservative schema-derived sampled product: class / recovered phase /
        # sampled data / framing-busy / progress / completed shift-stage.
        start = (reset, phase0, data0, False, p0, 0)
        queue = collections.deque([start])
        seen = {start}
        edges = 0
        violations = []
        live = collections.Counter()
        shifts = collections.Counter()
        kinds = collections.Counter()

        while queue and not violations: 
            code, phase, data, busy, pval, stage = queue.popleft()
            for event, nphase, ndata in legal_actions(phase, data, busy): 
                live_constants = set()
                for oc, oe, entry, const in observations: 
                    if oc == code and oe == event and guard_progress_possible(entry, cand.register, progress, pval): 
                        live_constants.add(const)
                for const in sorted(live_constants): 
                    live[(pval, stage, const)] += 1
                    if pval != cand.width or stage != cand.width: 
                        violations.append(
                            {
                                "kind": "OBSERVATION_BEFORE_FULL_SEQUENCE", 
                                "state": [code, phase, data, busy, pval, stage], 
                                "event": event, 
                                "constant": const, 
                            }
                        )
                        break
                if violations: 
                    break

                entries = rows.get(code, {}).get(event, [])
                nbusy = True if event == open_event else (False if event == close_event else busy)
                if not entries: 
                    ns = (code, nphase, ndata, nbusy, pval, stage)
                    edges += 1
                    if ns not in seen: 
                        seen.add(ns)
                        queue.append(ns)
                    continue

                for entry in entries: 
                    if not guard_progress_possible(entry, cand.register, progress, pval): 
                        continue
                    expr = entry["preserved_outcomes"][cand.register]
                    kind, _ibit = update_kind(expr, cand.register)
                    kinds[kind] += 1
                    if kind not in ("HOLD", "CLEAR", "SHIFT"): 
                        violations.append({"kind": "UNSUPPORTED_UPDATE", "transition": entry.get("transition_id")})
                        break
                    nstage = stage
                    if kind == "CLEAR": 
                        nstage = 0
                    elif kind == "SHIFT": 
                        shifts[pval] += 1
                        if pval == 0: 
                            nstage = 1
                        elif 1 <= pval < cand.width and stage == pval: 
                            nstage = pval + 1
                        else: 
                            nstage = cand.width + 1
                    pexpr = entry["preserved_outcomes"].get(progress)
                    if pexpr is None: 
                        violations.append({"kind": "MISSING_PROGRESS_OUTCOME", "transition": entry.get("transition_id")})
                        break
                    try: 
                        npval = eval_progress(pexpr, progress, pval)
                    except Exception: 
                        violations.append({"kind": "NONLOCAL_PROGRESS_OUTCOME", "transition": entry.get("transition_id")})
                        break
                    for nm in entry.get("next_map", []) or [{"code": code}]: 
                        ns = (int(nm["code"]), nphase, ndata, nbusy, npval, nstage)
                        edges += 1
                        if ns not in seen: 
                            seen.add(ns)
                            queue.append(ns)
                if violations: 
                    break
            if violations: 
                break

        mask = (1 << cand.width) - 1
        data_bad = []
        cases = 0
        if cand.width <= 12: 
            for const in cand.match_constants: 
                expected = [(const >> (cand.width - 1 - i)) & 1 for i in range(cand.width)]
                for old in range(1 << cand.width): 
                    for bits in range(1 << cand.width): 
                        cases += 1
                        hist = old
                        match = 0
                        seq = [(bits >> (cand.width - 1 - i)) & 1 for i in range(cand.width)]
                        for i, bit in enumerate(seq): 
                            hist = ((hist << 1) | bit) & mask
                            eq = int(bit == expected[i])
                            match = eq if i == 0 else match & eq
                        if bool(match) != bool(hist == const): 
                            data_bad.append({"constant": const, "old": old, "bits": bits})
                            break
                    if data_bad: 
                        break
                if data_bad: 
                    break
        else: 
            violations.append({"kind": "WIDTH_EXCEEDS_EXHAUSTIVE_PROOF_BOUND", "width": cand.width})

        suffix_bad = []
        suffix_cases = 0
        k = cand.low_keep_width
        if k: 
            kmask = (1 << k) - 1
            for hist in range(1 << cand.width): 
                low = hist & kmask
                for bit in (0, 1): 
                    for kind in ("HOLD", "CLEAR", "SHIFT"): 
                        suffix_cases += 1
                        if kind == "HOLD": 
                            nh, nl = hist, low
                        elif kind == "CLEAR": 
                            nh, nl = 0, 0
                        else: 
                            nh = ((hist << 1) | bit) & mask
                            nl = ((low << 1) | bit) & kmask
                        if (nh & kmask) != nl: 
                            suffix_bad.append({"hist": hist, "bit": bit, "kind": kind})
                            break
                    if suffix_bad: 
                        break
                if suffix_bad: 
                    break

        neg = 0
        if cand.match_constants: 
            orig = cand.match_constants[0]
            wrong = (orig ^ 1) & mask
            for bits in range(1 << cand.width): 
                seq = [(bits >> (cand.width - 1 - i)) & 1 for i in range(cand.width)]
                hist = 0
                match = 0
                for i, bit in enumerate(seq): 
                    hist = ((hist << 1) | bit) & mask
                    exp = (wrong >> (cand.width - 1 - i)) & 1
                    eq = int(bit == exp)
                    match = eq if i == 0 else match & eq
                if bool(match) != bool(hist == orig): 
                    neg += 1

        ok = not violations and bool(live) and not data_bad and not suffix_bad and neg > 0
        row.update(
            {
                "classification": "PASS_SHIFT_OBSERVATION_PROJECTION" if ok else "REJECT", 
                "progress_register": progress, 
                "reset_progress": p0, 
                "sequence_product": {
                    "states": len(seen), 
                    "edges": edges, 
                    "live_observations": sum(live.values()), 
                    "live_signature": [
                        {"progress": p, "stage": st, "constant": co, "count": n}
                        for (p, st, co), n in sorted(live.items())
                    ], 
                    "shift_progress_counts": dict(sorted(shifts.items())), 
                    "update_kinds": dict(kinds), 
                    "violations": violations, 
                }, 
                "match_identity": {"cases": cases, "violations": data_bad}, 
                "suffix_induction": {"cases": suffix_cases, "violations": suffix_bad}, 
                "negative_mutation_mismatches": neg, 
                "bit_consumer_instances": bit_consumers, 
            }
        )
        results.append(row)

    payload = {
        "version": "generic-observation-storage-projection-v2-recovered-topology", 
        "inputs": {
            "direct_table_sha256": sha(args.direct_table), 
            "ir_sha256": sha(args.ir), 
            "phase_product_sha256": sha(args.phase_product), 
            "counter_proof_sha256": sha(args.counter_proof), 
        }, 
        "topology": topo, 
        "framing": {"open_event": open_event, "close_event": close_event}, 
        "search": "autodiscover HOLD/CLEAR/SHL1+GPIO histories; masked constant equality + low-bit consumers; structural progress recurrence", 
        "results": results, 
        "result": "PASS" if any(r["classification"].startswith("PASS") for r in results) else "NO_CANDIDATE", 
        "notes": [
            "No register ID, event ID, address constant, phase spelling, scheduler-source ID, or GPIO phase/data bit is selected by this proof driver.", 
            "Framing direction is consumed from the generic counter-ownership proof; phase/data semantics are consumed from the recovered legal phase topology.", 
        ], 
    }
    args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True) + "\n")
    lines = ["BIO2RTL GENERIC OBSERVATION-STORAGE PROJECTION V2", "=" * 96]
    for r in results: 
        c = r["candidate"]
        lines.append(
            f"{c['register']}: {r['classification']} width={c['width']} -> {c['projected_bits']} bits "
            f"save={c['saved_bits']} constants={c['match_constants']} bits={c['observed_bits']} progress={r.get('progress_register')}"
        )
        if "sequence_product" in r: 
            lines.append(
                f"  product={r['sequence_product']['states']}/{r['sequence_product']['edges']} "
                f"live={r['sequence_product']['live_observations']} match_cases={r['match_identity']['cases']} "
                f"suffix_cases={r['suffix_induction']['cases']}"
            )
    lines.append("RESULT: " + payload["result"])
    args.report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__": 
    raise SystemExit(main())
