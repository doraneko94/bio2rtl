from __future__ import annotations

"""Scalable sound storage-range analysis for event-boundary state.

The exact joint-state closure used by :mod:`event_boundary_state_analysis` is
valuable when it terminates, but a corrected software relation can legitimately
have a very large Cartesian reachable set.  Storage-width proofs do not require
materializing that joint set.

This module computes a *per-register Cartesian over-approximation*.  Each
register owns an explicit finite value domain (the current BIO fixture has small
architectural widths).  Transfer is monotone:

* reset values seed every domain;
* a source transition may contribute an outcome whenever the subset of its
  guard constraints that can be evaluated from the outcome's referenced state
  variables/GPIO bits is satisfiable;
* constraints that cannot be represented locally are ignored, never assumed;
* GPIO bits are existentially enumerated from expression dependency analysis;
* if an expression Cartesian product exceeds the configured cap, the target is
  conservatively widened to its full declared domain.

Ignoring constraints can only add values.  Therefore every concrete
feasibility-proven event-boundary execution is contained in the resulting
per-register domains.  Natural-width narrowing derived from those domains is
sound without relying on scheduler reachability, basic-block identity, protocol
knowledge, or directed tests.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from math import ceil, log2
import itertools
from typing import Any, Iterable

from .event_boundary_state_analysis import (
    PersistentStateSpec, 
    _backward_gpio_dependencies, 
    _compile_transitions, 
    _eval_expr, 
    _gpio_domains, 
    _gpio_refs, 
    _state_refs, 
)

Expr = tuple


def _information_width(count: int) -> int: 
    if count <= 1: 
        return 0
    return int(ceil(log2(count)))


@dataclass
class AbstractPersistentStateRow: 
    state: str
    role: str
    original_width: int
    reset: int
    reachable_values_upper_bound: list[int]
    reachable_value_count_upper_bound: int
    natural_width: int
    information_width_lower_bound_candidate: int
    constant: bool
    recurrence_classes: dict[str, int]
    sparse_encoding_candidate_bits: int | None
    notes: list[str]


@dataclass
class AbstractEventBoundaryStorageResult: 
    proof_model: str
    source_transitions: int
    iterations: int
    final_potential_transition_rows: int
    state_order: list[str]
    state_rows: list[AbstractPersistentStateRow]
    original_storage_bits: int
    natural_storage_bits: int
    cartesian_value_product_log2_upper_bound: float
    expression_full_domain_fallbacks: int
    ignored_cross_state_guard_constraints: int
    ignored_state_gpio_guard_constraints: int
    notes: list[str]


def _product_size(domains: Iterable[Iterable[int]]) -> int: 
    n = 1
    for d in domains: 
        try: 
            n *= len(d)  # type: ignore[arg-type]
        except TypeError: 
            n *= len(tuple(d))
    return n


def _constraint_refs(pred: Expr) -> tuple[set[str], set[str]]: 
    return _state_refs(pred), _gpio_refs(pred)


def analyze_event_boundary_abstract_storage(
    transition_rows: list[dict[str, Any]], 
    state_specs: list[PersistentStateSpec], 
    *, 
    max_gpio_bits_per_sample: int = 12, 
    max_eval_combinations: int = 250_000, 
    max_iterations: int = 512, 
) -> AbstractEventBoundaryStorageResult: 
    """Return a sound per-register value-domain over-approximation.

    The result is intentionally weaker than exact joint reachability and is
    intended specifically for proof-backed natural-width storage lowering.
    """

    specs = {s.name: s for s in state_specs}
    if len(specs) != len(state_specs): 
        raise ValueError("duplicate persistent state specification")
    names = tuple(sorted(specs))
    widths = {n: int(specs[n].width) for n in names}
    masks = {n: (1 << widths[n]) - 1 for n in names}
    domains: dict[str, set[int]] = {
        n: {int(specs[n].reset) & masks[n]} for n in names
    }

    compiled, _ = _compile_transitions(transition_rows, set(names))
    recurrence_counts: dict[str, Counter[str]] = {n: Counter() for n in names}
    full_domain_fallbacks = 0

    ignored_cross = 0
    ignored_state_gpio = 0
    seen_constraint_shapes: set[tuple[str, str]] = set()
    for tr in compiled: 
        for pred, _pol in (*tr.state_constraints, *tr.gpio_constraints): 
            sr, gr = _constraint_refs(pred)
            if len(sr) > 1: 
                seen_constraint_shapes.add(("cross", repr(pred)))
            if sr and gr: 
                seen_constraint_shapes.add(("state_gpio", repr(pred)))
    ignored_cross = sum(1 for k, _ in seen_constraint_shapes if k == "cross")
    ignored_state_gpio = sum(1 for k, _ in seen_constraint_shapes if k == "state_gpio")

    def filtered_domain(
        state_name: str, 
        constraints: tuple[tuple[Expr, bool], ...], 
    ) -> set[int]: 
        """Apply only unary, GPIO-independent constraints for one state."""
        vals = domains[state_name]
        useful: list[tuple[Expr, bool]] = []
        for pred, polarity in constraints: 
            sr, gr = _constraint_refs(pred)
            if sr == {state_name} and not gr: 
                useful.append((pred, polarity))
        if not useful: 
            return set(vals)
        out: set[int] = set()
        for v in vals: 
            state = {state_name: v}
            if all(bool(_eval_expr(pred, state, {})) == pol for pred, pol in useful): 
                out.add(v)
        return out

    def transition_impossible_from_unary_constraints(tr) -> bool: 
        # Constant constraints and unary state constraints can prove a row
        # impossible.  Everything else is deliberately ignored.
        all_constraints = (*tr.state_constraints, *tr.gpio_constraints)
        checked_states: set[str] = set()
        for pred, polarity in all_constraints: 
            sr, gr = _constraint_refs(pred)
            if not sr and not gr: 
                if bool(_eval_expr(pred, {}, {})) != polarity: 
                    return True
            elif len(sr) == 1 and not gr: 
                checked_states |= sr
        for s in checked_states: 
            if s in domains and not filtered_domain(s, all_constraints): 
                return True
        return False

    def outcome_values(tr, target: str, expr: Expr) -> set[int]: 
        nonlocal full_domain_fallbacks
        all_constraints = (*tr.state_constraints, *tr.gpio_constraints)
        state_refs = sorted(s for s in _state_refs(expr) if s in domains)
        state_domains: list[list[int]] = []
        for s in state_refs: 
            vals = sorted(filtered_domain(s, all_constraints))
            if not vals: 
                return set()
            state_domains.append(vals)

        # Constraints whose state dependencies are entirely represented by the
        # expression can safely improve precision.  Other guards are ignored.
        local_constraints: list[tuple[Expr, bool]] = []
        gpio_deps: dict[str, set[int]] = defaultdict(set)
        for name, bits in _backward_gpio_dependencies(expr).items(): 
            gpio_deps[name].update(bits)
        expr_state_set = set(state_refs)
        for pred, polarity in all_constraints: 
            sr, _gr = _constraint_refs(pred)
            if sr and not sr.issubset(expr_state_set): 
                continue
            # A state-free GPIO guard or a guard over only the expression's
            # state variables can be evaluated in the same Cartesian product.
            local_constraints.append((pred, polarity))
            for name, bits in _backward_gpio_dependencies(pred).items(): 
                gpio_deps[name].update(bits)

        try: 
            gpio_domains = _gpio_domains(gpio_deps, max_gpio_bits_per_sample)
        except RuntimeError: 
            full_domain_fallbacks += 1
            return set(range(1 << widths[target]))
        gpio_names = sorted(gpio_domains)
        gpio_value_domains = [list(gpio_domains[n]) for n in gpio_names]

        combinations = _product_size(state_domains) * _product_size(gpio_value_domains)
        if combinations > max_eval_combinations: 
            full_domain_fallbacks += 1
            return set(range(1 << widths[target]))

        results: set[int] = set()
        state_product = itertools.product(*state_domains) if state_domains else [()]
        for sc in state_product: 
            st = dict(zip(state_refs, sc))
            gpio_product = itertools.product(*gpio_value_domains) if gpio_value_domains else [()]
            for gc in gpio_product: 
                gpio = dict(zip(gpio_names, gc))
                ok = True
                for pred, polarity in local_constraints: 
                    try: 
                        got = bool(_eval_expr(pred, st, gpio))
                    except KeyError: 
                        # A dependency not represented locally: ignoring the
                        # guard is the sound choice.
                        continue
                    if got != polarity: 
                        ok = False
                        break
                if not ok: 
                    continue
                results.add(_eval_expr(expr, st, gpio) & masks[target])
        return results

    iterations = 0
    final_potential_rows = 0
    for iteration in range(max_iterations): 
        additions: dict[str, set[int]] = {n: set() for n in names}
        potential_rows = 0
        for tr in compiled: 
            if transition_impossible_from_unary_constraints(tr): 
                continue
            potential_rows += 1
            for target, expr in tr.changed_outcomes: 
                if target not in domains: 
                    continue
                additions[target].update(outcome_values(tr, target, expr))
        changed = False
        for n in names: 
            new = additions[n] - domains[n]
            if new: 
                domains[n].update(new)
                changed = True
        iterations = iteration + 1
        final_potential_rows = potential_rows
        if not changed: 
            break
    else: 
        raise RuntimeError(f"abstract storage analysis did not converge in {max_iterations} iterations")

    # Recurrence classes are diagnostic only. Count rows still potentially
    # fireable at the fixed point.
    for tr in compiled: 
        if transition_impossible_from_unary_constraints(tr): 
            continue
        for name, cls in tr.recurrence: 
            if name in recurrence_counts: 
                recurrence_counts[name][cls] += 1

    rows: list[AbstractPersistentStateRow] = []
    for n in names: 
        vals = sorted(domains[n])
        constant = len(vals) == 1
        natural = 0 if constant else max(1, max(vals).bit_length())
        info = _information_width(len(vals))
        sparse = info if (not constant and info < natural) else None
        notes: list[str] = []
        if natural < widths[n] and not constant: 
            notes.append("natural zero-extended width narrowing is proven by Cartesian over-approximation")
        if constant: 
            notes.append("constant under Cartesian over-approximation")
        if sparse is not None: 
            notes.append("sparse encoding is a candidate only; no encoding/decoder is applied automatically")
        rows.append(AbstractPersistentStateRow(
            state = n, 
            role = specs[n].role, 
            original_width = widths[n], 
            reset = int(specs[n].reset) & masks[n], 
            reachable_values_upper_bound = vals, 
            reachable_value_count_upper_bound = len(vals), 
            natural_width = natural, 
            information_width_lower_bound_candidate = info, 
            constant = constant, 
            recurrence_classes = dict(sorted(recurrence_counts[n].items())), 
            sparse_encoding_candidate_bits = sparse, 
            notes = notes, 
        ))

    original_bits = sum(widths.values())
    natural_bits = sum(r.natural_width for r in rows)
    log2_product = sum(log2(max(1, len(domains[n]))) for n in names)
    return AbstractEventBoundaryStorageResult(
        proof_model = "FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1", 
        source_transitions = len(compiled), 
        iterations = iterations, 
        final_potential_transition_rows = final_potential_rows, 
        state_order = list(names), 
        state_rows = rows, 
        original_storage_bits = original_bits, 
        natural_storage_bits = natural_bits, 
        cartesian_value_product_log2_upper_bound = log2_product, 
        expression_full_domain_fallbacks = full_domain_fallbacks, 
        ignored_cross_state_guard_constraints = ignored_cross, 
        ignored_state_gpio_guard_constraints = ignored_state_gpio, 
        notes = [
            "Reset values are included in every abstract domain.", 
            "Transfer uses only feasibility-proven event-boundary source transitions.", 
            "Unrepresentable guard correlations are ignored, which widens rather than narrows the domains.", 
            "GPIO values are existentially enumerated from expression/guard dependency bits; no GPIO bit numbers are hard-coded.", 
            "Natural-width lowering is therefore sound for every concrete execution represented by the source transition relation.", 
            "Scheduler reachability, protocol names, transition identity, and basic-block identity are not used to justify state deletion or narrowing.", 
            "Sparse encodings are reported as candidates only and are not applied.", 
        ], 
    )


def result_to_dict(result: AbstractEventBoundaryStorageResult) -> dict[str, Any]: 
    return asdict(result)
