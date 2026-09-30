from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)

from .register_ssa import (
    find_reachable, 
    compute_dominators, 
)

from .ssa_opt import (
    parse_phi_arg, 
)


# ============================================================
# Result structures
# ============================================================

@dataclass
class BackEdge: 
    source: int
    target: int


@dataclass
class PhiClassification: 
    block_id: int
    block_start: int

    destination: str

    incoming: dict[int, str]

    backedge_predecessors: list[int]

    kind: str
    # "STATE" or "MUX"


@dataclass
class StateExtractionResult: 
    blocks: list[IRBlock]

    back_edges: list[BackEdge]

    phi_nodes: list[PhiClassification]

    state_phis: list[PhiClassification]

    mux_phis: list[PhiClassification]

    malformed_phis: list[
        tuple[int, str]
    ] = field(
        default_factory = list
    )

    missing_phi_predecessors: list[
        tuple[int, str, list[int]]
    ] = field(
        default_factory = list
    )

    extra_phi_predecessors: list[
        tuple[int, str, list[int]]
    ] = field(
        default_factory = list
    )


# ============================================================
# Back-edge detection
# ============================================================

def detect_back_edges(
    blocks: list[IRBlock], 
) -> list[BackEdge]: 
    """
    CFG edge:

        source -> target

    is a back edge when target dominates source.

    This is the standard natural-loop criterion.
    """

    if not blocks: 
        return []


    blocks_by_id = {
        block.id: block
        for block in blocks
    }


    entry = min(
        blocks_by_id
    )


    reachable = find_reachable(
        blocks_by_id, 
        entry, 
    )


    if len(reachable) != len(blocks): 

        unreachable = sorted(
            set(blocks_by_id)
            - reachable
        )

        raise RuntimeError(
            "State extraction found "
            "unreachable blocks: "
            + ", ".join(
                f"BB{x}"
                for x in unreachable
            )
        )


    dominators = compute_dominators(
        blocks_by_id, 
        entry, 
        reachable, 
    )


    result = []


    for source in sorted(
        reachable
    ): 

        block = blocks_by_id[
            source
        ]


        for target in (
            block.successors
        ): 

            # target dominates source
            #
            # => source -> target is a back edge.

            if (
                target
                in dominators[
                    source
                ]
            ): 

                result.append(
                    BackEdge(
                        source = source, 
                        target = target, 
                    )
                )


    return result


# ============================================================
# PHI parsing
# ============================================================

def parse_phi_inputs(
    op: IROp, 
) -> dict[int, str] | None: 

    incoming = {}


    for arg in op.args: 

        parsed = parse_phi_arg(
            arg
        )

        if parsed is None: 
            return None


        pred_text, value = parsed


        if not pred_text.startswith(
            "BB"
        ): 
            return None


        try: 

            predecessor = int(
                pred_text[2:]
            )

        except ValueError: 

            return None


        incoming[
            predecessor
        ] = value


    return incoming


# ============================================================
# PHI classification
# ============================================================

def classify_phis(
    blocks: list[IRBlock], 
) -> StateExtractionResult: 

    blocks_by_id = {
        block.id: block
        for block in blocks
    }


    back_edges = detect_back_edges(
        blocks
    )


    back_edge_set = {
        (
            edge.source, 
            edge.target, 
        )

        for edge in back_edges
    }


    phi_nodes = []

    malformed_phis = []

    missing_phi_predecessors = []

    extra_phi_predecessors = []


    for block in blocks: 

        cfg_predecessors = set(
            block.predecessors
        )


        for op in block.ops: 

            if op.kind != "PHI": 
                continue


            if op.dst is None: 

                malformed_phis.append(
                    (
                        block.id, 
                        "<no destination>", 
                    )
                )

                continue


            incoming = parse_phi_inputs(
                op
            )


            if incoming is None: 

                malformed_phis.append(
                    (
                        block.id, 
                        op.dst, 
                    )
                )

                continue


            phi_predecessors = set(
                incoming
            )


            missing = sorted(
                cfg_predecessors
                - phi_predecessors
            )


            extra = sorted(
                phi_predecessors
                - cfg_predecessors
            )


            if missing: 

                missing_phi_predecessors.append(
                    (
                        block.id, 
                        op.dst, 
                        missing, 
                    )
                )


            if extra: 

                extra_phi_predecessors.append(
                    (
                        block.id, 
                        op.dst, 
                        extra, 
                    )
                )


            backedge_predecessors = sorted(
                predecessor

                for predecessor
                in incoming

                if (
                    predecessor, 
                    block.id, 
                )
                in back_edge_set
            )


            kind = (
                "STATE"
                if backedge_predecessors
                else "MUX"
            )


            phi_nodes.append(
                PhiClassification(
                    block_id = 
                        block.id, 

                    block_start = 
                        block.start, 

                    destination = 
                        op.dst, 

                    incoming = 
                        incoming, 

                    backedge_predecessors = 
                        backedge_predecessors, 

                    kind = 
                        kind, 
                )
            )


    state_phis = [
        phi

        for phi in phi_nodes

        if phi.kind == "STATE"
    ]


    mux_phis = [
        phi

        for phi in phi_nodes

        if phi.kind == "MUX"
    ]


    return StateExtractionResult(
        blocks = blocks, 

        back_edges = back_edges, 

        phi_nodes = phi_nodes, 

        state_phis = state_phis, 

        mux_phis = mux_phis, 

        malformed_phis = 
            malformed_phis, 

        missing_phi_predecessors = 
            missing_phi_predecessors, 

        extra_phi_predecessors = 
            extra_phi_predecessors, 
    )


# ============================================================
# State summary helpers
# ============================================================

def state_input_values(
    state_phi: PhiClassification, 
) -> tuple[
    list[str], 
    list[str], 
]: 
    """
    Return:

        initial/forward-edge values
        back-edge values
    """

    forward = []
    feedback = []


    backedge_set = set(
        state_phi.backedge_predecessors
    )


    for predecessor, value in sorted(
        state_phi.incoming.items()
    ): 

        if predecessor in backedge_set: 

            feedback.append(
                value
            )

        else: 

            forward.append(
                value
            )


    return (
        forward, 
        feedback, 
    )