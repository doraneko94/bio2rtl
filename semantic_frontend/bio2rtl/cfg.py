from dataclasses import dataclass, field

from .parser import Disassembly
from .riscv import (
    Instruction, 
    branch_target, 
    BRANCH_OPS, 
)


@dataclass
class BasicBlock: 
    id: int
    start: int
    instructions: list[Instruction]

    successors: list[int] = field(
        default_factory = list
    )

    predecessors: list[int] = field(
        default_factory = list
    )

    @property
    def end(self) -> int: 
        return self.instructions[-1].address

    @property
    def last(self) -> Instruction: 
        return self.instructions[-1]


@dataclass
class CFG: 
    blocks: list[BasicBlock]
    address_to_block: dict[int, int]


def find_leaders(
    dis: Disassembly, 
) -> set[int]: 

    instructions = dis.instructions

    leaders = {
        instructions[0].address
    }

    known_addresses = {
        insn.address
        for insn in instructions
    }


    for insn in instructions: 

        if not insn.is_control_flow: 
            continue

        target = branch_target(insn)

        if (
            target is not None
            and target in known_addresses
        ): 
            leaders.add(target)


        # Conditional branches have a fall-through path.
        # jal is treated conservatively as having a
        # continuation after returning.

        if (
            insn.opcode in BRANCH_OPS
            or insn.opcode == "jal"
        ): 
            if (
                insn.next_address
                in known_addresses
            ): 
                leaders.add(
                    insn.next_address
                )


    return leaders


def build_cfg(
    dis: Disassembly, 
) -> CFG: 

    leaders = find_leaders(dis)

    blocks = []

    current = []


    for insn in dis.instructions: 

        if (
            current
            and insn.address in leaders
        ): 
            blocks.append(
                BasicBlock(
                    id = len(blocks), 
                    start = current[0].address, 
                    instructions = current, 
                )
            )

            current = []

        current.append(insn)


    if current: 
        blocks.append(
            BasicBlock(
                id = len(blocks), 
                start = current[0].address, 
                instructions = current, 
            )
        )


    address_to_block = {
        block.start: block.id
        for block in blocks
    }


    # Build an instruction-address -> block map too,
    # because targets may be useful even after later passes.

    containing_block = {}

    for block in blocks: 
        for insn in block.instructions: 
            containing_block[
                insn.address
            ] = block.id


    for index, block in enumerate(blocks): 

        last = block.last

        successors = []


        # Conditional branch:
        #
        # target + fall-through

        if last.opcode in BRANCH_OPS: 

            target = branch_target(last)

            if (
                target is not None
                and target in containing_block
            ): 
                successors.append(
                    containing_block[target]
                )

            if (
                last.next_address
                in containing_block
            ): 
                successors.append(
                    containing_block[
                        last.next_address
                    ]
                )


        # Unconditional direct jump

        elif last.opcode == "j": 

            target = branch_target(last)

            if (
                target is not None
                and target in containing_block
            ): 
                successors.append(
                    containing_block[target]
                )


        # Function call.
        #
        # For the control-flow graph of the caller,
        # retain the continuation as an edge.
        # The call target is also retained.

        elif last.opcode == "jal": 

            target = branch_target(last)

            if (
                target is not None
                and target in containing_block
            ): 
                successors.append(
                    containing_block[target]
                )

            if (
                last.next_address
                in containing_block
            ): 
                successors.append(
                    containing_block[
                        last.next_address
                    ]
                )


        # ret/jr/jalr terminate this local direct CFG.
        elif last.opcode in {
            "ret", 
            "jr", 
            "jalr", 
        }: 
            pass


        # Ordinary fall-through
        else: 

            if index + 1 < len(blocks): 
                successors.append(
                    blocks[index + 1].id
                )


        # Remove duplicates while preserving order.

        block.successors = list(
            dict.fromkeys(successors)
        )


    # Predecessors

    for block in blocks: 

        for successor in block.successors: 

            blocks[
                successor
            ].predecessors.append(
                block.id
            )


    return CFG(
        blocks = blocks, 
        address_to_block = address_to_block, 
    )