import argparse
from pathlib import Path

from .analysis import analyze
from .bitwidth import analyze_bit_widths
from .carrier_update import analyze_register_updates
from .cfg import build_cfg
from .dff_candidates import select_dff_candidates
from .dff_width_infer import infer_dff_widths
from .edge_state import build_edge_state_ir
from .edge_state_semantics import analyze_edge_state_semantics
from .edge_event_analysis import analyze_edge_events, write_edge_event_report
from .write_guard_analysis import analyze_write_guards, write_write_guard_report
from .control_phase_analysis import analyze_control_phases, write_control_phase_report
from .canonical_phase_analysis import analyze_canonical_phase_predicates, write_canonical_phase_report
from .final_state import build_final_state
from .fsm_ir import build_fsm_ir
from .functions import discover_functions
from .gpio_effect import build_gpio_effects
from .hardware_predicate_analysis import analyze_hardware_predicates, write_hardware_predicate_report
from .event_hardware_predicate_analysis import analyze_event_hardware_predicates, write_event_hardware_predicate_report
from .event_vector_analysis import analyze_event_vectors, write_event_vector_report
from .event_vector_predicate_analysis import analyze_event_vector_predicates, write_event_vector_predicate_report
from .event_vector_transition_analysis import analyze_event_vector_transitions, write_event_vector_transition_report
from .event_vector_decision_dag import analyze_event_vector_decision_dag, write_event_vector_decision_dag_report
from .event_vector_sequence_dag import analyze_event_vector_sequence_dag, write_event_vector_sequence_dag_report
from .polling_phase_event_analysis import analyze_polling_phase_events, write_polling_phase_event_report
from .hardware_event_partition_analysis import analyze_hardware_event_partition, write_hardware_event_partition_report
from .wait_poll_elimination_analysis import analyze_wait_poll_elimination, write_wait_poll_elimination_report
from .epsilon_transition_analysis import analyze_epsilon_transitions, write_epsilon_transition_report
from .semantic_state_role_analysis import analyze_semantic_state_roles, write_semantic_state_role_report
from .feasible_transition_core import analyze_feasible_transition_core, write_feasible_transition_core_report
from .feasible_symbolic_executor import analyze_feasible_symbolic_executor, write_feasible_symbolic_executor_report
from .dedicated_transition_core_ir import build_dedicated_transition_core_ir, write_dedicated_transition_core_ir
from .hardware_pattern import analyze_hardware_patterns
from .hardware_object_analysis import analyze_hardware_objects, write_hardware_object_report
from .hardware_behavior_ir import build_hardware_behavior_ir
from .hardware_behavior_report import write_hardware_behavior_report
from .hardware_enable_analysis import analyze_hardware_enables, write_hardware_enable_report
from .definition_site_analysis import analyze_definition_sites, write_definition_site_report
from .canonical_event_analysis import analyze_canonical_events, write_canonical_event_report
from .dedicated_hardware_ir import build_dedicated_hardware_ir
from .dedicated_hardware_report import write_dedicated_hardware_report
from .event_transition_ir import build_event_transition_ir
from .event_transition_report import write_event_transition_report
from .event_domain_analysis import analyze_event_domains, write_event_domain_report
from .event_effect_analysis import analyze_event_gpio_effects, write_event_effect_report
from .event_phase_composition import analyze_event_phase_composition, write_event_phase_composition_report
from .event_phase_proof import analyze_event_phase_proofs, write_event_phase_proof_report
from .event_macro_transition_analysis import analyze_event_macro_transitions, write_event_macro_transition_report
from .event_macro_full_analysis import analyze_full_event_macro_transitions, write_full_event_macro_report
from .control_automaton_analysis import analyze_control_automaton, write_control_automaton_report
from .boundary_range_analysis import analyze_boundary_ranges, write_boundary_range_report
from .boundary_state_prune import apply_constant_boundary_state_pruning
from .event_liveness_analysis import analyze_event_boundary_liveness, write_event_liveness_report
from .hardware_temporal_region import build_hardware_temporal_regions, write_hardware_temporal_region_report
from .hardware_event_transition_analysis import analyze_hardware_event_transitions, write_hardware_event_transition_report
from .definition_schedule_analysis import analyze_definition_schedules, write_definition_schedule_report
from .definition_site_ir import build_definition_site_ir
from .definition_site_equivalence import analyze_definition_site_equivalence, write_definition_site_equivalence_report
from .definition_guard_analysis import analyze_definition_guards, write_definition_guard_report
from .selective_hoist_analysis import analyze_selective_hoists, write_selective_hoist_report
from .liveout_hoist_analysis import analyze_liveout_hoists, write_liveout_hoist_report
from .sample_barrier_analysis import analyze_sample_barriers, write_sample_barrier_report
from .sample_barrier_fusion_analysis import analyze_sample_barrier_fusion, write_sample_barrier_fusion_report
from .barrier_retime_analysis import analyze_barrier_retime, write_barrier_retime_report
from .proven_hoist_plan import build_proven_hoist_plan
from .inline_calls import inline_helper_functions
from .logical_state import analyze_logical_states
from .loop_bound import analyze_family_loop_bounds
from .lower import lower_cfg, require_supported_ir
from .next_state_expr import (
    build_next_state_expr_ir, 
    semantic_value_roots, 
)
from .observable_cone import trace_observable_cone
from .physical_state_diagnostic import (
    analyze_physical_state_diagnostic, 
    write_physical_state_report, 
)
from .parser import parse_disassembly
from .register_ssa import convert_to_ssa
from .control_predicate_analysis import (
    analyze_control_predicates, 
    print_control_predicates, 
)
from .rtl_ir import build_rtl_ir
from .semantic_boundary import (
    analyze_semantic_boundaries, 
    print_semantic_boundaries, 
)
from .semantic_microstate import (
    build_semantic_microstates, 
    print_semantic_microstates, 
)
from .semantic_next_state import (
    build_semantic_next_state, 
    print_semantic_next_state, 
)
from .semantic_reach import (
    build_semantic_reach, 
    print_semantic_reach, 
)
from .semantic_ssa import (
    build_semantic_ssa, 
    build_transition_semantic_ssa, 
    print_semantic_ssa, 
)
from .semantic_systemverilog import (
    emit_semantic_systemverilog, 
)
from .ssa_opt import optimize_ssa
from .stack_lowering import analyze_sp
from .stack_ssa import promote_stack_to_state
from .state_extract import classify_phis
from .state_opt import eliminate_dead_states
from .state_ssa import convert_states_to_ssa
from .structural_netlist import build_structural_netlist


# ============================================================
# Helpers
# ============================================================


def print_header(
    text: str, 
) -> None: 

    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def require(
    condition: bool, 
    message: str, 
) -> None: 

    if not condition: 

        raise RuntimeError(
            message
        )


# ============================================================
# Main
# ============================================================


def main() -> None: 

    parser = argparse.ArgumentParser(
        description = (
            "Convert Baochip BIO RISC-V disassembly "
            "into application-independent semantic RTL IR"
        )
    )

    parser.add_argument(
        "disassembly", 
        type = Path, 
        help = "BIO .dis file", 
    )

    parser.add_argument(
        "-o", 
        "--output", 
        type = Path, 
        default = Path("generated/bio2rtl_generated.sv"), 
        help = (
            "output SystemVerilog path "
            "(default: generated/bio2rtl_generated.sv)"
        ), 
    )

    parser.add_argument(
        "--state-report", 
        type = Path, 
        default = None, 
        help = (
            "write a generic physical-state semantic diagnostic report "
            "(and a JSON sidecar) without changing RTL generation"
        ), 
    )

    parser.add_argument(
        "--canonical-event-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that merges multiple CFG spellings "
            "of the same physical GPIO edge into canonical hardware events"
        ), 
    )

    parser.add_argument(
        "--event-report", 
        type = Path, 
        default = None, 
        help = (
            "write a generic GPIO edge-event recovery diagnostic report "
            "(and a JSON sidecar) without changing RTL generation"
        ), 
    )

    parser.add_argument(
        "--write-guard-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic mapping physical next-state write "
            "guards to recovered GPIO edge events and residual control predicates"
        ), 
    )

    parser.add_argument(
        "--control-phase-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic for common-source control snapshots "
            "and priority/phase guard chains"
        ), 
    )

    parser.add_argument(
        "--canonical-phase-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that canonicalizes repeated "
            "snapshot/control guard prefixes into semantic phase predicates"
        ), 
    )

    parser.add_argument(
        "--event-vector-transition-report", 
        type = Path, 
        default = None, 
        help = "write simultaneous event-vector macro-transition diagnostic", 
    )

    parser.add_argument(
        "--event-vector-predicate-report", 
        type = Path, 
        default = None, 
        help = "write event-vector-conditioned CFG-free hardware predicate diagnostic", 
    )

    parser.add_argument(
        "--event-vector-decision-dag-report", 
        type = Path, 
        default = None, 
        help = "write reduced CFG-free hardware decision DAG diagnostic per event vector", 
    )

    parser.add_argument(
        "--epsilon-transition-report", 
        type = Path, 
        default = None, 
        help = "write event-free epsilon transition diagnostic", 
    )

    parser.add_argument(
        "--wait-poll-elimination-report", 
        type = Path, 
        default = None, 
        help = "prove wait-only polling paths are removable from dedicated hardware", 
    )

    parser.add_argument(
        "--hardware-event-partition-report", 
        type = Path, 
        default = None, 
        help = "write unified history-edge/polling-phase hardware event partition diagnostic", 
    )

    parser.add_argument(
        "--polling-phase-event-report", 
        type = Path, 
        default = None, 
        help = "write polling-phase edge-event recovery diagnostic", 
    )

    parser.add_argument(
        "--event-vector-sequence-dag-report", 
        type = Path, 
        default = None, 
        help = "write ordered CFG-free hardware decision DAG diagnostic per event vector", 
    )

    parser.add_argument(
        "--semantic-state-role-report", 
        type = Path, 
        default = None, 
        help = "write transition-local semantic state role diagnostic", 
    )

    parser.add_argument(
        "--feasible-transition-core-report", 
        type = Path, 
        default = None, 
        help = "write feasibility-pruned CFG-free dedicated hardware transition-core diagnostic", 
    )

    parser.add_argument(
        "--feasible-symbolic-executor-report", 
        type = Path, 
        default = None, 
        help = "write feasibility-aware symbolic hardware executor diagnostic", 
    )

    parser.add_argument(
        "--dedicated-transition-core-ir", 
        type = Path, 
        default = None, 
        help = "write feasibility-pruned CFG-free dedicated transition core IR JSON", 
    )

    parser.add_argument(
        "--event-vector-report", 
        type = Path, 
        default = None, 
        help = "write simultaneous recovered hardware event-vector diagnostic", 
    )

    parser.add_argument(
        "--hardware-predicate-report", 
        type = Path, 
        default = None, 
        help = "normalize residual CFG predicates into hardware-expression identities", 
    )

    parser.add_argument(
        "--event-hardware-predicate-report", 
        type = Path, 
        default = None, 
        help = "write event-conditioned CFG-free hardware predicate diagnostic", 
    )

    parser.add_argument(
        "--definition-site-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic comparing original STATE_DEF sites "
            "with edge-expanded hardware update occurrences"
        ), 
    )

    parser.add_argument(
        "--barrier-retime-report", 
        type = Path, 
        default = None, 
        help = (
            "retime pending state updates across proven-pure sample barriers "
            "and re-run exhaustive selective-hoist equivalence"
        ), 
    )

    parser.add_argument(
        "--sample-barrier-fusion-report", 
        type = Path, 
        default = None, 
        help = (
            "prove whether cross-sample pending updates may be retimed across "
            "input-only straight-line sampling regions"
        ), 
    )

    parser.add_argument(
        "--sample-barrier-report", 
        type = Path, 
        default = None, 
        help = (
            "diagnose hardware-update expressions that are carried across "
            "semantic GPIO-sampling regions before persistent commit"
        ), 
    )

    parser.add_argument(
        "--liveout-hoist-report", 
        type = Path, 
        default = None, 
        help = (
            "split duplicated state updates into original operations and "
            "boundary live-out retention, then exhaustively verify guarded hoists"
        ), 
    )

    parser.add_argument(
        "--selective-hoist-report", 
        type = Path, 
        default = None, 
        help = (
            "test definition-site hoisting only for update forms with one "
            "original definition and multiple edge-exit copies"
        ), 
    )

    parser.add_argument(
        "--definition-guard-report", 
        type = Path, 
        default = None, 
        help = (
            "measure dedicated-hardware enable predicates at original state "
            "definition sites, excluding downstream CPU-control branches"
        ), 
    )

    parser.add_argument(
        "--definition-site-equivalence-report", 
        type = Path, 
        default = None, 
        help = (
            "symbolically compare established edge-write state semantics with "
            "definition-site state semantics over every semantic-region path"
        ), 
    )

    parser.add_argument(
        "--definition-schedule-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic recovering canonical hardware-event "
            "schedules at original STATE_DEF sites before exit-edge duplication"
        ), 
    )

    parser.add_argument(
        "--dedicated-hardware-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only Dedicated Hardware IR report that attaches "
            "canonical physical events to grouped hardware update rules"
        ), 
    )

    parser.add_argument(
        "--event-transition-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only Event Transition IR report that groups original "
            "STATE_DEF operations under recovered physical events, before CFG-free lowering"
        ), 
    )

    parser.add_argument(
        "--event-domain-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that classifies persistent state by recovered physical event domain"
        ), 
    )

    parser.add_argument(
        "--event-effect-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that attributes externally visible GPIO effects and their state dependencies to recovered events"
        ), 
    )

    parser.add_argument(
        "--event-phase-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic for composing unobservable updates across complementary physical-event phases"
        ), 
    )

    parser.add_argument(
        "--event-phase-proof-report", 
        type = Path, 
        default = None, 
        help = (
            "write a proof-oriented diagnostic for eliminating unobservable intermediate state between complementary physical-event phases"
        ), 
    )

    parser.add_argument(
        "--event-macro-transition-report", 
        type = Path, 
        default = None, 
        help = (
            "write a diagnostic that collapses CPU CFG paths under each canonical event by identical final persistent-state transition expressions"
        ), 
    )

    parser.add_argument(
        "--boundary-range-report", 
        type = Path, 
        default = None, 
        help = ("prove semantic-boundary boolean/range invariants for persistent state"), 
    )

    parser.add_argument(
        "--control-automaton-report", 
        type = Path, 
        default = None, 
        help = (
            "write a diagnostic that classifies event-macro control outcomes as HOLD/CONST/COPY/complex transition templates"
        ), 
    )

    parser.add_argument(
        "--event-macro-full-report", 
        type = Path, 
        default = None, 
        help = (
            "write an exact macro-step collapse diagnostic including physical/semantic state, next sample boundary, and GPIO effects"
        ), 
    )



    parser.add_argument(
        "--hardware-event-transition-report", 
        type = Path, 
        default = None, 
        help = (
            "symbolically fuse transparent GPIO sample barriers and report "
            "canonical-event-to-event-wait persistent-state transitions"
        ), 
    )

    parser.add_argument(
        "--hardware-temporal-region-report", 
        type = Path, 
        default = None, 
        help = (
            "re-partition GPIO_READ software sampling into canonical hardware-event "
            "time and report transparent/startup sample regions without changing RTL"
        ), 
    )

    parser.add_argument(
        "--event-liveness-report", 
        type = Path, 
        default = None, 
        help = (
            "write an event-boundary liveness diagnostic identifying physical states that truly require values from before a canonical event"
        ), 
    )

    parser.add_argument(
        "--behavior-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only Behavioral Hardware IR report that groups "
            "identical CFG-selected state updates into hardware update rules"
        ), 
    )

    parser.add_argument(
        "--hardware-enable-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that ORs exact CFG path guards for "
            "identical hardware updates and measures Boolean path collapse"
        ), 
    )

    parser.add_argument(
        "--hardware-object-report", 
        type = Path, 
        default = None, 
        help = (
            "write a read-only diagnostic that measures how many CFG-selected "
            "state updates collapse into application-independent hardware objects"
        ), 
    )

    parser.add_argument(
        "--backend", 
        choices = ("edge", "folded", "defsite", "proven-hw", "boundary-pruned", "event-local"), 
        default = "edge", 
        help = (
            "physical-state backend: edge keeps the established "
            "reach/edge-selected writes; folded symbolically folds "
            "CFG path guards; defsite emits persistent-state updates at "
            "their original SSA definition blocks; proven-hw applies only "
            "sample-barrier retimes and definition hoists that passed exhaustive proof; boundary-pruned removes only reset-zero persistent states proven constant at every semantic boundary; event-local reruns semantic lowering after demoting boundary-constant CPU-temporary states before RTL construction"
        ), 
    )

    args = parser.parse_args()


    # ========================================================
    # 1. Disassembly -> CFG -> hardware-oriented IR
    # ========================================================

    dis = parse_disassembly(
        args.disassembly
    )

    analysis = analyze(
        dis
    )

    cfg = build_cfg(
        dis
    )

    ir_blocks = lower_cfg(
        cfg
    )
    # Generic Ver.1 is fail-closed: unsupported instructions/branches may not
    # survive into semantic analyses where they could be accidentally ignored.
    require_supported_ir(ir_blocks)

    function_analysis = (
        discover_functions(
            ir_blocks
        )
    )


    # ========================================================
    # 2. Stack memory -> explicit candidate hardware state
    # ========================================================

    sp_analysis = analyze_sp(
        ir_blocks, 
        function_analysis, 
    )

    promoted = promote_stack_to_state(
        ir_blocks, 
        sp_analysis, 
    )

    require(
        promoted.remaining_loads == 0, 
        "stack promotion left LOAD operations", 
    )

    require(
        promoted.remaining_stores == 0, 
        "stack promotion left STORE operations", 
    )

    require(
        not promoted.unresolved_addresses, 
        "stack promotion left unresolved addresses", 
    )


    dead_state = eliminate_dead_states(
        promoted
    )

    require(
        not dead_state.dangling_state_reads, 
        (
            "dead-state elimination "
            "produced dangling state reads"
        ), 
    )


    # ========================================================
    # 3. Inline helper functions -> register/state SSA
    # ========================================================

    inlined = inline_helper_functions(
        dead_state.blocks, 
        function_analysis, 
    )

    require(
        inlined.remaining_calls == 0, 
        (
            "CALL remains after "
            "helper-function inlining"
        ), 
    )

    require(
        inlined.remaining_returns == 0, 
        (
            "RETURN remains after "
            "helper-function inlining"
        ), 
    )


    ssa = convert_to_ssa(
        inlined.blocks
    )

    require(
        not ssa.unversioned_defs, 
        (
            "register SSA has "
            "unversioned definitions"
        ), 
    )

    require(
        not ssa.unversioned_uses, 
        (
            "register SSA has "
            "unversioned uses"
        ), 
    )


    state_ssa = convert_states_to_ssa(
        ssa.blocks
    )

    require(
        state_ssa.remaining_state_writes == 0, 
        (
            "STATE_WRITE remains after "
            "state SSA conversion"
        ), 
    )

    require(
        not state_ssa.unversioned_state_defs, 
        (
            "state SSA has "
            "unversioned definitions"
        ), 
    )

    require(
        not state_ssa.unversioned_state_uses, 
        (
            "state SSA has "
            "unversioned uses"
        ), 
    )


    optimized = optimize_ssa(
        state_ssa.blocks
    )

    require(
        not optimized.missing_definitions, 
        (
            "SSA optimization produced "
            "missing definitions"
        ), 
    )

    require(
        not optimized.duplicate_definitions, 
        (
            "SSA optimization produced "
            "duplicate definitions"
        ), 
    )


    # ========================================================
    # 4. Recover persistent hardware state
    #
    # No I2C-specific knowledge is used.
    # ========================================================

    extracted = classify_phis(
        optimized.blocks
    )

    logical = analyze_logical_states(
        extracted
    )

    require(
        (
            logical.grouped_state_phis
            == logical.total_state_phis
        ), 
        (
            "not all state PHIs were grouped "
            "into logical families"
        ), 
    )

    require(
        not logical.unknown_state_phis, 
        (
            "unknown logical-state "
            "PHIs remain"
        ), 
    )


    observable = trace_observable_cone(
        optimized.blocks, 
        extracted, 
    )

    register_updates = (
        analyze_register_updates(
            optimized.blocks, 
            observable, 
        )
    )

    semantic_boundaries_early = (
        analyze_semantic_boundaries(
            optimized.blocks
        )
    )

    dff_candidates = (
        select_dff_candidates(
            observable, 
            logical, 
            register_updates, 
        )
    )

    bitwidth = analyze_bit_widths(
        optimized.blocks, 
        dff_candidates, 
    )

    inferred_widths = infer_dff_widths(
        optimized.blocks, 
        dff_candidates, 
        bitwidth, 
    )

    require(
        not inferred_widths.unknown_families, 
        (
            "DFF width inference "
            "has UNKNOWN families"
        ), 
    )


    loop_bounds = analyze_family_loop_bounds(
        optimized.blocks, 
        set(
            inferred_widths
            .bound_required_families
        ), 
    )

    final_state = build_final_state(
        inferred_widths, 
        loop_bounds, 
    )

    require(
        not final_state.unproven, 
        (
            "final hardware state still "
            "contains unproven families"
        ), 
    )


    # ========================================================
    # 5. Recover edge-specific persistent-state semantics
    # ========================================================

    fsm = build_fsm_ir(
        optimized.blocks
    )


    edge_state = build_edge_state_ir(
        optimized.blocks, 
        final_state, 
    )

    require(
        set(final_state.states)
        <= edge_state.written_families, 
        (
            "edge-state IR does not cover "
            "all final-state families"
        ), 
    )


    edge_semantics = (
        analyze_edge_state_semantics(
            optimized.blocks, 
            edge_state, 
            final_state, 
        )
    )

    require(
        not edge_semantics.unresolved_writes, 
        (
            "edge-state semantics contains "
            "unresolved writes"
        ), 
    )


    next_state_expr = (
        build_next_state_expr_ir(
            optimized.blocks, 
            edge_semantics, 
            final_state, 
        )
    )

    require(
        (
            next_state_expr.total_writes
            == len(
                edge_semantics.effective_writes
            )
        ), 
        (
            "next-state expression count "
            "does not match effective edge writes"
        ), 
    )


    # ========================================================
    # 6. Infer application-independent hardware primitives
    #
    # q' = q + 1
    #       -> COUNTER_UP
    #
    # q' = q - 1
    #       -> COUNTER_DOWN
    #
    # q' = (q << k) | input
    #       -> SHIFT_REGISTER
    # ========================================================

    hardware_patterns = (
        analyze_hardware_patterns(
            next_state_expr
        )
    )

    require(
        (
            len(hardware_patterns.patterns)
            == next_state_expr.total_writes
        ), 
        (
            "not every next-state write "
            "received a hardware classification"
        ), 
    )


    rtl_ir = build_rtl_ir(
        final_state, 
        hardware_patterns, 
    )

    require(
        (
            rtl_ir.total_edge_writes
            == next_state_expr.total_writes
        ), 
        (
            "RTL IR lost "
            "edge writes"
        ), 
    )


    # ========================================================
    # 7. Structural netlist
    #
    # IMPORTANT:
    #
    # structural_netlist must exist BEFORE semantic SSA is
    # constructed, because SemanticSSABuilder uses the recovered
    # state families as semantic leaf nodes.
    # ========================================================

    structural_netlist = (
        build_structural_netlist(
            rtl_ir, 
            hardware_patterns, 
        )
    )


    invalid_inputs = [
        (
            node.id, 
            source, 
        )

        for node
        in structural_netlist.nodes.values()

        for source
        in node.inputs

        if source
        not in structural_netlist.nodes
    ]


    invalid_writes = [
        write

        for write
        in structural_netlist.writes

        if (
            write.state_node
            not in structural_netlist.nodes

            or

            write.expression_node
            not in structural_netlist.nodes
        )
    ]


    require(
        (
            len(
                structural_netlist.state_nodes
            )
            == len(
                rtl_ir.registers
            )
        ), 
        (
            "structural netlist "
            "lost state nodes"
        ), 
    )

    require(
        (
            len(
                structural_netlist.writes
            )
            == rtl_ir.total_edge_writes
        ), 
        (
            "structural netlist "
            "lost state writes"
        ), 
    )

    require(
        not invalid_inputs, 
        (
            "structural netlist contains "
            "invalid node inputs"
        ), 
    )

    require(
        not invalid_writes, 
        (
            "structural netlist contains "
            "invalid state writes"
        ), 
    )


    # ========================================================
    # 8. Identify real temporal boundaries
    #
    # GPIO_WRITE is an action, not a time boundary.
    #
    # GPIO_READ is currently the relevant external sampling
    # boundary recovered from BIO semantics.
    # ========================================================

    semantic_boundaries = semantic_boundaries_early

    print_semantic_boundaries(
        semantic_boundaries
    )


    semantic_microstates = (
        build_semantic_microstates(
            fsm, 
            semantic_boundaries, 
        )
    )

    print_semantic_microstates(
        semantic_microstates
    )

    require(
        (
            not
            semantic_microstates
            .uncovered_blocks
        ), 
        (
            "semantic microstate partition "
            "does not cover the complete CFG"
        ), 
    )

    require(
        (
            semantic_microstates
            .regions_with_internal_cycles
            == 0
        ), 
        (
            "semantic region contains an "
            "unsampled internal temporal cycle"
        ), 
    )


    # ========================================================
    # 9. Build combinational reachability DAG
    #
    # Basic blocks are no longer interpreted as clocked FSM
    # states. They become combinational reachability nodes inside
    # each semantic sampling region.
    # ========================================================

    semantic_reach = (
        build_semantic_reach(
            fsm, 
            semantic_microstates, 
        )
    )

    print_semantic_reach(
        semantic_reach
    )


    # ========================================================
    # 10. Lower SSA without creating control_x* DFFs
    #
    # PHIs remain predecessor-selected semantic muxes.
    #
    # This is intentionally performed AFTER structural-state
    # discovery, so known persistent states terminate the SSA
    # expression graph as STATE leaves.
    # ========================================================

    semantic_ssa = (
        build_semantic_ssa(
            optimized.blocks, 
            structural_netlist, 
            semantic_reach, 
            fsm, 
            extra_roots = semantic_value_roots(
                next_state_expr
            ), 
        )
    )

    print_semantic_ssa(
        semantic_ssa
    )

    require(
        not semantic_ssa.unresolved, 
        (
            "semantic SSA contains "
            "unresolved values"
        ), 
    )

    require(
        not semantic_ssa.cycles, 
        (
            "semantic SSA contains "
            "unresolved cyclic values"
        ), 
    )


    # ========================================================
    # 11. Recover GPIO output side effects
    #
    # These will be attached to reachability conditions by the
    # new semantic SystemVerilog backend.
    # ========================================================

    gpio_effects = build_gpio_effects(
        optimized.blocks, 
        structural_netlist, 
    )

    require(
        not gpio_effects.unresolved, 
        (
            "GPIO effect lowering contains "
            "unresolved SSA values"
        ), 
    )

    control_predicates = analyze_control_predicates(
        structural_netlist, 
        semantic_ssa, 
    )

    print_control_predicates(
        control_predicates
    )

    hardware_behavior = build_hardware_behavior_ir(
        rtl_ir, 
        hardware_patterns, 
    )

    if args.definition_site_report is not None: 
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        write_definition_site_report(definition_sites, args.definition_site_report)
        print()
        print(f"definition-site diagnostic: {args.definition_site_report}")
        print(f"definition-site diagnostic JSON: {args.definition_site_report}.json")

    if args.behavior_report is not None: 
        write_hardware_behavior_report(
            hardware_behavior, 
            args.behavior_report, 
        )
        print()
        print(f"behavioral hardware IR: {args.behavior_report}")
        print(f"behavioral hardware IR JSON: {args.behavior_report}.json")

    if args.hardware_object_report is not None: 
        hardware_objects = analyze_hardware_objects(
            rtl_ir, 
            next_state_expr, 
            hardware_patterns, 
            semantic_reach, 
        )
        write_hardware_object_report(
            hardware_objects, 
            args.hardware_object_report, 
        )
        print()
        print(f"hardware-object diagnostic: {args.hardware_object_report}")
        print(f"hardware-object diagnostic JSON: {args.hardware_object_report}.json")

    if args.state_report is not None: 
        physical_diagnostic = analyze_physical_state_diagnostic(
            optimized.blocks, 
            logical, 
            final_state, 
            edge_semantics, 
            next_state_expr, 
            hardware_patterns, 
            control_predicates, 
            gpio_effects, 
        )
        write_physical_state_report(
            physical_diagnostic, 
            args.state_report, 
        )
        print()
        print(
            "physical-state diagnostic: "
            f"{args.state_report}"
        )
        print(
            "physical-state diagnostic JSON: "
            f"{args.state_report}.json"
        )

    if args.event_report is not None: 
        # Reuse the same generic physical-state classification even when the
        # user did not request the physical-state text report.  This analysis
        # is read-only and does not affect RTL emission.
        if args.state_report is None: 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, 
                logical, 
                final_state, 
                edge_semantics, 
                next_state_expr, 
                hardware_patterns, 
                control_predicates, 
                gpio_effects, 
            )
        edge_events = analyze_edge_events(
            optimized.blocks, 
            final_state, 
            next_state_expr, 
            semantic_reach, 
            semantic_ssa, 
            physical_diagnostic, 
        )
        write_edge_event_report(edge_events, args.event_report)
        print()
        print(f"edge-event diagnostic: {args.event_report}")
        print(f"edge-event diagnostic JSON: {args.event_report}.json")


    if args.canonical_event_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        canonical_events = analyze_canonical_events(edge_events)
        write_canonical_event_report(canonical_events, args.canonical_event_report)
        print()
        print(f"canonical-event diagnostic: {args.canonical_event_report}")
        print(f"canonical-event diagnostic JSON: {args.canonical_event_report}.json")

    if args.write_guard_report is not None: 
        # Guard attribution depends on the generic event analysis but remains
        # diagnostic-only.  Build prerequisites on demand without changing
        # the selected RTL backend.
        if args.state_report is None and 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, 
                logical, 
                final_state, 
                edge_semantics, 
                next_state_expr, 
                hardware_patterns, 
                control_predicates, 
                gpio_effects, 
            )
        if args.event_report is None and 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, 
                final_state, 
                next_state_expr, 
                semantic_reach, 
                semantic_ssa, 
                physical_diagnostic, 
            )
        write_guards = analyze_write_guards(
            semantic_reach, 
            next_state_expr, 
            semantic_ssa, 
            edge_events, 
        )
        write_write_guard_report(write_guards, args.write_guard_report)
        print()
        print(f"write-guard diagnostic: {args.write_guard_report}")
        print(f"write-guard diagnostic JSON: {args.write_guard_report}.json")

    if args.barrier_retime_report is not None: 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        sample_barriers = analyze_sample_barriers(semantic_reach, hardware_behavior, definition_sites)
        barrier_fusion = analyze_sample_barrier_fusion(semantic_reach, semantic_ssa, next_state_expr, gpio_effects, sample_barriers, optimized.blocks)
        barrier_retime = analyze_barrier_retime(
            semantic_reach, next_state_expr, hardware_behavior, definition_sites, sample_barriers, barrier_fusion, 
            sorted(structural_netlist.state_nodes), 
        )
        write_barrier_retime_report(barrier_retime, args.barrier_retime_report)
        print()
        print(f"barrier-retime diagnostic: {args.barrier_retime_report}")
        print(f"barrier-retime diagnostic JSON: {args.barrier_retime_report}.json")

    if args.sample_barrier_fusion_report is not None: 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        sample_barriers = analyze_sample_barriers(semantic_reach, hardware_behavior, definition_sites)
        barrier_fusion = analyze_sample_barrier_fusion(
            semantic_reach, semantic_ssa, next_state_expr, gpio_effects, sample_barriers, optimized.blocks, 
        )
        write_sample_barrier_fusion_report(barrier_fusion, args.sample_barrier_fusion_report)
        print()
        print(f"sample-barrier fusion diagnostic: {args.sample_barrier_fusion_report}")
        print(f"sample-barrier fusion diagnostic JSON: {args.sample_barrier_fusion_report}.json")

    if args.sample_barrier_report is not None: 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        sample_barriers = analyze_sample_barriers(semantic_reach, hardware_behavior, definition_sites)
        write_sample_barrier_report(sample_barriers, args.sample_barrier_report)
        print()
        print(f"sample-barrier diagnostic: {args.sample_barrier_report}")
        print(f"sample-barrier diagnostic JSON: {args.sample_barrier_report}.json")

    if args.liveout_hoist_report is not None: 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        liveout_hoists = analyze_liveout_hoists(
            semantic_reach, next_state_expr, hardware_behavior, definition_sites, 
            sorted(structural_netlist.state_nodes), 
        )
        write_liveout_hoist_report(liveout_hoists, args.liveout_hoist_report)
        print()
        print(f"liveout-hoist diagnostic: {args.liveout_hoist_report}")
        print(f"liveout-hoist diagnostic JSON: {args.liveout_hoist_report}.json")

    if args.selective_hoist_report is not None: 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        selective_hoists = analyze_selective_hoists(
            semantic_reach, next_state_expr, hardware_behavior, definition_sites, 
            sorted(structural_netlist.state_nodes), 
        )
        write_selective_hoist_report(selective_hoists, args.selective_hoist_report)
        print()
        print(f"selective-hoist diagnostic: {args.selective_hoist_report}")
        print(f"selective-hoist diagnostic JSON: {args.selective_hoist_report}.json")

    if args.definition_guard_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, next_state_expr, 
                hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, definition_sites, hardware_behavior, 
        )
        write_definition_guard_report(definition_guards, args.definition_guard_report)
        print()
        print(f"definition-guard diagnostic: {args.definition_guard_report}")
        print(f"definition-guard diagnostic JSON: {args.definition_guard_report}.json")

    if args.definition_site_equivalence_report is not None: 
        definition_site_ir_check = build_definition_site_ir(state_ssa, final_state)
        defsite_equiv = analyze_definition_site_equivalence(
            semantic_reach, next_state_expr, definition_site_ir_check, 
            sorted(structural_netlist.state_nodes), 
        )
        write_definition_site_equivalence_report(
            defsite_equiv, args.definition_site_equivalence_report, 
        )
        require(defsite_equiv.equivalent, "definition-site backend symbolic equivalence failed")
        print()
        print(f"definition-site equivalence: {args.definition_site_equivalence_report}")
        print(f"definition-site equivalence JSON: {args.definition_site_equivalence_report}.json")

    if args.definition_schedule_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_schedule = analyze_definition_schedules(
            semantic_reach, edge_events, canonical_events, definition_sites, hardware_behavior, 
        )
        write_definition_schedule_report(definition_schedule, args.definition_schedule_report)
        print()
        print(f"definition-schedule diagnostic: {args.definition_schedule_report}")
        print(f"definition-schedule diagnostic JSON: {args.definition_schedule_report}.json")

    if args.dedicated_hardware_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        if 'write_guards' not in locals(): 
            write_guards = analyze_write_guards(
                semantic_reach, next_state_expr, semantic_ssa, edge_events, 
            )
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        dedicated_hw = build_dedicated_hardware_ir(
            hardware_behavior, definition_sites, canonical_events, write_guards, 
        )
        write_dedicated_hardware_report(dedicated_hw, args.dedicated_hardware_report)
        print()
        print(f"dedicated-hardware IR: {args.dedicated_hardware_report}")
        print(f"dedicated-hardware IR JSON: {args.dedicated_hardware_report}.json")

    if args.event_transition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(
            canonical_events, definition_guards, 
        )
        write_event_transition_report(
            event_transition_ir, args.event_transition_report, 
        )
        print()
        print(f"event-transition IR: {args.event_transition_report}")
        print(f"event-transition IR JSON: {args.event_transition_report}.json")

    if args.event_domain_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(
            canonical_events, definition_guards, 
        )
        event_domains = analyze_event_domains(event_transition_ir, hardware_behavior)
        write_event_domain_report(event_domains, args.event_domain_report)
        print()
        print(f"event-domain diagnostic: {args.event_domain_report}")
        print(f"event-domain diagnostic JSON: {args.event_domain_report}.json")

    if args.event_effect_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        event_effects = analyze_event_gpio_effects(
            semantic_reach, semantic_ssa, edge_events, canonical_events, gpio_effects, 
        )
        write_event_effect_report(event_effects, args.event_effect_report)
        print()
        print(f"event-effect diagnostic: {args.event_effect_report}")
        print(f"event-effect diagnostic JSON: {args.event_effect_report}.json")

    if args.event_phase_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(
            canonical_events, definition_guards, 
        )
        event_domains = analyze_event_domains(event_transition_ir, hardware_behavior)
        event_effects = analyze_event_gpio_effects(
            semantic_reach, semantic_ssa, edge_events, canonical_events, gpio_effects, 
        )
        event_phase = analyze_event_phase_composition(
            event_transition_ir, event_domains, event_effects, 
        )
        write_event_phase_composition_report(event_phase, args.event_phase_report)
        print()
        print(f"event-phase diagnostic: {args.event_phase_report}")
        print(f"event-phase diagnostic JSON: {args.event_phase_report}.json")

    if args.event_phase_proof_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(
            canonical_events, definition_guards, 
        )
        event_domains = analyze_event_domains(event_transition_ir, hardware_behavior)
        event_effects = analyze_event_gpio_effects(
            semantic_reach, semantic_ssa, edge_events, canonical_events, gpio_effects, 
        )
        event_phase = analyze_event_phase_composition(
            event_transition_ir, event_domains, event_effects, 
        )
        event_phase_proof = analyze_event_phase_proofs(
            event_transition_ir, event_phase, event_domains.multi_event_bits, 
        )
        write_event_phase_proof_report(
            event_phase_proof, args.event_phase_proof_report, 
        )
        print()
        print(f"event-phase proof: {args.event_phase_proof_report}")
        print(f"event-phase proof JSON: {args.event_phase_proof_report}.json")

    if args.event_macro_transition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        # Control projection is derived structurally: small multi-event states
        # after proven m44/m48 phase composition are the control-automaton target.
        event_domains = None
        definition_sites = analyze_definition_sites(
            state_ssa, final_state, hardware_behavior, 
        )
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(
            canonical_events, definition_guards, 
        )
        event_domains = analyze_event_domains(event_transition_ir, hardware_behavior)
        control_regs = [
            row.register for row in event_domains.registers
            if row.domain_kind == "MULTI_EVENT" and row.width <= 2
        ]
        event_macro = analyze_event_macro_transitions(
            semantic_reach, next_state_expr, structural_netlist, canonical_events, 
            control_registers = control_regs, 
        )
        write_event_macro_transition_report(
            event_macro, args.event_macro_transition_report, 
        )
        print()
        print(f"event macro-transition diagnostic: {args.event_macro_transition_report}")
        print(f"event macro-transition diagnostic JSON: {args.event_macro_transition_report}.json")

    if args.boundary_range_report is not None: 
        boundary_ranges = analyze_boundary_ranges(
            semantic_reach, next_state_expr, structural_netlist, 
        )
        write_boundary_range_report(boundary_ranges, args.boundary_range_report)
        print()
        print(f"boundary-range diagnostic: {args.boundary_range_report}")
        print(f"boundary-range diagnostic JSON: {args.boundary_range_report}.json")

    if args.control_automaton_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_guards = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, 
            definition_sites, hardware_behavior, 
        )
        event_transition_ir = build_event_transition_ir(canonical_events, definition_guards)
        event_domains = analyze_event_domains(event_transition_ir, hardware_behavior)
        control_regs = [
            row.register for row in event_domains.registers
            if row.domain_kind == "MULTI_EVENT" and row.width <= 2
        ]
        control_auto = analyze_control_automaton(
            semantic_reach, next_state_expr, structural_netlist, canonical_events, control_regs, 
        )
        write_control_automaton_report(control_auto, args.control_automaton_report)
        print()
        print(f"control-automaton diagnostic: {args.control_automaton_report}")
        print(f"control-automaton diagnostic JSON: {args.control_automaton_report}.json")

    if args.event_macro_full_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        event_macro_full = analyze_full_event_macro_transitions(
            semantic_reach, next_state_expr, structural_netlist, semantic_ssa, 
            gpio_effects, canonical_events, 
        )
        write_full_event_macro_report(event_macro_full, args.event_macro_full_report)
        print()
        print(f"full event macro-transition diagnostic: {args.event_macro_full_report}")
        print(f"full event macro-transition diagnostic JSON: {args.event_macro_full_report}.json")



    if args.hardware_event_transition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        if 'hardware_temporal_regions' not in locals(): 
            hardware_temporal_regions = build_hardware_temporal_regions(
                semantic_microstates, canonical_events, 
            )
        hardware_event_transitions = analyze_hardware_event_transitions(
            semantic_reach, next_state_expr, structural_netlist, 
            canonical_events, hardware_temporal_regions, 
        )
        write_hardware_event_transition_report(
            hardware_event_transitions, args.hardware_event_transition_report, 
        )
        print()
        print(f"hardware-event-transition diagnostic: {args.hardware_event_transition_report}")
        print(f"hardware-event-transition diagnostic JSON: {args.hardware_event_transition_report}.json")

    if args.hardware_temporal_region_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        hardware_temporal_regions = build_hardware_temporal_regions(
            semantic_microstates, canonical_events, 
        )
        write_hardware_temporal_region_report(
            hardware_temporal_regions, args.hardware_temporal_region_report, 
        )
        print()
        print(f"hardware-temporal-region diagnostic: {args.hardware_temporal_region_report}")
        print(f"hardware-temporal-region diagnostic JSON: {args.hardware_temporal_region_report}.json")

    if args.event_liveness_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        definition_site_ir = build_definition_site_ir(state_ssa, final_state)
        event_liveness = analyze_event_boundary_liveness(
            semantic_reach, canonical_events, definition_site_ir, semantic_ssa, 
            gpio_effects, structural_netlist, 
        )
        write_event_liveness_report(event_liveness, args.event_liveness_report)
        print()
        print(f"event-boundary liveness: {args.event_liveness_report}")
        print(f"event-boundary liveness JSON: {args.event_liveness_report}.json")

    if args.control_phase_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'write_guards' not in locals(): 
            write_guards = analyze_write_guards(
                semantic_reach, next_state_expr, semantic_ssa, edge_events, 
            )
        control_phases = analyze_control_phases(
            physical_diagnostic, next_state_expr, write_guards, 
        )
        write_control_phase_report(control_phases, args.control_phase_report)
        print()
        print(f"control-phase diagnostic: {args.control_phase_report}")
        print(f"control-phase diagnostic JSON: {args.control_phase_report}.json")

    if args.canonical_phase_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, 
                next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(
                optimized.blocks, final_state, next_state_expr, 
                semantic_reach, semantic_ssa, physical_diagnostic, 
            )
        if 'write_guards' not in locals(): 
            write_guards = analyze_write_guards(
                semantic_reach, next_state_expr, semantic_ssa, edge_events, 
            )
        if 'control_phases' not in locals(): 
            control_phases = analyze_control_phases(
                physical_diagnostic, next_state_expr, write_guards, 
            )
        canonical_phases = analyze_canonical_phase_predicates(
            control_phases, write_guards, 
        )
        write_canonical_phase_report(canonical_phases, args.canonical_phase_report)
        print()
        print(f"canonical-phase diagnostic: {args.canonical_phase_report}")
        print(f"canonical-phase diagnostic JSON: {args.canonical_phase_report}.json")

    if args.event_vector_transition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        hardware_temporal_evt = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        evt = analyze_event_vector_transitions(semantic_reach, next_state_expr, structural_netlist, canonical_events, hardware_temporal_evt)
        write_event_vector_transition_report(evt, args.event_vector_transition_report)
        print(f"event-vector-transition diagnostic: {args.event_vector_transition_report}")

    if args.event_vector_predicate_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        if 'definition_sites' not in locals(): 
            definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_guards_evp = analyze_definition_guards(semantic_reach, semantic_ssa, edge_events, canonical_events, definition_sites, hardware_behavior)
        evp = analyze_event_vector_predicates(semantic_reach, canonical_events, semantic_ssa, definition_guards_evp)
        write_event_vector_predicate_report(evp, args.event_vector_predicate_report)
        print(f"event-vector-predicate diagnostic: {args.event_vector_predicate_report}")

    if args.event_vector_decision_dag_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        hardware_temporal_dag = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        definition_site_ir_dag = build_definition_site_ir(state_ssa, final_state)
        transition_semantic_ssa_dag = build_transition_semantic_ssa(
            state_ssa.blocks, structural_netlist, semantic_reach, fsm, 
        )
        evd = analyze_event_vector_decision_dag(semantic_reach, next_state_expr, structural_netlist, canonical_events, hardware_temporal_dag, transition_semantic_ssa_dag, definition_site_ir_dag)
        write_event_vector_decision_dag_report(evd, args.event_vector_decision_dag_report)
        print(f"event-vector-decision-dag diagnostic: {args.event_vector_decision_dag_report}")

    if args.epsilon_transition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_eps = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_eps = analyze_polling_phase_events(semantic_reach, transition_ssa_eps, next_state_expr)
        eps = analyze_epsilon_transitions(semantic_reach, next_state_expr, structural_netlist, transition_ssa_eps, canonical_events, polling_eps)
        write_epsilon_transition_report(eps, args.epsilon_transition_report)
        print(f"epsilon-transition diagnostic: {args.epsilon_transition_report}")

    if args.wait_poll_elimination_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_wait = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_wait = analyze_polling_phase_events(semantic_reach, transition_ssa_wait, next_state_expr)
        wpe = analyze_wait_poll_elimination(semantic_reach, next_state_expr, structural_netlist, transition_ssa_wait, gpio_effects, canonical_events, polling_wait)
        write_wait_poll_elimination_report(wpe, args.wait_poll_elimination_report)
        print(f"wait-poll-elimination diagnostic: {args.wait_poll_elimination_report}")

    if args.hardware_event_partition_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_partition = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_partition = analyze_polling_phase_events(semantic_reach, transition_ssa_partition, next_state_expr)
        hep = analyze_hardware_event_partition(semantic_reach, transition_ssa_partition, canonical_events, polling_partition)
        write_hardware_event_partition_report(hep, args.hardware_event_partition_report)
        print(f"hardware-event-partition diagnostic: {args.hardware_event_partition_report}")

    if args.polling_phase_event_report is not None: 
        transition_ssa_phase = build_transition_semantic_ssa(
            state_ssa.blocks, structural_netlist, semantic_reach, fsm, 
        )
        ppe = analyze_polling_phase_events(semantic_reach, transition_ssa_phase, next_state_expr)
        write_polling_phase_event_report(ppe, args.polling_phase_event_report)
        print(f"polling-phase-event diagnostic: {args.polling_phase_event_report}")

    if args.event_vector_sequence_dag_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        temporal_seq = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        def_ir_seq = build_definition_site_ir(state_ssa, final_state)
        transition_ssa_seq = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        evs = analyze_event_vector_sequence_dag(semantic_reach, next_state_expr, structural_netlist, canonical_events, temporal_seq, transition_ssa_seq, def_ir_seq)
        write_event_vector_sequence_dag_report(evs, args.event_vector_sequence_dag_report)
        print(f"event-vector-sequence-dag diagnostic: {args.event_vector_sequence_dag_report}")



    if args.dedicated_transition_core_ir is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_dtci = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_dtci = analyze_polling_phase_events(semantic_reach, transition_ssa_dtci, next_state_expr)
        temporal_dtci = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        def_ir_dtci = build_definition_site_ir(state_ssa, final_state)
        dtci = build_dedicated_transition_core_ir(
            semantic_reach, next_state_expr, structural_netlist, canonical_events, polling_dtci, 
            temporal_dtci, transition_ssa_dtci, def_ir_dtci, 
        )
        write_dedicated_transition_core_ir(dtci, args.dedicated_transition_core_ir)
        print(f"dedicated-transition-core IR: {args.dedicated_transition_core_ir}")

    if args.feasible_transition_core_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_ftc = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_ftc = analyze_polling_phase_events(semantic_reach, transition_ssa_ftc, next_state_expr)
        temporal_ftc = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        def_ir_ftc = build_definition_site_ir(state_ssa, final_state)
        ftc = analyze_feasible_transition_core(
            semantic_reach, next_state_expr, structural_netlist, canonical_events, polling_ftc, 
            temporal_ftc, transition_ssa_ftc, def_ir_ftc, 
        )
        write_feasible_transition_core_report(ftc, args.feasible_transition_core_report)
        print(f"feasible-transition-core diagnostic: {args.feasible_transition_core_report}")

    if args.feasible_symbolic_executor_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects)
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        transition_ssa_fse = build_transition_semantic_ssa(state_ssa.blocks, structural_netlist, semantic_reach, fsm)
        polling_fse = analyze_polling_phase_events(semantic_reach, transition_ssa_fse, next_state_expr)
        temporal_fse = build_hardware_temporal_regions(semantic_microstates, canonical_events)
        def_ir_fse = build_definition_site_ir(state_ssa, final_state)
        fse = analyze_feasible_symbolic_executor(
            semantic_reach, structural_netlist, canonical_events, polling_fse, 
            temporal_fse, transition_ssa_fse, def_ir_fse, gpio_effects, 
        )
        write_feasible_symbolic_executor_report(fse, args.feasible_symbolic_executor_report)
        print(f"feasible-symbolic-executor diagnostic: {args.feasible_symbolic_executor_report}")

    if args.semantic_state_role_report is not None: 
        transition_semantic_ssa_roles = build_transition_semantic_ssa(
            state_ssa.blocks, structural_netlist, semantic_reach, fsm, 
        )
        ssr = analyze_semantic_state_roles(transition_semantic_ssa_roles)
        write_semantic_state_role_report(ssr, args.semantic_state_role_report)
        print(f"semantic-state-role diagnostic: {args.semantic_state_role_report}")

    if args.event_vector_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        event_vectors = analyze_event_vectors(semantic_reach, canonical_events, semantic_ssa)
        write_event_vector_report(event_vectors, args.event_vector_report)
        print(f"event-vector diagnostic: {args.event_vector_report}")

    if args.hardware_predicate_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        if 'definition_sites' not in locals(): 
            definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_guards_hp = analyze_definition_guards(semantic_reach, semantic_ssa, edge_events, canonical_events, definition_sites, hardware_behavior)
        hp = analyze_hardware_predicates(semantic_ssa, definition_guards_hp)
        write_hardware_predicate_report(hp, args.hardware_predicate_report)
        print(f"hardware-predicate diagnostic: {args.hardware_predicate_report}")

    if args.event_hardware_predicate_report is not None: 
        if 'physical_diagnostic' not in locals(): 
            physical_diagnostic = analyze_physical_state_diagnostic(
                optimized.blocks, logical, final_state, edge_semantics, next_state_expr, hardware_patterns, control_predicates, gpio_effects, 
            )
        if 'edge_events' not in locals(): 
            edge_events = analyze_edge_events(optimized.blocks, final_state, next_state_expr, semantic_reach, semantic_ssa, physical_diagnostic)
        if 'canonical_events' not in locals(): 
            canonical_events = analyze_canonical_events(edge_events)
        if 'definition_sites' not in locals(): 
            definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        definition_guards_ehp = analyze_definition_guards(
            semantic_reach, semantic_ssa, edge_events, canonical_events, definition_sites, hardware_behavior
        )
        event_hp = analyze_event_hardware_predicates(
            semantic_reach, semantic_ssa, canonical_events, definition_guards_ehp
        )
        write_event_hardware_predicate_report(event_hp, args.event_hardware_predicate_report)
        print(f"event-hardware-predicate diagnostic: {args.event_hardware_predicate_report}")


    # ========================================================
    # 11b. Experimental event-local state demotion
    #
    # A state proven reset-zero and HOLD/CONST(0) at every semantic
    # boundary is not persistent hardware state.  Unlike the old
    # boundary-pruned post-SV rewrite, remove it *before* rebuilding
    # semantic SSA so its intra-event uses are re-expanded as ordinary
    # combinational SSA expressions rather than hardware FF leaves.
    # ========================================================
    if args.backend == "event-local": 
        first_boundary_ranges = analyze_boundary_ranges(semantic_reach, next_state_expr, structural_netlist)
        demoted = sorted(r.register for r in first_boundary_ranges.rows if r.constant_zero_candidate)
        require(bool(demoted), "event-local found no boundary-constant persistent states")
        kept_states = {k: v for k, v in final_state.states.items() if k not in set(demoted)}
        final_state = type(final_state)(states = kept_states, total_bits = sum(x.width for x in kept_states.values()), unproven = [])

        edge_state = build_edge_state_ir(optimized.blocks, final_state)
        edge_semantics = analyze_edge_state_semantics(optimized.blocks, edge_state, final_state)
        require(not edge_semantics.unresolved_writes, "event-local edge semantics contains unresolved writes")
        next_state_expr = build_next_state_expr_ir(optimized.blocks, edge_semantics, final_state)
        hardware_patterns = analyze_hardware_patterns(next_state_expr)
        rtl_ir = build_rtl_ir(final_state, hardware_patterns)
        structural_netlist = build_structural_netlist(rtl_ir, hardware_patterns)
        semantic_ssa = build_semantic_ssa(optimized.blocks, structural_netlist, semantic_reach, fsm, extra_roots = semantic_value_roots(next_state_expr))
        require(not semantic_ssa.unresolved, "event-local semantic SSA contains unresolved values")
        require(not semantic_ssa.cycles, "event-local semantic SSA contains unresolved cycles")
        gpio_effects = build_gpio_effects(optimized.blocks, structural_netlist)
        hardware_behavior = build_hardware_behavior_ir(rtl_ir, hardware_patterns)
        print()
        print("event-local demoted states : " + ", ".join(demoted))
        print(f"event-local state bits      : {final_state.total_bits}")

    # ========================================================
    # 12. Fold physical next-state guards (experimental backend)
    # ========================================================

    semantic_next_state = None

    if args.backend == "folded": 
        semantic_next_state = build_semantic_next_state(
            semantic_reach, 
            next_state_expr, 
        )
        print_semantic_next_state(semantic_next_state)

    if args.hardware_enable_report is not None: 
        # hardware-enable analysis needs symbolic semantic next-state data,
        # but ordinary edge/proven-hw generation does not.  Build it lazily
        # for this diagnostic so the optional value is narrowed locally and
        # does not become a runtime requirement of unrelated backends.
        report_semantic_next_state = semantic_next_state
        if report_semantic_next_state is None: 
            report_semantic_next_state = build_semantic_next_state(
                semantic_reach, 
                next_state_expr, 
            )

        hardware_enables = analyze_hardware_enables(
            hardware_behavior, 
            report_semantic_next_state, 
        )
        write_hardware_enable_report(
            hardware_enables, 
            args.hardware_enable_report, 
        )
        print()
        print(f"hardware-enable diagnostic: {args.hardware_enable_report}")
        print(f"hardware-enable diagnostic JSON: {args.hardware_enable_report}.json")

    definition_site_ir = None
    proven_hoist_plan = None
    if args.backend == "defsite": 
        definition_site_ir = build_definition_site_ir(state_ssa, final_state)
    elif args.backend == "proven-hw": 
        definition_sites = analyze_definition_sites(state_ssa, final_state, hardware_behavior)
        sample_barriers = analyze_sample_barriers(semantic_reach, hardware_behavior, definition_sites)
        barrier_fusion = analyze_sample_barrier_fusion(semantic_reach, semantic_ssa, next_state_expr, gpio_effects, sample_barriers, optimized.blocks)
        barrier_retime = analyze_barrier_retime(
            semantic_reach, next_state_expr, hardware_behavior, definition_sites, sample_barriers, barrier_fusion, 
            sorted(structural_netlist.state_nodes), 
        )
        require(barrier_retime.post_retime_hoist.jointly_equivalent, "proven-hw hoist proof failed")
        proven_hoist_plan = build_proven_hoist_plan(
            semantic_reach, next_state_expr, hardware_behavior, definition_sites, sample_barriers, barrier_fusion, barrier_retime, 
        )

    # ========================================================
    # 13. Emit semantic SystemVerilog
    # ========================================================

    emit_semantic_systemverilog(
        structural_netlist, 
        next_state_expr, 
        semantic_reach, 
        semantic_ssa, 
        gpio_effects, 
        args.output, 
        semantic_next_state, 
        definition_site_ir, 
        proven_hoist_plan, 
    )

    if args.backend == "boundary-pruned": 
        boundary_ranges_for_backend = analyze_boundary_ranges(
            semantic_reach, next_state_expr, structural_netlist, 
        )
        pruned_boundary_states = apply_constant_boundary_state_pruning(
            args.output, boundary_ranges_for_backend, 
        )
        print()
        print("boundary-pruned persistent states: " + (", ".join(pruned_boundary_states) or "-"))

    require(
        args.output.is_file(), 
        "semantic SystemVerilog backend did not create output file", 
    )

    # ========================================================
    # Final current-stage result
    # ========================================================

    print_header(
        "BIO2RTL SEMANTIC RESULT"
    )

    print(
        f"file                  : "
        f"{args.disassembly}"
    )

    print(
        f"SystemVerilog         : "
        f"{args.output}"
    )

    print(
        f"instructions          : "
        f"{analysis.instruction_count}"
    )

    print(
        f"CFG blocks            : "
        f"{len(cfg.blocks)}"
    )

    print(
        f"optimized blocks      : "
        f"{len(optimized.blocks)}"
    )

    print(
        f"persistent states     : "
        f"{len(final_state.states)}"
    )

    print(
        f"persistent state bits : "
        f"{final_state.total_bits}"
    )

    print(
        f"structural nodes      : "
        f"{len(structural_netlist.nodes)}"
    )

    print(
        f"GPIO sample states    : "
        f"{len(semantic_microstates.sample_blocks)}"
    )

    print(
        f"semantic regions      : "
        f"{len(semantic_microstates.regions)}"
    )

    print(
        f"GPIO effects          : "
        f"{len(gpio_effects.effects)}"
    )

    print(
        f"semantic PHI values   : "
        f"{len(semantic_ssa.phi_values)}"
    )

    print(
        f"semantic live-ins     : "
        f"{len(semantic_ssa.liveins)}"
    )


    print_header(
        "INFERRED HARDWARE PRIMITIVES"
    )

    for kind, count in sorted(
        structural_netlist
        .primitive_counts
        .items()
    ): 

        print(
            f"{kind:20s}: "
            f"{count}"
        )


    print_header(
        "INFERRED STATE STRUCTURE"
    )

    for (
        family, 
        node_id, 
    ) in sorted(
        structural_netlist
        .state_nodes
        .items()
    ): 

        node = (
            structural_netlist
            .nodes[node_id]
        )

        width_text = (
            "?"
            if node.width is None
            else str(node.width)
        )

        print(
            f"{family:32s} "
            f"{width_text:>2s} bit "
            f"{node.kind}"
        )


    print_header(
        "GPIO EFFECT RESULT"
    )

    for kind, count in sorted(
        gpio_effects.counts.items()
    ): 

        print(
            f"{kind:18s}: "
            f"{count}"
        )

    print(
        f"blocks affected   : "
        f"{len(gpio_effects.by_block)}"
    )


    print_header(
        "CURRENT PIPELINE CHECK"
    )

    print(
        "STRUCTURAL STATE    : PASS"
    )

    print(
        "SEMANTIC PARTITION  : PASS"
    )

    print(
        "REACHABILITY DAG    : PASS"
    )

    print(
        "SEMANTIC SSA        : PASS"
    )

    print(
        "GPIO EFFECT         : PASS"
    )

    print(
        "SYSTEMVERILOG       : PASS"
    )

    print()
    print(
        "OLD 93-STATE RTL BACKEND: DISABLED"
    )

    print(
        "SEMANTIC RTL BACKEND: ENABLED"
    )


if __name__ == "__main__": 

    main()