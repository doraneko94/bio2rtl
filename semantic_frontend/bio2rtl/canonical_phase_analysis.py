from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .control_phase_analysis import ControlPhaseAnalysisResult, _literal_family
from .write_guard_analysis import WriteGuardAnalysisResult


_SIMPLE_RE = re.compile(r"^STATE\(([^)]+)\)=([01])$")


@dataclass
class CanonicalPhasePredicate: 
    predicate_id: str
    kind: str
    members: list[str]
    selected_member: str | None
    required_zero_members: list[str]
    extra_literals: list[str]
    use_count: int
    original_literal_occurrences: int
    example_write_edges: list[str]


@dataclass
class CanonicalPhaseAnalysisResult: 
    cluster_source: str | None
    cluster_members: list[str]
    contexts_with_cluster_guards: int
    original_cluster_literal_occurrences: int
    distinct_original_signatures: int
    canonical_predicate_count: int
    canonical_predicate_uses: int
    simple_selector_predicates: int
    fallthrough_predicates: int
    extended_predicates: int
    reusable_zero_prefix_lengths: list[int]
    reusable_chain_selector_indices: list[int]
    direct_level_predicates: list[str]
    max_original_literals_per_predicate: int
    mean_original_literals_per_use: float
    literal_to_predicate_reduction: float
    predicates: list[CanonicalPhasePredicate]
    notes: list[str]


def _edge_name(context) -> str: 
    return f"BB{context.source_block:03d}->BB{context.target_block:03d}"


def _canonicalize(local: list, ordered_members: list[str]): 
    """Return a structural phase key without protocol-specific names.

    The key recognizes priority prefixes of small snapshot state tests:
      zeros... + terminal one => SELECT
      zeros...                => FALLTHROUGH
    Any non-bit comparison is retained as an EXTENDED suffix so the analysis
    never claims equivalence that was not established.
    """
    order = {family: i for i, family in enumerate(ordered_members)}
    simple: list[tuple[int, str, int, str]] = []
    extra: list[str] = []
    for lit in local: 
        m = _SIMPLE_RE.match(lit.text)
        family = _literal_family(lit.text)
        if m is not None and family in order: 
            simple.append((order[family], family, int(m.group(2)), lit.text))
        else: 
            extra.append(lit.text)
    simple.sort()

    families = [x[1] for x in simple]
    values = [x[2] for x in simple]
    selected = None
    zeros: list[str] = []
    base_kind = "EXACT"

    if simple: 
        ones = [i for i, value in enumerate(values) if value == 1]
        if not ones and all(value == 0 for value in values): 
            base_kind = "FALLTHROUGH"
            zeros = families[:]
        elif (
            len(ones) == 1
            and ones[0] == len(values) - 1
            and all(value == 0 for value in values[:-1])
        ): 
            base_kind = "SELECT"
            zeros = families[:-1]
            selected = families[-1]

    if extra: 
        kind = "EXTENDED_" + base_kind
    else: 
        kind = base_kind

    # Preserve exact member order and extra conditions; this is intentionally
    # conservative and therefore suitable as a diagnostic precursor to RTL.
    key = (
        kind, 
        tuple(zeros), 
        selected, 
        tuple(extra), 
        tuple((family, value) for _idx, family, value, _text in simple)
        if base_kind == "EXACT" else (), 
    )
    return key, kind, zeros, selected, extra, len(local)


def analyze_canonical_phase_predicates(
    phases: ControlPhaseAnalysisResult, 
    guards: WriteGuardAnalysisResult, 
) -> CanonicalPhaseAnalysisResult: 
    if not phases.snapshot_clusters or not phases.guard_chains: 
        return CanonicalPhaseAnalysisResult(
            cluster_source = None, 
            cluster_members = [], 
            contexts_with_cluster_guards = 0, 
            original_cluster_literal_occurrences = 0, 
            distinct_original_signatures = 0, 
            canonical_predicate_count = 0, 
            canonical_predicate_uses = 0, 
            simple_selector_predicates = 0, 
            fallthrough_predicates = 0, 
            extended_predicates = 0, 
            reusable_zero_prefix_lengths = [], 
            reusable_chain_selector_indices = [], 
            direct_level_predicates = [], 
            max_original_literals_per_predicate = 0, 
            mean_original_literals_per_use = 0.0, 
            literal_to_predicate_reduction = 0.0, 
            predicates = [], 
            notes = ["No snapshot/control cluster was available for canonical phase analysis."], 
        )

    # Current diagnostics may find multiple clusters in other programs.  Pick
    # the cluster carrying the most guard literals; no stack offset or protocol
    # name participates in this choice.
    pair = max(
        zip(phases.snapshot_clusters, phases.guard_chains), 
        key = lambda item: item[1].total_cluster_literals, 
    )
    cluster, chain = pair
    members = set(cluster.member_families)
    original_signatures: set[tuple[str, ...]] = set()
    records: dict[tuple, dict] = {}
    contexts = 0
    total_literals = 0

    for context in guards.write_edges: 
        local = [
            lit for lit in context.residual_required_literals
            if lit.category == "PHYSICAL_STATE" and _literal_family(lit.text) in members
        ]
        if not local: 
            continue
        contexts += 1
        local.sort(key = lambda lit: (lit.block, lit.text))
        total_literals += len(local)
        original_signatures.add(tuple(lit.text for lit in local))
        key, kind, zeros, selected, extra, literal_count = _canonicalize(local, chain.families)
        rec = records.setdefault(key, {
            "kind": kind, 
            "zeros": zeros, 
            "selected": selected, 
            "extra": extra, 
            "uses": 0, 
            "literals": 0, 
            "edges": [], 
            "members": sorted({_literal_family(lit.text) for lit in local if _literal_family(lit.text)}), 
        })
        rec["uses"] += 1
        rec["literals"] += literal_count
        rec["edges"].append(_edge_name(context))

    predicates: list[CanonicalPhasePredicate] = []
    for idx, (_key, rec) in enumerate(
        sorted(records.items(), key = lambda kv: (-kv[1]["uses"], str(kv[0]))), start = 1
    ): 
        predicates.append(CanonicalPhasePredicate(
            predicate_id = f"PHASE_P{idx:02d}", 
            kind = rec["kind"], 
            members = rec["members"], 
            selected_member = rec["selected"], 
            required_zero_members = rec["zeros"], 
            extra_literals = rec["extra"], 
            use_count = rec["uses"], 
            original_literal_occurrences = rec["literals"], 
            example_write_edges = rec["edges"][:8], 
        ))

    n = len(predicates)
    uses = sum(p.use_count for p in predicates)
    max_literals = max((p.original_literal_occurrences // p.use_count for p in predicates), default = 0)

    # Measure a reusable priority-chain basis.  PREFIX_k means the first k
    # ordered members are zero; SELECT_k means PREFIX_k and member k is one.
    # Direct level tests that do not carry the preceding prefix are kept
    # separate rather than being incorrectly promoted to phase selectors.
    prefix_lengths: set[int] = set()
    selector_indices: set[int] = set()
    direct_levels: set[str] = set()
    position = {family: i for i, family in enumerate(chain.families)}
    for pred in predicates: 
        if pred.kind == "FALLTHROUGH": 
            expected = chain.families[:len(pred.required_zero_members)]
            if pred.required_zero_members == expected: 
                prefix_lengths.add(len(expected))
            elif len(pred.required_zero_members) == 1: 
                direct_levels.add(f"{pred.required_zero_members[0]}=0")
        elif pred.kind == "SELECT" and pred.selected_member is not None: 
            idx = position[pred.selected_member]
            if pred.required_zero_members == chain.families[:idx]: 
                selector_indices.add(idx)
                if idx > 0: 
                    prefix_lengths.add(idx)
            elif not pred.required_zero_members: 
                direct_levels.add(f"{pred.selected_member}=1")

    return CanonicalPhaseAnalysisResult(
        cluster_source = cluster.source_family, 
        cluster_members = chain.families, 
        contexts_with_cluster_guards = contexts, 
        original_cluster_literal_occurrences = total_literals, 
        distinct_original_signatures = len(original_signatures), 
        canonical_predicate_count = n, 
        canonical_predicate_uses = uses, 
        simple_selector_predicates = sum(p.kind == "SELECT" for p in predicates), 
        fallthrough_predicates = sum(p.kind == "FALLTHROUGH" for p in predicates), 
        extended_predicates = sum(p.kind.startswith("EXTENDED_") or p.kind == "EXACT" for p in predicates), 
        reusable_zero_prefix_lengths = sorted(prefix_lengths), 
        reusable_chain_selector_indices = sorted(selector_indices), 
        direct_level_predicates = sorted(direct_levels), 
        max_original_literals_per_predicate = max_literals, 
        mean_original_literals_per_use = (total_literals / uses) if uses else 0.0, 
        literal_to_predicate_reduction = (1.0 - uses / total_literals) if total_literals else 0.0, 
        predicates = predicates, 
        notes = [
            "Canonical predicates are inferred structurally from priority-prefix snapshot guards; no I2C phase names or stack offsets are hard-coded.", 
            "One canonical predicate use can stand for several repeated physical-state literals in a write guard.", 
            "Extended predicates retain non-bit comparisons rather than assuming they are equivalent to simple phase selectors.", 
            "This is diagnostic-only: physical FFs and generated RTL are not modified.", 
        ], 
    )


def write_canonical_phase_report(result: CanonicalPhaseAnalysisResult, path: Path) -> None: 
    lines: list[str] = []
    lines.append("CANONICAL CONTROL-PHASE PREDICATE DIAGNOSTIC")
    lines.append("=" * 78)
    lines.append(f"cluster source                    : {result.cluster_source}")
    lines.append(f"cluster members                   : {', '.join(result.cluster_members)}")
    lines.append(f"contexts with cluster guards      : {result.contexts_with_cluster_guards}")
    lines.append(f"original cluster literal uses     : {result.original_cluster_literal_occurrences}")
    lines.append(f"distinct original signatures      : {result.distinct_original_signatures}")
    lines.append(f"canonical phase predicates        : {result.canonical_predicate_count}")
    lines.append(f"canonical predicate uses          : {result.canonical_predicate_uses}")
    lines.append(f"selector / fallthrough / extended : {result.simple_selector_predicates} / {result.fallthrough_predicates} / {result.extended_predicates}")
    lines.append(f"reusable zero-prefix lengths      : {result.reusable_zero_prefix_lengths}")
    lines.append(f"reusable chain selector indices   : {result.reusable_chain_selector_indices}")
    lines.append(f"direct non-prefix level predicates: {', '.join(result.direct_level_predicates)}")
    lines.append(f"mean literals represented/use     : {result.mean_original_literals_per_use:.2f}")
    lines.append(f"literal->predicate occurrence reduction: {100.0*result.literal_to_predicate_reduction:.2f}%")
    lines.append("")

    for pred in result.predicates: 
        lines.append(f"{pred.predicate_id}  {pred.kind}  uses={pred.use_count}  original_literals={pred.original_literal_occurrences}")
        if pred.required_zero_members: 
            lines.append(f"  zero-prefix : {', '.join(pred.required_zero_members)}")
        if pred.selected_member: 
            lines.append(f"  selected    : {pred.selected_member}")
        if pred.extra_literals: 
            lines.append(f"  extra       : {' ; '.join(pred.extra_literals)}")
        lines.append(f"  members     : {', '.join(pred.members)}")
        lines.append(f"  write edges : {', '.join(pred.example_write_edges)}")
        lines.append("")

    lines.append("NOTES")
    lines.append("-" * 78)
    for note in result.notes: 
        lines.append(f"- {note}")

    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n", encoding = "utf-8")
    Path(str(path) + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), encoding = "utf-8"
    )
