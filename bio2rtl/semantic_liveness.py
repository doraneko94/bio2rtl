from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .boolean_contract import _expr_vars_contract


_CONST_NETS = {"1'b0", "1'b1", "1'h0", "1'h1", "1'd0", "1'd1", "0", "1"}


def _as_name(value) -> str | None:
    if value is None:
        return None
    name = str(value)
    return None if name in _CONST_NETS else name


def _add_name(out: set[str], value) -> None:
    name = _as_name(value)
    if name is not None:
        out.add(name)


def _add_expr_vars(out: set[str], expr) -> None:
    if expr is None:
        return
    for name in _expr_vars_contract(expr):
        _add_name(out, name)


def analyze_semantic_liveness(contracts: Iterable[dict], graph: dict) -> dict:
    """Find named combinational roles that can affect the physical core boundary.

    The roots are the real top-level outputs plus every combinational net that can
    influence retained state or an explicitly preserved primitive/latch.  Named
    combinational roles outside this transitive dependency closure are semantically
    valid but physically unobservable and therefore need not be mapped.
    """

    contracts = list(contracts)
    comb: dict[str, object] = {}
    owners: dict[str, set[str]] = {}
    roots: set[str] = set()
    root_reasons: dict[str, set[str]] = {}

    def add_root(value, reason: str) -> None:
        name = _as_name(value)
        if name is None:
            return
        roots.add(name)
        root_reasons.setdefault(name, set()).add(reason)

    for contract in contracts:
        component = str(contract.get('component_class', ''))
        for name, expr in (contract.get('combinational_outputs') or {}).items():
            name = str(name)
            if name in comb and comb[name] != expr:
                raise ValueError(f'global combinational role collision {name}')
            comb[name] = expr
            owners.setdefault(name, set()).add(component)

        for dff in contract.get('dffs', []):
            add_root(dff.get('clock'), f'{component}:dff_clock')
            add_root(dff.get('reset'), f'{component}:dff_reset')
            for name in _expr_vars_contract(dff.get('d_expr')):
                add_root(name, f'{component}:dff_d')

        for primitive in contract.get('primitives', []):
            add_root(primitive.get('input'), f'{component}:primitive_input')
            for name in _expr_vars_contract(primitive.get('input_expr')):
                add_root(name, f'{component}:primitive_input_expr')

        for latch in contract.get('latches', []):
            for side in ('set', 'reset'):
                for name in latch.get(side + '_inputs', []):
                    add_root(name, f'{component}:latch_{side}')
                for expr in (latch.get(side + '_input_exprs') or {}).values():
                    for name in _expr_vars_contract(expr):
                        add_root(name, f'{component}:latch_{side}_expr')

    assignments = {
        str(row['lhs']): str(row['rhs'])
        for row in graph.get('assignments', [])
        if isinstance(row, dict) and 'lhs' in row and 'rhs' in row
    }

    def resolve_assignment(name: str) -> str:
        seen: set[str] = set()
        current = str(name)
        while current in assignments:
            if current in seen:
                raise ValueError(f'assignment cycle while resolving top output {name}')
            seen.add(current)
            current = assignments[current]
        return current

    for port in graph.get('ports', []):
        if port.get('direction') != 'output':
            continue
        public = str(port['name'])
        add_root(resolve_assignment(public), f'top_output:{public}')

    live: set[str] = set()
    stack = sorted(name for name in roots if name in comb)
    while stack:
        name = stack.pop()
        if name in live:
            continue
        live.add(name)
        for dep in sorted(_expr_vars_contract(comb[name])):
            if dep in comb and dep not in live:
                stack.append(dep)

    all_comb = set(comb)
    pruned = all_comb - live
    return {
        'version': 'bio2rtl-semantic-combinational-liveness-v1',
        'status': 'PASS',
        'root_nets': sorted(roots),
        'root_reasons': {name: sorted(reasons) for name, reasons in sorted(root_reasons.items())},
        'live_combinational_outputs': sorted(live),
        'pruned_combinational_outputs': sorted(pruned),
        'combinational_output_count_before': len(all_comb),
        'combinational_output_count_after': len(live),
        'pruned_owners': {name: sorted(owners.get(name, ())) for name in sorted(pruned)},
    }


def prune_contract_for_liveness(contract: dict, liveness: dict) -> dict:
    """Return a mapping-only contract with dead named combinational roles removed."""

    live = set(map(str, liveness.get('live_combinational_outputs', [])))
    result = deepcopy(contract)
    comb = {
        str(name): expr
        for name, expr in (contract.get('combinational_outputs') or {}).items()
        if str(name) in live
    }
    removed = set(map(str, (contract.get('combinational_outputs') or {}).keys())) - set(comb)
    result['combinational_outputs'] = comb
    result['interface_outputs'] = [
        str(name)
        for name in contract.get('interface_outputs', [])
        if str(name) not in removed
    ]
    result['liveness_pruned_outputs'] = sorted(removed)
    return result


def prune_contracts_for_liveness(contracts: Iterable[dict], liveness: dict) -> list[dict]:
    return [prune_contract_for_liveness(contract, liveness) for contract in contracts]
