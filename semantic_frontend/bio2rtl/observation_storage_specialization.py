from __future__ import annotations

"""Generic observation-driven storage projection helpers.

This module deliberately contains no BIO register names, protocol addresses, or
transition IDs.  It recognizes a semantic shift-history register only from its
current direct-FSM update/consumer structure and proposes retaining exactly the
observations that remain live: constant-match accumulators plus a low-bit suffix.
"""

from dataclasses import dataclass
from typing import Any, Iterable

Expr = Any


def refs(x: Expr) -> set[str]: 
    out: set[str] = set()
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


def is_reg(x: Expr, rid: str) -> bool: 
    return isinstance(x, list) and len(x) >= 2 and x[0] == "REG" and str(x[1]) == rid


def const_value(x: Expr) -> int | None: 
    if isinstance(x, list) and len(x) >= 2 and x[0] == "CONST": 
        return int(x[1])
    return None


def _flatten_or(x: Expr) -> list[Expr]: 
    if isinstance(x, list) and len(x) >= 3 and x[0] == "OP" and x[1] == "OR": 
        out: list[Expr] = []
        for y in x[2]: 
            out += _flatten_or(y)
        return out
    return [x]


def shift_input_bit(expr: Expr, rid: str) -> int | None: 
    """Return GPIO bit for (REG<<1)|GPIO_BIT, independent of OR operand order."""
    parts = _flatten_or(expr)
    saw_shift = False
    gpio_bit: int | None = None
    for p in parts: 
        if (isinstance(p, list) and len(p) >= 3 and p[0] == "OP" and p[1] == "SHL"
                and len(p[2]) == 2 and is_reg(p[2][0], rid) and const_value(p[2][1]) == 1): 
            saw_shift = True
            continue
        if (isinstance(p, list) and len(p) >= 3 and p[0] == "BIT_VALUE"
                and p[1] == ["GPIO_INPUT"]): 
            gpio_bit = int(p[2])
            continue
        # Any extra nonzero term means this is not the canonical shift form.
        if const_value(p) == 0: 
            continue
        return None
    return gpio_bit if saw_shift else None


def update_kind(expr: Expr, rid: str) -> tuple[str, int | None]: 
    if is_reg(expr, rid): 
        return ("HOLD", None)
    if const_value(expr) == 0: 
        return ("CLEAR", None)
    bit = shift_input_bit(expr, rid)
    if bit is not None: 
        return ("SHIFT", bit)
    return ("OTHER", None)


def low_mask_width(mask: int) -> int | None: 
    if mask <= 0: 
        return None
    w = mask.bit_length()
    return w if mask == (1 << w) - 1 else None


def masked_equality(expr: Expr, rid: str) -> tuple[int, int] | None: 
    """Recognize (REG & low_mask)==CONST or REG==CONST.

    Returns (mask, constant).  Direct REG equality has mask=-1 because its width
    is not encoded by the expression itself.
    """
    if not (isinstance(expr, list) and len(expr) >= 3 and expr[0] == "EQ"): 
        return None
    a, b = expr[1], expr[2]
    for lhs, rhs in ((a, b), (b, a)): 
        cv = const_value(rhs)
        if cv is None: 
            continue
        if is_reg(lhs, rid): 
            return (-1, cv)
        if (isinstance(lhs, list) and len(lhs) >= 3 and lhs[0] == "OP" and lhs[1] == "AND"): 
            aa = lhs[2]
            if len(aa) != 2: 
                continue
            if is_reg(aa[0], rid) and const_value(aa[1]) is not None: 
                return (int(const_value(aa[1])), cv)
            if is_reg(aa[1], rid) and const_value(aa[0]) is not None: 
                return (int(const_value(aa[0])), cv)
    return None


def bit_reads(expr: Expr, rid: str) -> set[int]: 
    out: set[int] = set()
    if isinstance(expr, list): 
        if (len(expr) >= 3 and expr[0] == "BIT_VALUE" and is_reg(expr[1], rid)): 
            out.add(int(expr[2]))
            return out
        for y in expr: 
            out |= bit_reads(y, rid)
    elif isinstance(expr, dict): 
        for y in expr.values(): 
            out |= bit_reads(y, rid)
    return out


def is_supported_consumer(expr: Expr, rid: str, width: int) -> bool: 
    """True iff every use of rid is a recognized equality or direct low-bit read."""
    if rid not in refs(expr): 
        return True
    if masked_equality(expr, rid) is not None: 
        return True
    # Allow expressions composed around direct BIT_VALUE uses, but reject a raw
    # REG occurrence that is not beneath BIT_VALUE.
    def walk(x: Expr, under_bit: bool = False) -> bool: 
        if isinstance(x, list): 
            if is_reg(x, rid): 
                return under_bit
            if len(x) >= 3 and x[0] == "BIT_VALUE" and is_reg(x[1], rid): 
                return 0 <= int(x[2]) < width
            return all(walk(y, under_bit) for y in x)
        if isinstance(x, dict): 
            return all(walk(y, under_bit) for y in x.values())
        return True
    return walk(expr)


@dataclass(frozen = True)
class ShiftProjectionCandidate: 
    register: str
    width: int
    input_gpio_bit: int
    match_constants: tuple[int, ...]
    observed_bits: tuple[int, ...]
    low_keep_width: int
    projected_bits: int
    saved_bits: int
    progress_register: str | None


def discover_shift_projection_candidates(table: dict, storage_rows: Iterable[dict] | None = None) -> list[ShiftProjectionCandidate]: 
    rows = {int(k): v for k, v in table["rows"].items()}
    outcomes: dict[str, list[Expr]] = {}
    entries: list[tuple[int, str, dict]] = []
    for c, evs in rows.items(): 
        for ev, ents in evs.items(): 
            for ent in ents: 
                entries.append((c, ev, ent))
                for rid, ex in ent.get("preserved_outcomes", {}).items(): 
                    outcomes.setdefault(str(rid), []).append(ex)

    semantic_width = {}
    if storage_rows is not None: 
        for r in storage_rows: 
            semantic_width[str(r.get("register"))] = int(r.get("semantic_width", 0) or 0)

    result: list[ShiftProjectionCandidate] = []
    for rid, xs in sorted(outcomes.items()): 
        kinds = [update_kind(x, rid) for x in xs]
        if not any(k == "SHIFT" for k, _ in kinds): 
            continue
        if any(k == "OTHER" for k, _ in kinds): 
            continue
        ibits = {b for k, b in kinds if k == "SHIFT"}
        if len(ibits) != 1: 
            continue
        input_bit = next(iter(ibits))
        assert input_bit is not None

        eqs: set[tuple[int, int]] = set()
        bits: set[int] = set()
        unsupported = False
        for _c, _ev, ent in entries: 
            exprs = [g.get("expression") for g in ent.get("guard", [])]
            exprs += [ex for tr, ex in ent.get("preserved_outcomes", {}).items() if str(tr) != rid]
            for ex in exprs: 
                if rid not in refs(ex): 
                    continue
                me = masked_equality(ex, rid)
                if me is not None: 
                    eqs.add(me)
                bits |= bit_reads(ex, rid)
                if not is_supported_consumer(ex, rid, max(1, semantic_width.get(rid, 32))): 
                    unsupported = True
        if unsupported or not eqs: 
            continue
        # All match consumers must refer to the same contiguous low mask.  This
        # is the observable width after earlier generic bit-liveness pruning.
        explicit = {m for m, _ in eqs if m >= 0}
        if len(explicit) != 1: 
            continue
        mask = next(iter(explicit))
        width = low_mask_width(mask)
        if width is None: 
            continue
        if any(c & ~mask for _, c in eqs): 
            continue
        if any(b >= width for b in bits): 
            continue
        constants = tuple(sorted({c & mask for _, c in eqs}))
        low_keep = max(bits)+1 if bits else 0
        projected = len(constants) + low_keep
        if projected >= width: 
            continue

        # Discover a progress register structurally: on SHIFT entries it must
        # have self-only recurrence, and at least one occurrence must be +1.
        shift_entries = [ent for _c, _ev, ent in entries if update_kind(ent.get("preserved_outcomes", {}).get(rid), rid)[0] == "SHIFT"]
        progress: list[str] = []
        other_regs = sorted({str(x) for ent in shift_entries for x in ent.get("preserved_outcomes", {}) if str(x) != rid})
        for pr in other_regs: 
            saw_inc = False
            ok = True
            for ent in shift_entries: 
                ex = ent.get("preserved_outcomes", {}).get(pr)
                if ex is None: 
                    ok = False
                    break
                rr = refs(ex)
                if not rr.issubset({pr}): 
                    ok = False
                    break
                if (isinstance(ex, list) and len(ex)>=3 and ex[0] == "OP" and ex[1] == "ADD"
                    and len(ex[2]) == 2 and is_reg(ex[2][0], pr) and const_value(ex[2][1]) == 1): saw_inc = True
            if ok and saw_inc: 
                progress.append(pr)
        pr = progress[0] if len(progress) == 1 else None
        result.append(ShiftProjectionCandidate(rid, width, int(input_bit), constants, tuple(sorted(bits)), low_keep, projected, width-projected, pr))
    return result
