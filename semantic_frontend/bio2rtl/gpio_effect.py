from __future__ import annotations

from dataclasses import dataclass

from .ir import (
    IROp, 
    IRBlock, 
)

from .control_expr import (
    ControlExpr, 
    ControlExprBuilder, 
)

from .structural_netlist import (
    StructuralNetlist, 
)


GPIO_EFFECT_KINDS = {
    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 
}


@dataclass(frozen = True)
class GPIOEffect: 
    block_id: int

    kind: str

    source_value: str

    expression: ControlExpr

    address: int | None


@dataclass
class GPIOEffectResult: 
    effects: list[GPIOEffect]

    by_block: dict[
        int, 
        list[GPIOEffect], 
    ]

    counts: dict[str, int]

    unresolved: list[str]


def build_gpio_effects(
    blocks: list[IRBlock], 
    structural: StructuralNetlist, 
) -> GPIOEffectResult: 
    """
    Recover all externally visible BIO GPIO writes.

    Important:
    This pass preserves the BIO special-register operation
    exactly. It does NOT guess pin-level semantics for
    GPIO_SET / GPIO_CLEAR_N / DIR_SET / DIR_CLEAR / MASK.
    """

    builder = ControlExprBuilder(
        blocks, 
        structural, 
    )

    effects: list[GPIOEffect] = []

    by_block: dict[
        int, 
        list[GPIOEffect], 
    ] = {}

    counts = {
        kind: 0
        for kind in sorted(
            GPIO_EFFECT_KINDS
        )
    }


    for block in blocks: 

        for op in block.ops: 

            if op.kind not in GPIO_EFFECT_KINDS: 
                continue


            if len(op.args) != 1: 

                raise RuntimeError(
                    f"BB{block.id} "
                    f"{op.kind}: expected exactly "
                    f"1 operand, got {len(op.args)}"
                )


            source = op.args[0]


            expression = builder.build(
                source
            )


            effect = GPIOEffect(
                block_id = 
                    block.id, 

                kind = 
                    op.kind, 

                source_value = 
                    source, 

                expression = 
                    expression, 

                address = 
                    op.address, 
            )


            effects.append(
                effect
            )


            by_block.setdefault(
                block.id, 
                [], 
            ).append(
                effect
            )


            counts[
                op.kind
            ] += 1


    return GPIOEffectResult(
        effects = effects, 

        by_block = by_block, 

        counts = counts, 

        unresolved = 
            list(
                builder.unresolved
            ), 
    )