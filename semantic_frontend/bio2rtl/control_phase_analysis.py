from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .next_state_expr import NextStateExprIR
from .physical_state_diagnostic import PhysicalStateDiagnosticResult
from .write_guard_analysis import WriteGuardAnalysisResult


@dataclass
class SnapshotCluster: 
    source_family: str
    source_width: int
    member_families: list[str]
    member_widths: dict[str, int]
    copy_edges: list[str]
    copied_members_per_edge: dict[str, list[str]]
    members_used_in_control: int
    total_control_predicate_uses: int
    evidence: list[str]


@dataclass
class GuardChain: 
    cluster_source: str
    families: list[str]
    branch_blocks: list[int]
    literal_occurrences: dict[str, int]
    zero_prefix_occurrences: dict[str, int]
    terminal_true_occurrences: dict[str, int]
    contexts_with_2_or_more_members: int
    contexts_with_3_or_more_members: int
    max_members_in_context: int
    total_cluster_literals: int
    distinct_cluster_guard_signatures: int
    prefix_like_contexts: int
    prefix_like_fraction: float
    evidence: list[str]


@dataclass
class ControlPhaseAnalysisResult: 
    snapshot_clusters: list[SnapshotCluster]
    guard_chains: list[GuardChain]
    candidate_snapshot_clusters: int
    candidate_snapshot_bits: int
    cluster_guard_literals: int
    cluster_guard_literal_fraction_of_physical: float
    contexts_with_cluster_guards: int
    notes: list[str]


def _state_copy_source(write) -> str | None: 
    expr = write.expression
    if write.kind != "STATE_COPY": 
        return None
    if expr.kind != "STATE" or expr.state_family is None: 
        return None
    return expr.state_family


def _family_map(physical: PhysicalStateDiagnosticResult): 
    return {item.family: item for item in physical.families}


def _build_snapshot_clusters(
    physical: PhysicalStateDiagnosticResult, 
    next_state: NextStateExprIR, 
) -> list[SnapshotCluster]: 
    fam = _family_map(physical)
    copies_by_source: dict[str, list] = defaultdict(list)
    for write in next_state.writes: 
        source = _state_copy_source(write)
        if source is not None: 
            copies_by_source[source].append(write)

    clusters: list[SnapshotCluster] = []
    for source, writes in sorted(copies_by_source.items()): 
        members = sorted({w.family for w in writes if w.family != source})
        # Generic, conservative candidate rule: a common source copied into at
        # least three small persistent families.  No stack offset or protocol
        # name is used.
        small_members = [m for m in members if m in fam and fam[m].width <= 2]
        if len(small_members) < 3: 
            continue
        edge_members: dict[str, list[str]] = defaultdict(list)
        for write in writes: 
            if write.family in small_members: 
                edge_members[f"BB{write.source_block:03d}->BB{write.target_block:03d}"].append(write.family)
        control_uses = sum(fam[m].branch_predicate_count for m in small_members)
        clusters.append(
            SnapshotCluster(
                source_family = source, 
                source_width = fam[source].width if source in fam else 0, 
                member_families = small_members, 
                member_widths = {m: fam[m].width for m in small_members}, 
                copy_edges = sorted(edge_members), 
                copied_members_per_edge = {k: sorted(v) for k, v in sorted(edge_members.items())}, 
                members_used_in_control = sum(fam[m].branch_predicate_count > 0 for m in small_members), 
                total_control_predicate_uses = control_uses, 
                evidence = [
                    f"one persistent source is copied into {len(small_members)} small state families", 
                    f"{sum(fam[m].width for m in small_members)} destination state bits participate in the snapshot cluster", 
                    f"{sum(fam[m].branch_predicate_count > 0 for m in small_members)} cluster members are used by branch predicates", 
                ], 
            )
        )
    return clusters


def _literal_family(text: str) -> str | None: 
    marker = "STATE("
    pos = text.find(marker)
    if pos < 0: 
        return None
    end = text.find(")", pos + len(marker))
    if end < 0: 
        return None
    return text[pos + len(marker):end]


def _literal_level(text: str) -> int | None: 
    if text.endswith("=0"): 
        return 0
    if text.endswith("=1"): 
        return 1
    return None


def _build_guard_chain(
    cluster: SnapshotCluster, 
    guards: WriteGuardAnalysisResult, 
) -> GuardChain: 
    members = set(cluster.member_families)
    occurrence = Counter()
    zero_occurrence = Counter()
    true_occurrence = Counter()
    branch_blocks_by_family: dict[str, set[int]] = defaultdict(set)
    signatures: set[tuple[str, ...]] = set()
    contexts2 = 0
    contexts3 = 0
    max_members = 0
    prefix_like = 0
    total_literals = 0

    for context in guards.write_edges: 
        local = []
        for lit in context.residual_required_literals: 
            if lit.category != "PHYSICAL_STATE": 
                continue
            family = _literal_family(lit.text)
            if family not in members: 
                continue
            level = _literal_level(lit.text)
            local.append((lit.block, family, level, lit.truth, lit.text))
            occurrence[family] += 1
            branch_blocks_by_family[family].add(lit.block)
            total_literals += 1
            if level == 0: 
                zero_occurrence[family] += 1
            elif level == 1: 
                true_occurrence[family] += 1

        local.sort()
        unique_families = []
        seen = set()
        for _block, family, _level, _truth, _text in local: 
            if family not in seen: 
                seen.add(family)
                unique_families.append(family)
        count = len(unique_families)
        max_members = max(max_members, count)
        contexts2 += count >= 2
        contexts3 += count >= 3
        if local: 
            signatures.add(tuple(item[4] for item in local))

        # Prefix-like means earlier cluster tests are all false/zero before a
        # later member is selected, or the context falls through an all-zero
        # prefix.  This is a structural property of a priority decoder, not an
        # assumption about I2C/FSM semantics.
        if count >= 2: 
            levels = [item[2] for item in local]
            nonzero_positions = [i for i, level in enumerate(levels) if level == 1]
            if not nonzero_positions: 
                if all(level in (0, None) for level in levels): 
                    prefix_like += 1
            else: 
                first = nonzero_positions[0]
                if all(level == 0 for level in levels[:first]): 
                    prefix_like += 1

    ordered = sorted(
        cluster.member_families, 
        key = lambda m: min(branch_blocks_by_family[m]) if branch_blocks_by_family[m] else 10**9, 
    )
    branch_blocks = sorted({b for blocks in branch_blocks_by_family.values() for b in blocks})
    denominator = max(1, contexts2)
    return GuardChain(
        cluster_source = cluster.source_family, 
        families = ordered, 
        branch_blocks = branch_blocks, 
        literal_occurrences = {m: occurrence[m] for m in ordered}, 
        zero_prefix_occurrences = {m: zero_occurrence[m] for m in ordered}, 
        terminal_true_occurrences = {m: true_occurrence[m] for m in ordered}, 
        contexts_with_2_or_more_members = contexts2, 
        contexts_with_3_or_more_members = contexts3, 
        max_members_in_context = max_members, 
        total_cluster_literals = total_literals, 
        distinct_cluster_guard_signatures = len(signatures), 
        prefix_like_contexts = prefix_like, 
        prefix_like_fraction = prefix_like / denominator, 
        evidence = [
            f"cluster members appear in {contexts2} write contexts with >=2 members", 
            f"{contexts3} contexts contain >=3 cluster members", 
            f"maximum of {max_members} cluster members are tested in one write context", 
            f"{prefix_like}/{contexts2} multi-member contexts have priority-prefix-like polarity", 
        ], 
    )


def analyze_control_phases(
    physical: PhysicalStateDiagnosticResult, 
    next_state: NextStateExprIR, 
    guards: WriteGuardAnalysisResult, 
) -> ControlPhaseAnalysisResult: 
    clusters = _build_snapshot_clusters(physical, next_state)
    chains = [_build_guard_chain(cluster, guards) for cluster in clusters]
    physical_literals = guards.category_counts.get("PHYSICAL_STATE", 0)
    cluster_literals = sum(chain.total_cluster_literals for chain in chains)
    contexts = 0
    member_sets = [set(c.member_families) for c in clusters]
    for context in guards.write_edges: 
        families = {
            _literal_family(lit.text)
            for lit in context.residual_required_literals
            if lit.category == "PHYSICAL_STATE"
        }
        if any(bool(families & members) for members in member_sets): 
            contexts += 1
    return ControlPhaseAnalysisResult(
        snapshot_clusters = clusters, 
        guard_chains = chains, 
        candidate_snapshot_clusters = len(clusters), 
        candidate_snapshot_bits = sum(sum(c.member_widths.values()) for c in clusters), 
        cluster_guard_literals = cluster_literals, 
        cluster_guard_literal_fraction_of_physical = (cluster_literals / physical_literals) if physical_literals else 0.0, 
        contexts_with_cluster_guards = contexts, 
        notes = [
            "Snapshot clusters are inferred only from common STATE_COPY sources into >=3 small persistent families; stack offsets and protocol names are not used.", 
            "Guard-chain analysis is diagnostic-only and does not claim that cluster members are logically equivalent or mutually exclusive.", 
            "Prefix-like polarity is evidence for priority/control-phase decoding, not proof that destination FFs can be removed.", 
            "Generated RTL is not modified by this analysis.", 
        ], 
    )


def write_control_phase_report(result: ControlPhaseAnalysisResult, path: Path) -> None: 
    lines: list[str] = []
    lines.append("CONTROL SNAPSHOT / PHASE-CHAIN DIAGNOSTIC")
    lines.append("=" * 78)
    lines.append(f"candidate snapshot clusters      : {result.candidate_snapshot_clusters}")
    lines.append(f"candidate snapshot destination bits: {result.candidate_snapshot_bits}")
    lines.append(f"write contexts with cluster guards: {result.contexts_with_cluster_guards}")
    lines.append(f"cluster physical guard literals   : {result.cluster_guard_literals}")
    lines.append(f"share of physical guard literals  : {100.0 * result.cluster_guard_literal_fraction_of_physical:.2f}%")
    lines.append("")

    for idx, (cluster, chain) in enumerate(zip(result.snapshot_clusters, result.guard_chains), start = 1): 
        lines.append(f"CLUSTER {idx}")
        lines.append("-" * 78)
        lines.append(f"common source       : {cluster.source_family} [{cluster.source_width} bit]")
        lines.append(f"destination members : {', '.join(cluster.member_families)}")
        lines.append(f"destination bits    : {sum(cluster.member_widths.values())}")
        lines.append(f"members used control: {cluster.members_used_in_control}/{len(cluster.member_families)}")
        lines.append(f"control predicate uses: {cluster.total_control_predicate_uses}")
        lines.append("copy edges:")
        for edge, members in cluster.copied_members_per_edge.items(): 
            lines.append(f"  {edge}: {', '.join(members)}")
        lines.append("guard-chain order (by first branch block):")
        for family in chain.families: 
            lines.append(
                f"  {family:28s} uses={chain.literal_occurrences.get(family,0):3d} "
                f"zero={chain.zero_prefix_occurrences.get(family,0):3d} "
                f"one={chain.terminal_true_occurrences.get(family,0):3d}"
            )
        lines.append(f"branch blocks          : {', '.join(f'BB{x:03d}' for x in chain.branch_blocks)}")
        lines.append(f"contexts >=2 members   : {chain.contexts_with_2_or_more_members}")
        lines.append(f"contexts >=3 members   : {chain.contexts_with_3_or_more_members}")
        lines.append(f"max members/context    : {chain.max_members_in_context}")
        lines.append(f"cluster guard literals : {chain.total_cluster_literals}")
        lines.append(f"distinct guard signatures: {chain.distinct_cluster_guard_signatures}")
        lines.append(f"priority-prefix-like   : {chain.prefix_like_contexts}/{chain.contexts_with_2_or_more_members} ({100.0*chain.prefix_like_fraction:.2f}%)")
        lines.append("evidence:")
        for item in cluster.evidence + chain.evidence: 
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("NOTES")
    lines.append("-" * 78)
    for note in result.notes: 
        lines.append(f"- {note}")

    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n", encoding = "utf-8")
    Path(str(path) + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), 
        encoding = "utf-8", 
    )
