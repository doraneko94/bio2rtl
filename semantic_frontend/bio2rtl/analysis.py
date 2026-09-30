from dataclasses import dataclass, field

from .parser import Disassembly
from .riscv import (
    BIO_SPECIAL_REGS, 
    bio_registers_used, 
    instruction_registers, 
)


@dataclass
class BioRegisterUsage: 
    register: str
    semantic: str

    addresses: list[int] = field(
        default_factory = list
    )

    opcodes: list[str] = field(
        default_factory = list
    )


@dataclass
class AnalysisResult: 
    instruction_count: int

    opcode_counts: dict[str, int]

    normal_registers: set[str]

    bio_registers: dict[
        str, 
        BioRegisterUsage
    ]

    labels: dict[str, int]


def analyze(
    dis: Disassembly, 
) -> AnalysisResult: 

    opcode_counts: dict[str, int] = {}

    normal_registers = set()

    bio_usage: dict[
        str, 
        BioRegisterUsage
    ] = {}


    for insn in dis.instructions: 

        opcode_counts[insn.opcode] = (
            opcode_counts.get(
                insn.opcode, 
                0, 
            )
            + 1
        )


        regs = instruction_registers(
            insn
        )

        normal_registers |= regs


        for reg, semantic in (
            bio_registers_used(insn).items()
        ): 

            if reg not in bio_usage: 

                bio_usage[reg] = (
                    BioRegisterUsage(
                        register = reg, 
                        semantic = semantic, 
                    )
                )

            bio_usage[
                reg
            ].addresses.append(
                insn.address
            )

            bio_usage[
                reg
            ].opcodes.append(
                insn.opcode
            )


    return AnalysisResult(
        instruction_count = len(
            dis.instructions
        ), 

        opcode_counts = opcode_counts, 

        normal_registers = 
            normal_registers, 

        bio_registers = bio_usage, 

        labels = dis.labels, 
    )