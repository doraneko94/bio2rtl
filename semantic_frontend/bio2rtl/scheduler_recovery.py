from __future__ import annotations

"""Recover Dedicated Event scheduler/state-layout facts from frontend diagnostics.

This module is intentionally independent of any prior Dedicated Event JSON fixture.
It consumes diagnostics that are regenerated from the input .dis plus the generated
semantic SystemVerilog.  A prior validated IR may be used by a caller as a test
oracle, but is never an input to recovery.
"""

from collections import Counter
import ast
import re
from typing import Any

U32_MASK = 0xFFFFFFFF


def _u32(x: int) -> int: 
    return int(x) & U32_MASK


def _event_name(events: list[str] | tuple[str, ...]) -> str: 
    return "+".join(sorted(str(x) for x in events)) if events else "EVENT_FREE"


def _parse_sv_int(token: str) -> int: 
    t = token.replace("_", "").strip()
    m = re.fullmatch(r"(?:\d+)'([hHdDbB])([0-9a-fA-FxXzZ]+)", t)
    if m: 
        base = {"h": 16, "d": 10, "b": 2}[m.group(1).lower()]
        digits = m.group(2)
        if any(c in digits.lower() for c in "xz"): 
            raise ValueError(f"non-constant SV integer: {token}")
        return int(digits, base)
    return int(t, 0)


def _sv_const_expr_to_python(expr: str, env: dict[str, int]) -> str: 
    def repl_const(m: re.Match[str]) -> str: 
        return str(_parse_sv_int(m.group(0)))
    out = re.sub(r"\d+'[hHdDbB][0-9a-fA-FxXzZ_]+", repl_const, expr)
    for name, value in sorted(env.items(), key = lambda kv: -len(kv[0])): 
        out = re.sub(rf"\b{re.escape(name)}\b", str(int(value)), out)
    return out


_ALLOWED_AST = (
    ast.Expression, ast.Constant, ast.UnaryOp, ast.BinOp, 
    ast.Invert, ast.USub, ast.UAdd, 
    ast.BitAnd, ast.BitOr, ast.BitXor, 
    ast.Add, ast.Sub, ast.LShift, ast.RShift, 
)


def _eval_sv_const_expr(expr: str, env: dict[str, int]) -> int: 
    py = _sv_const_expr_to_python(expr, env)
    tree = ast.parse(py, mode = "eval")
    for node in ast.walk(tree): 
        if not isinstance(node, _ALLOWED_AST): 
            raise ValueError(f"unsupported startup SV expression node {type(node).__name__}: {expr}")
    return _u32(eval(compile(tree, "<startup-sv>", "eval"), {"__builtins__": {}}, {}))


def _extract_reset_values(semantic_sv: str) -> dict[str, int]: 
    # Restrict to reset branch to avoid matching normal sequential assignments.
    m = re.search(r"if\s*\(reset\)\s*begin(?P<body>.*?)\n\s*end\s+else\s+begin", semantic_sv, re.S)
    if not m: 
        raise ValueError("cannot locate semantic SV reset branch")
    body = m.group("body")
    out: dict[str, int] = {}
    for name, raw in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*<=\s*([^;]+);", body, re.M): 
        try: 
            out[name] = _parse_sv_int(raw.strip())
        except Exception: 
            continue
    return out


def _extract_semantic_widths(semantic_sv: str) -> dict[str, int]: 
    out: dict[str, int] = {}
    for hi, lo, name in re.findall(r"logic\s*\[(\d+)\s*:\s*(\d+)\]\s+sem_([A-Za-z0-9_]+)_storage\s*;", semantic_sv): 
        out[name] = abs(int(hi) - int(lo)) + 1
    # Also support direct semantic state declarations if no _storage is used.
    for hi, lo, name in re.findall(r"logic\s*\[(\d+)\s*:\s*(\d+)\]\s+sem_([A-Za-z0-9_]+)\s*;", semantic_sv): 
        out.setdefault(name, abs(int(hi) - int(lo)) + 1)
    return out


def _extract_startup_gpio(semantic_sv: str) -> tuple[int, int, int]: 
    # The startup block is the entry-reach block that assigns gpio_mask_next.
    block_re = re.compile(r"if\s*\((reach_entry_[A-Za-z0-9_]+)\)\s*begin(?P<body>.*?)\n\s*end", re.S)
    selected = None
    for m in block_re.finditer(semantic_sv): 
        if re.search(r"\bgpio_mask_next\s*=", m.group("body")): 
            selected = m.group("body")
            break
    if selected is None: 
        # Generic output-only/edge-only programs often initialize only GPIO data
        # or direction and never write the mask.  Fall back to the first entry
        # block containing any GPIO side effect; reset values supply untouched
        # fields.  The legacy I2C path still takes the gpio_mask_next block above
        # and therefore remains byte-identical.
        for m in block_re.finditer(semantic_sv): 
            if re.search(r"\bgpio_(?:mask|data|direction)_next\s*=", m.group("body")): 
                selected = m.group("body")
                break
    if selected is None: 
        # No startup GPIO side effect is legal; all values remain at reset.
        selected = ""
    reset = _extract_reset_values(semantic_sv)
    env = {
        "gpio_mask_next": int(reset.get("gpio_mask", U32_MASK)), 
        "gpio_data_next": int(reset.get("gpio_data", 0)), 
        "gpio_direction_next": int(reset.get("gpio_direction", 0)), 
    }
    for line in selected.splitlines(): 
        mm = re.match(r"\s*(gpio_mask_next|gpio_data_next|gpio_direction_next)\s*=\s*(.*);\s*$", line)
        if not mm: 
            continue
        name, expr = mm.group(1), mm.group(2)
        env[name] = _eval_sv_const_expr(expr, env)
    return _u32(env["gpio_mask_next"]), _u32(env["gpio_data_next"]), _u32(env["gpio_direction_next"])


def _extract_startup_wait_predicate(semantic_sv: str) -> tuple[list[Any], bool]: 
    # Find the first sampled-state self-loop after entry.  The semantic frontend
    # emits wait loops as active_sample_X & (predicate) and the exit edge as the
    # complement.  Preserve that predicate and use polarity=False for run.
    entry = re.search(r"assign\s+edge_entry_[A-Za-z0-9_]+_bb(\d+)\s*=\s*reach_entry_[A-Za-z0-9_]+\s*&\s*\(1'b1\);", semantic_sv)
    if not entry: 
        # Current emitter may name the target only in the edge lhs; use the first
        # active_sample self-loop as a conservative generic fallback.
        sample_id = None
    else: 
        sample_id = entry.group(1)
    candidates = []
    pat = re.compile(
        r"assign\s+edge_sample_bb(?P<s>\d+)_bb(?P=s)_bb(?P=s)\s*=\s*active_sample_bb(?P=s)\s*&\s*\((?P<expr>.*)\);"
    )
    for m in pat.finditer(semantic_sv): 
        if sample_id is None or m.group("s") == sample_id: 
            candidates.append(m.group("expr").strip())
    if not candidates: 
        # The entry edge target in this emitter is encoded as bb001->bb002, while
        # the sampled-state name is sample_bb002. Find the earliest self-loop.
        for m in pat.finditer(semantic_sv): 
            candidates.append(m.group("expr").strip())
            break
    if len(candidates) != 1: 
        raise ValueError(f"ambiguous startup wait self-loop predicates: {candidates}")
    expr = candidates[0]
    # Strip balanced outer parens aggressively.
    while expr.startswith("(") and expr.endswith(")"): 
        inner = expr[1:-1].strip()
        depth = 0; ok = True
        for ch in inner: 
            if ch == "(": 
                depth += 1
            elif ch == ")": 
                depth -= 1
                if depth < 0: 
                    ok = False
                    break
        if ok and depth == 0: 
            expr = inner
        else: 
            break
    # Expected generic form: (sampled_gpio_X & CONST) != CONST or == CONST.
    mm = re.fullmatch(
        r"\(?\s*sampled_gpio_[A-Za-z0-9_]+\s*&\s*(?P<mask>\d+'[hHdDbB][0-9a-fA-FxXzZ_]+)\s*\)?\s*(?P<op>!=|==)\s*(?P<value>\d+'[hHdDbB][0-9a-fA-FxXzZ_]+)", 
        expr, 
    )
    if not mm: 
        raise ValueError(f"unsupported startup wait predicate in semantic SV: {expr}")
    mask = _parse_sv_int(mm.group("mask")); value = _parse_sv_int(mm.group("value"))
    tag = "NE" if mm.group("op") == "!=" else "EQ"
    ded = [tag, ["OP", "AND", [["GPIO_INPUT"], ["CONST", _u32(mask)]]], ["CONST", _u32(value)]]
    # self-loop predicate is the wait condition; startup/run is its logical complement.
    return ded, True


def _infer_required_gpio_level(start_expr: list[Any], run_polarity: bool, bit: int) -> int | None: 
    # Handle equality/inequality of (GPIO & mask) to constant.  Determine the
    # unique bit level implied by the run condition if possible.
    if not isinstance(start_expr, list) or len(start_expr) != 3 or start_expr[0] not in ("EQ", "NE"): 
        return None
    left, right = start_expr[1], start_expr[2]
    if not (isinstance(left, list) and left[:2] == ["OP", "AND"] and right[0] == "CONST"): 
        return None
    args = left[2]
    if args[0] != ["GPIO_INPUT"] or args[1][0] != "CONST": 
        return None
    mask, value = _u32(args[1][1]), _u32(right[1])
    # Compute whether run condition means equality.
    expr_true_means_equal = start_expr[0] == "EQ"
    run_means_equal = expr_true_means_equal == bool(run_polarity)
    if not run_means_equal or not ((mask >> bit) & 1): 
        return None
    return (value >> bit) & 1


def _qualifier_from_text(text: str) -> dict[str, Any]: 
    m = re.fullmatch(r"GPIO@[^\[]+\[(\d+)\]=(0|1)", str(text))
    if not m: 
        raise ValueError(f"unsupported canonical-event qualifier: {text}")
    return {"bit": int(m.group(1)), "level": int(m.group(2)), "source": "GPIO_INPUT"}


def recover_scheduler_template(
    *, 
    fse_report: dict[str, Any], 
    state_report: dict[str, Any], 
    state_role_report: dict[str, Any], 
    canonical_event_report: dict[str, Any], 
    polling_report: dict[str, Any], 
    semantic_sv: str, 
) -> dict[str, Any]: 
    canonical_events = list(canonical_event_report.get("canonical_events", []))
    polling_rows = list(polling_report.get("rows", []))
    # Generic Ver.1 scheduler composition is optional.  A BIO may expose
    # canonical edges without a software polling phase, a polling phase without
    # a separate history detector, both (the original I2C benchmark), or no
    # recovered scheduler primitive at all.  Never invent a fake phase/clock to
    # make a later pass happy.
    legacy_edge_plus_polling = bool(canonical_events) and bool(polling_rows)

    # History scheduler sources are the canonical event previous-sample latches.
    history_sources: list[tuple[str, int]] = []
    for ce in canonical_events: 
        pair = (str(ce["latch"]), int(ce["gpio_bit"]))
        if pair not in history_sources: 
            history_sources.append(pair)

    phase_sources: list[tuple[str, int]] = []
    for pr in polling_rows: 
        pair = (str(pr["state"]), int(pr["gpio_bit"]))
        if pair not in phase_sources: 
            phase_sources.append(pair)

    # Preserve the historical I2C startup contract byte-for-byte when a real
    # polling phase exists.  Edge-only BIOs have no wait barrier: their history
    # initialization comes from software/reset state, not from an invented GPIO
    # level constraint.
    if polling_rows: 
        start_expr, wait_polarity = _extract_startup_wait_predicate(semantic_sv)
        run_polarity = not wait_polarity
    else: 
        start_expr, wait_polarity, run_polarity = None, None, None

    reset_values = _extract_reset_values(semantic_sv)
    def history_source_one_value(source: str, bit: int) -> int: 
        row = next((r for r in state_role_report.get("rows", []) if str(r.get("name")) == source), None)
        if row is None: 
            return 1
        for ex in row.get("nonself_unique", []): 
            if not (isinstance(ex, list) and len(ex)>=3 and ex[0] == "OP" and ex[1] == "AND"): 
                continue
            args = ex[2] if isinstance(ex[2], list) else []
            consts = [a for a in args if isinstance(a, list) and len(a)>=2 and a[0] == "CONST"]
            samples = [a for a in args if isinstance(a, list) and a and a[0] in ("GPIO_SAMPLE", "GPIO_INPUT", "GPIO_VALUE")]
            if consts and samples: 
                mask = int(consts[0][1]) & 0xffffffff
                if (mask>>bit)&1: 
                    return 1<<bit
        return 1

    scheduler_owned = []
    hist_id: dict[tuple[str, int], str] = {}
    for i, (source, bit) in enumerate(history_sources): 
        sid = f"H{i:02d}"; hist_id[(source, bit)] = sid
        if start_expr is not None: 
            init = _infer_required_gpio_level(start_expr, bool(run_polarity), bit)
        else: 
            init = int(reset_values.get(f"r_{source}", reset_values.get(f"sem_{source}_storage", reset_values.get(f"sem_{source}", 0))))
            # Scheduler history is a logical sampled bit even when the source
            # register uses a wider/one-hot software encoding.  Canonical-event
            # discovery supplies the GPIO bit; absent an explicit encoding proof
            # the conservative reset is its low logical bit.
            init = 1 if init == 1 else 0
        row = {
            "exposed_to_controller": True, 
            "id": sid, 
            "initial_value": ["CONST", int(init)], 
            "input_bit": bit, 
            "maintenance": "CAPTURE_GPIO_INPUT", 
            "role": "EDGE_HISTORY_AUX", 
            "source": source, 
        }
        one = history_source_one_value(source, bit)
        if one!=1: 
            row["source_one_value"] = int(one)
        scheduler_owned.append(row)
    phase_id: dict[tuple[str, int], str] = {}
    for i, (source, bit) in enumerate(phase_sources): 
        sid = f"PH{i:02d}"; phase_id[(source, bit)] = sid
        init = _infer_required_gpio_level(start_expr, bool(run_polarity), bit) if start_expr is not None else 0
        if init is None: 
            init = 0
        scheduler_owned.append({
            "exposed_to_controller": False, 
            "id": sid, 
            "initial_value": ["CONST", int(init)], 
            "input_bit": bit, 
            "maintenance": "EVENT_PHASE_AUTOMATON", 
            "role": "POLLING_PHASE_AUTOMATON", 
            "source": source, 
        })

    detectors = []
    for ce in canonical_events: 
        detectors.append({
            "edge": str(ce["edge"]), 
            "event_id": str(ce["event_id"]), 
            "input_bit": int(ce["gpio_bit"]), 
            "kind": "QUALIFIED_INPUT_EDGE", 
            "qualifiers": [_qualifier_from_text(q) for q in ce.get("qualifiers", [])], 
            "software_source_removed": True, 
        })
    # Infer polling event IDs from FSE event names not accounted for by canonical events.
    event_ids = sorted({str(e) for tr in fse_report.get("transition_rows", []) for e in tr.get("events", [])})
    canonical_ids = {str(x["event_id"]) for x in canonical_events}
    poll_event_ids = [e for e in event_ids if e not in canonical_ids]
    for pr in polling_rows: 
        sid = phase_id[(str(pr["state"]), int(pr["gpio_bit"]))]
        for edge, before, after in (("RISE", 0, 1), ("FALL", 1, 0)): 
            matches = [e for e in poll_event_ids if edge in e.upper()]
            if len(matches) != 1: 
                raise ValueError(f"cannot uniquely identify polling {edge} event from {poll_event_ids}")
            detectors.append({
                "edge": edge, 
                "event_id": matches[0], 
                "input_bit": int(pr["gpio_bit"]), 
                "kind": "POLLING_PHASE_COMPLETION", 
                "phase_after": after, 
                "phase_before": before, 
                "phase_state": sid, 
                "qualifiers": [], 
                "software_source_removed": True, 
            })

    # Architectural physical registers retain the frontend's family order, minus
    # previous-sample latches that became scheduler-owned.
    hist_sources_set = {s for s, _ in history_sources}
    physical_families = [r for r in state_report.get("families", []) if str(r["family"]) not in hist_sources_set]
    arch = []
    for i, row in enumerate(physical_families): 
        src = str(row["family"]); rid = f"P{i:02d}"; width = int(row["width"])
        raw_reset = int(reset_values.get(f"r_{src}", 0))
        arch.append({"id": rid, "kind": "PHYSICAL", "provenance": src, "reset": ["CONST", raw_reset], "width": width})

    phase_sources_set = {s for s, _ in phase_sources}
    semantic_rows = [r for r in state_role_report.get("rows", []) if str(r["name"]) not in phase_sources_set and str(r["name"]) not in hist_sources_set]
    semantic_names = [str(r["name"]) for r in semantic_rows]
    sem_widths = _extract_semantic_widths(semantic_sv)

    def phi_entry_initial_value(row: dict[str, Any]) -> int | None: 
        """Recover post-startup state from the SSA PHI entry predecessor.

        The emitted semantic SV reset represents the frontend storage object's
        pre-program reset and can differ from a BIO's explicit startup
        initialization (for example ``li x9, 0xb`` before entering the main
        loop).  State-role PHIs retain that startup edge explicitly.  Prefer
        predecessor block 0 when present; otherwise accept a unique constant
        incoming edge.  Ambiguous PHIs remain on the legacy reset path rather
        than guessing.
        """
        const_incoming: list[tuple[int, int]] = []
        for inc in row.get("incoming", []): 
            if not (isinstance(inc, list) and len(inc) == 2): 
                continue
            pred, expr = inc
            if isinstance(expr, list) and len(expr) >= 2 and expr[0] == "CONST": 
                const_incoming.append((int(pred), int(expr[1])))
        entry0 = [v for pred, v in const_incoming if pred == 0]
        if len(entry0) == 1: 
            return entry0[0]
        if len(const_incoming) == 1: 
            return const_incoming[0][1]
        return None

    row_by_name = {str(r["name"]): r for r in semantic_rows}
    for i, src in enumerate(semantic_names): 
        rid = f"S{i:02d}"; width = int(sem_widths.get(src, 1))
        raw_reset = int(reset_values.get(f"sem_{src}_storage", reset_values.get(f"sem_{src}", 0)))
        entry_init = phi_entry_initial_value(row_by_name[src])
        if entry_init is not None: 
            raw_reset = int(entry_init)
        arch.append({"id": rid, "kind": "SEMANTIC", "provenance": src, "reset": ["CONST", raw_reset], "width": width})

    gpio_mask, gpio_data, gpio_dir = _extract_startup_gpio(semantic_sv)
    arch.append({"id": "G_DATA", "kind": "GPIO", "provenance": "gpio_data", "reset": ["CONST", gpio_data], "width": 32})
    arch.append({"id": "G_DIR", "kind": "GPIO", "provenance": "gpio_direction", "reset": ["CONST", gpio_dir], "width": 32})

    counts = Counter(_event_name(tr.get("events", [])) for tr in fse_report.get("transition_rows", []))
    all_detector_ids = [str(d["event_id"]) for d in detectors]
    classes = []
    for name in sorted(counts, key = lambda x: (x != "EVENT_FREE", x)): 
        required = [] if name == "EVENT_FREE" else name.split("+")
        classes.append({
            "event_class": name, 
            "forbidden_detectors": sorted(x for x in all_detector_ids if x not in required), 
            "required_detectors": required, 
            "scheduler_only": name == "EVENT_FREE", 
            "transition_count": int(counts[name]), 
        })

    startup_regs = [{"register": r["id"], "value": list(r["reset"])} for r in arch]
    if start_expr is not None: 
        startup = {
            "gpio_mask_constant": gpio_mask, 
            "register_values": startup_regs, 
            "startup_run_predicates": [[{"expression": start_expr, "polarity": run_polarity}]], 
            "startup_wait_predicates": [[{"expression": start_expr, "polarity": wait_polarity}]], 
        }
    else: 
        startup = {
            "gpio_mask_constant": gpio_mask, 
            "register_values": startup_regs, 
            "startup_run_predicates": [], 
            "startup_wait_predicates": [], 
        }

    return {
        "version": "dedicated-scheduler-template-v1-auto-recovered", 
        "architectural_registers": arch, 
        "scheduler_owned_sources": scheduler_owned, 
        "scheduler_detectors": detectors, 
        "startup": startup, 
        "event_classes": classes, 
        "recovery": ({
            "fixture_free": True, 
            "history_sources": len(history_sources), 
            "phase_sources": len(phase_sources), 
            "architectural_registers": len(arch), 
            "event_classes": len(classes), 
        } if legacy_edge_plus_polling else {
            "fixture_free": True, 
            "history_sources": len(history_sources), 
            "phase_sources": len(phase_sources), 
            "architectural_registers": len(arch), 
            "event_classes": len(classes), 
            "scheduler_mode": ("EDGE_ONLY" if history_sources else ("POLLING_ONLY" if phase_sources else "NONE")), 
            "startup_barrier": bool(polling_rows), 
        }), 
    }
