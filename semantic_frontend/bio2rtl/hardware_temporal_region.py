from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .semantic_microstate import SemanticMicrostateResult


@dataclass(frozen = True)
class SoftwareSampleClassification: 
    sample_block: int
    region: str
    kind: str
    exit_samples: tuple[int, ...]
    branch_blocks: tuple[int, ...]
    gpio_action_blocks: tuple[int, ...]
    internal_cycle_blocks: tuple[int, ...]


@dataclass(frozen = True)
class HardwareTemporalEventRegion: 
    event_id: str
    source_region: str
    gpio_bit: int
    edge: str
    qualifiers: tuple[str, ...]
    fused_transparent_samples: tuple[int, ...]
    polling_resample_eliminated: bool


@dataclass
class HardwareTemporalRegionIR: 
    software_sample_points: int
    canonical_hardware_events: int
    event_polling_roots: tuple[int, ...]
    transparent_sample_barriers: tuple[int, ...]
    startup_or_control_samples: tuple[int, ...]
    event_regions: list[HardwareTemporalEventRegion]
    sample_classifications: list[SoftwareSampleClassification]
    cfg_free_temporal_partition_ready: bool
    blockers: list[str]
    notes: list[str]


def _sample_region_name(block: int) -> str: 
    return f"SAMPLE_BB{block:03d}"


def _transparent(region) -> bool: 
    if region.start_block is None: 
        return False
    exits = set(region.exit_sample_blocks)
    return (
        not region.branch_blocks
        and not region.gpio_action_blocks
        and not region.internal_cycle_blocks
        and len(exits) == 1
        and region.start_block not in exits
    )


def _follow_transparent_samples(
    start_sample: int, 
    microstates: SemanticMicrostateResult, 
    event_root_samples: set[int], 
) -> tuple[tuple[int, ...], int | None, bool]: 
    """Follow input-only sample regions until a nontransparent/root sample.

    Returns (fused_samples, terminal_sample, cycle_detected).  Event-root
    samples are boundaries of the hardware-time graph and are not themselves
    added to the fused list.
    """
    fused: list[int] = []
    seen: set[int] = set()
    current = start_sample
    while current not in event_root_samples: 
        if current in seen: 
            return tuple(fused), current, True
        seen.add(current)
        region = microstates.regions.get(_sample_region_name(current))
        if region is None or not _transparent(region): 
            return tuple(fused), current, False
        fused.append(current)
        current = next(iter(region.exit_sample_blocks))
    return tuple(fused), current, False


def build_hardware_temporal_regions(
    microstates: SemanticMicrostateResult, 
    canonical_events: CanonicalEventAnalysisResult, 
) -> HardwareTemporalRegionIR: 
    """Re-partition software GPIO sampling into hardware event time.

    This is intentionally a temporal-architecture IR, not an RTL transform.
    GPIO_READ sample points are classified as either event-polling roots,
    transparent input-only barriers that can be fused into an event macro-step,
    or startup/control samples that still require separate treatment.
    """
    event_root_samples: set[int] = set()
    for event in canonical_events.canonical_events: 
        if event.region.startswith("SAMPLE_BB"): 
            try: 
                event_root_samples.add(int(event.region.removeprefix("SAMPLE_BB")))
            except ValueError: 
                pass

    transparent_samples: set[int] = set()
    classifications: list[SoftwareSampleClassification] = []
    startup_or_control: set[int] = set()

    for sample in sorted(microstates.sample_blocks): 
        region = microstates.regions[_sample_region_name(sample)]
        if sample in event_root_samples: 
            kind = "EVENT_POLLING_ROOT"
        elif _transparent(region): 
            kind = "TRANSPARENT_INPUT_BARRIER"
            transparent_samples.add(sample)
        else: 
            kind = "STARTUP_OR_CONTROL_SAMPLE"
            startup_or_control.add(sample)
        classifications.append(
            SoftwareSampleClassification(
                sample_block = sample, 
                region = region.name, 
                kind = kind, 
                exit_samples = tuple(sorted(region.exit_sample_blocks)), 
                branch_blocks = tuple(sorted(region.branch_blocks)), 
                gpio_action_blocks = tuple(sorted(region.gpio_action_blocks)), 
                internal_cycle_blocks = tuple(sorted(region.internal_cycle_blocks)), 
            )
        )

    blockers: list[str] = []
    event_regions: list[HardwareTemporalEventRegion] = []
    for event in canonical_events.canonical_events: 
        if not event.region.startswith("SAMPLE_BB"): 
            blockers.append(f"{event.event_id}: source region is not a GPIO sample region")
            continue
        root = int(event.region.removeprefix("SAMPLE_BB"))
        region = microstates.regions.get(event.region)
        if region is None: 
            blockers.append(f"{event.event_id}: missing source semantic region {event.region}")
            continue

        fused: set[int] = set()
        bad_exit = False
        for exit_sample in sorted(region.exit_sample_blocks): 
            if exit_sample == root: 
                # Re-reading the polling GPIO is software time, not a distinct
                # hardware event boundary.
                continue
            chain, terminal, cycle = _follow_transparent_samples(
                exit_sample, microstates, event_root_samples, 
            )
            fused.update(chain)
            if cycle: 
                blockers.append(
                    f"{event.event_id}: transparent sample chain cycles at BB{terminal:03d}"
                )
                bad_exit = True
            elif terminal not in event_root_samples: 
                blockers.append(
                    f"{event.event_id}: exit BB{exit_sample:03d} reaches unresolved sample BB{terminal:03d}"
                )
                bad_exit = True

        event_regions.append(
            HardwareTemporalEventRegion(
                event_id = event.event_id, 
                source_region = event.region, 
                gpio_bit = event.gpio_bit, 
                edge = event.edge, 
                qualifiers = tuple(event.qualifiers), 
                fused_transparent_samples = tuple(sorted(fused)), 
                polling_resample_eliminated = (root in region.exit_sample_blocks), 
            )
        )
        if bad_exit: 
            continue

    if not canonical_events.canonical_events: 
        blockers.append("no canonical hardware events recovered")

    # Startup/control samples are not blockers for the steady-state event
    # partition.  They remain an explicit initialization/control prelude and
    # must be lowered separately before a full-program CFG-free backend exists.
    ready = bool(event_regions) and not blockers

    return HardwareTemporalRegionIR(
        software_sample_points = len(microstates.sample_blocks), 
        canonical_hardware_events = len(canonical_events.canonical_events), 
        event_polling_roots = tuple(sorted(event_root_samples)), 
        transparent_sample_barriers = tuple(sorted(transparent_samples)), 
        startup_or_control_samples = tuple(sorted(startup_or_control)), 
        event_regions = event_regions, 
        sample_classifications = classifications, 
        cfg_free_temporal_partition_ready = ready, 
        blockers = blockers, 
        notes = [
            "GPIO_READ is treated as software observation, not automatically as hardware time.", 
            "Canonical GPIO edge events replace polling-root re-samples as steady-state hardware temporal boundaries.", 
            "Input-only straight-line sample regions with one exit are fused transparently across the event macro-step.", 
            "Startup/control sampling is retained separately and is not silently folded into steady-state event time.", 
            "This IR contains no reach/edge predicate identity; basic-block numbers are provenance only.", 
        ], 
    )


def write_hardware_temporal_region_report(result: HardwareTemporalRegionIR, path: Path) -> None: 
    lines = [
        "HARDWARE TEMPORAL REGION / SOFTWARE-TIME ELIMINATION", 
        "=" * 78, 
        f"software GPIO sample points      : {result.software_sample_points}", 
        f"canonical hardware events       : {result.canonical_hardware_events}", 
        "event polling roots              : " + (", ".join(f"BB{x:03d}" for x in result.event_polling_roots) or "-"), 
        "transparent sample barriers      : " + (", ".join(f"BB{x:03d}" for x in result.transparent_sample_barriers) or "-"), 
        "startup/control sample points    : " + (", ".join(f"BB{x:03d}" for x in result.startup_or_control_samples) or "-"), 
        f"steady event partition ready    : {result.cfg_free_temporal_partition_ready}", 
        "", 
        "SOFTWARE SAMPLE CLASSIFICATION", 
        "-" * 78, 
    ]
    for row in result.sample_classifications: 
        lines += [
            f"BB{row.sample_block:03d}  {row.kind}", 
            "  exits        : " + (", ".join(f"BB{x:03d}" for x in row.exit_samples) or "-"), 
            "  branches     : " + (", ".join(f"BB{x:03d}" for x in row.branch_blocks) or "-"), 
            "  GPIO effects : " + (", ".join(f"BB{x:03d}" for x in row.gpio_action_blocks) or "-"), 
            "", 
        ]
    lines += ["HARDWARE EVENT TEMPORAL REGIONS", "-" * 78]
    for row in result.event_regions: 
        lines += [
            f"{row.event_id}: GPIO[{row.gpio_bit}] {row.edge}", 
            f"  software polling region      : {row.source_region}", 
            "  qualifiers                   : " + (", ".join(row.qualifiers) or "-"), 
            "  fused transparent samples    : " + (", ".join(f"BB{x:03d}" for x in row.fused_transparent_samples) or "-"), 
            f"  polling re-sample eliminated : {row.polling_resample_eliminated}", 
            "", 
        ]
    if result.blockers: 
        lines += ["BLOCKERS", "-" * 78] + [f"- {x}" for x in result.blockers] + [""]
    lines += ["Notes", "-" * 78] + [f"- {x}" for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True)
    )
