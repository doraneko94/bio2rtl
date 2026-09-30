from __future__ import annotations
from pathlib import Path
import re
from .boundary_range_analysis import BoundaryRangeAnalysis
from .semantic_systemverilog import state_reg_name


def apply_constant_boundary_state_pruning(path: Path, analysis: BoundaryRangeAnalysis) -> list[str]: 
    """Replace proven reset-zero persistent registers by constant wires.

    The proof is semantic-boundary based: every region-exit value is HOLD or
    CONST(0), and reset initializes physical state to zero.  Intermediate SSA
    computations remain in the combinational next-state network; only the
    persistent storage is removed.
    """
    text = path.read_text()
    pruned = [r.register for r in analysis.rows if r.constant_zero_candidate]
    if not pruned: 
        return []

    # Infer declaration width from emitted RTL, convert the persistent register
    # into a constant-driven logic net, and remove all nonblocking FF writes.
    for family in pruned: 
        reg = state_reg_name(family)
        decl = re.search(rf'(?m)^logic(?: \[(\d+):0\])? {re.escape(reg)};\s*$', text)
        if decl is None: 
            raise RuntimeError(f'{family}: emitted state declaration not found')
        msb = decl.group(1)
        width = 1 if msb is None else int(msb) + 1
        replacement = decl.group(0) + f"\nassign {reg} = {width}'d0;"
        text = text[:decl.start()] + replacement + text[decl.end():]
        text = re.sub(rf'(?m)^\s*{re.escape(reg)}\s*<=\s*[^;]+;\s*$', '', text)

    path.write_text(text)
    return pruned
