import re

from .cfg import CFG
from .ir import IROp, IRBlock
from .riscv import (
    Instruction, 
    normalize_register, 
    branch_target, 
)


BIO_READ_REGS = {
    "gpio": "GPIO_READ", 
}


BIO_WRITE_REGS = {
    "gpset": "GPIO_SET", 
    "gpclrn": "GPIO_CLEAR_N", 
    "dirout": "GPIO_DIR_SET", 
    "dirin": "GPIO_DIR_CLEAR", 
    "gpmask": "GPIO_MASK", 
}


# Generic Ver.1 fail-closed ISA contract.  An opcode is listed here only when this
# lowering module has an exact Hardware-IR representation for it.  Context-specific
# restrictions in later semantic passes remain additional acceptance conditions.
SUPPORTED_BRANCH_OPS = {"beq", "bne", "blt", "bltu", "bgeu", "beqz", "bnez"}
SUPPORTED_OPCODES = {
    "mv", "li", "lui", "addi", "add", "andi", "and", "or", 
    "slli", "srli", "srl", "zext.b", "lw", "lbu", "sw", 
    *SUPPORTED_BRANCH_OPS, "j", "jal", "ret", 
}

def unsupported_ir_ops(blocks): 
    return [op for b in blocks for op in b.ops if op.kind == "UNSUPPORTED"]

def require_supported_ir(blocks): 
    bad = unsupported_ir_ops(blocks)
    if bad: 
        rows = []
        for op in bad[:32]: 
            rows.append({"address": op.address, "args": list(op.args), "comment": op.comment})
        raise RuntimeError(f"unsupported BIO/RISC-V instructions after lowering: {rows}")
    return blocks


def reg_name(value: str) -> str: 

    value = value.strip()

    normalized = normalize_register(
        value
    )

    if normalized is not None: 
        return normalized

    return value


def parse_memory_operand(
    value: str, 
) -> str: 

    value = value.strip()

    # objdump may append a resolved-address comment:
    #
    #   -4(x2) # ffc <symbol+0xffc>
    #
    # Only the actual RISC-V memory operand is relevant
    # to Hardware IR.

    if "#" in value: 
        value = value.split(
            "#", 
            1, 
        )[0].strip()


    match = re.match(
        r"^(-?\d+)\(([^)]+)\)$", 
        value, 
    )

    if not match: 
        return value


    offset = int(
        match.group(1)
    )

    base = reg_name(
        match.group(2)
    )


    if offset == 0: 
        return base

    if offset > 0: 
        return (
            f"{base}+{offset}"
        )

    return (
        f"{base}{offset}"
    )

def immediate(value: str) -> str: 

    value = value.strip()

    return value


def lower_mv(
    insn: Instruction, 
) -> list[IROp]: 

    if len(insn.operands) != 2: 
        return [
            IROp(
                kind = "UNSUPPORTED", 
                args = [insn.text], 
                address = insn.address, 
            )
        ]


    dst_raw = insn.operands[0]
    src_raw = insn.operands[1]


    # --------------------------------------------------------
    # BIO special-register READ
    # --------------------------------------------------------

    if src_raw in BIO_READ_REGS: 

        return [
            IROp(
                kind = BIO_READ_REGS[src_raw], 
                dst = reg_name(dst_raw), 
                address = insn.address, 
            )
        ]


    # --------------------------------------------------------
    # BIO special-register WRITE
    # --------------------------------------------------------

    if dst_raw in BIO_WRITE_REGS: 

        return [
            IROp(
                kind = BIO_WRITE_REGS[dst_raw], 
                args = [
                    reg_name(src_raw)
                ], 
                address = insn.address, 
            )
        ]


    # --------------------------------------------------------
    # Normal register copy
    # --------------------------------------------------------

    return [
        IROp(
            kind = "ASSIGN", 
            dst = reg_name(dst_raw), 
            args = [
                reg_name(src_raw)
            ], 
            address = insn.address, 
        )
    ]


def branch_condition(
    insn: Instruction, 
) -> str: 

    op = insn.opcode
    a = insn.operands


    if op == "beqz": 
        return (
            f"{reg_name(a[0])} == 0"
        )


    if op == "bnez": 
        return (
            f"{reg_name(a[0])} != 0"
        )


    if op == "beq": 
        return (
            f"{reg_name(a[0])} "
            f"== {reg_name(a[1])}"
        )


    if op == "bne": 
        return (
            f"{reg_name(a[0])} "
            f"!= {reg_name(a[1])}"
        )


    if op == "blt": 
        return (
            f"signed({reg_name(a[0])}) "
            f"< signed({reg_name(a[1])})"
        )


    if op == "bltu": 
        return (
            f"{reg_name(a[0])} "
            f"<u {reg_name(a[1])}"
        )


    if op == "bgeu": 
        return (
            f"{reg_name(a[0])} "
            f">=u {reg_name(a[1])}"
        )


    return (
        f"{op} "
        + ", ".join(a[:-1])
    )


def lower_instruction(
    insn: Instruction, 
) -> list[IROp]: 

    op = insn.opcode
    a = insn.operands


    # ========================================================
    # BIO register accesses
    # ========================================================

    if op == "mv": 
        return lower_mv(insn)


    # ========================================================
    # Constants
    # ========================================================

    if op == "li": 

        return [
            IROp(
                kind = "CONST", 
                dst = reg_name(a[0]), 
                args = [
                    immediate(a[1])
                ], 
                address = insn.address, 
            )
        ]


    if op == "lui": 

        return [
            IROp(
                kind = "CONST", 
                dst = reg_name(a[0]), 
                args = [
                    f"({immediate(a[1])} << 12)"
                ], 
                address = insn.address, 
            )
        ]


    # ========================================================
    # Arithmetic
    # ========================================================

    if op == "addi": 

        return [
            IROp(
                kind = "ADD", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    immediate(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "add": 

        return [
            IROp(
                kind = "ADD", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    reg_name(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "andi": 

        return [
            IROp(
                kind = "AND", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    immediate(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "and": 

        return [
            IROp(
                kind = "AND", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    reg_name(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "or": 

        return [
            IROp(
                kind = "OR", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    reg_name(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "slli": 

        return [
            IROp(
                kind = "SHL", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    immediate(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "srli": 

        return [
            IROp(
                kind = "SHR", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    immediate(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "srl": 

        return [
            IROp(
                kind = "SHR", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    reg_name(a[2]), 
                ], 
                address = insn.address, 
            )
        ]


    if op == "zext.b": 

        return [
            IROp(
                kind = "AND", 
                dst = reg_name(a[0]), 
                args = [
                    reg_name(a[1]), 
                    "0xff", 
                ], 
                address = insn.address, 
            )
        ]


    # ========================================================
    # Memory
    # ========================================================

    if op in {
        "lw", 
        "lbu", 
    }: 

        return [
            IROp(
                kind = "LOAD", 
                dst = reg_name(a[0]), 
                args = [
                    parse_memory_operand(
                        a[1]
                    )
                ], 
                address = insn.address, 
                comment = op, 
            )
        ]


    if op == "sw": 

        return [
            IROp(
                kind = "STORE", 
                args = [
                    parse_memory_operand(
                        a[1]
                    ), 
                    reg_name(a[0]), 
                ], 
                address = insn.address, 
            )
        ]


    # ========================================================
    # Conditional control flow
    # ========================================================

    if insn.is_branch: 

        if op not in SUPPORTED_BRANCH_OPS: 
            return [IROp(kind = "UNSUPPORTED", args = [insn.opcode, *insn.operands], 
                         address = insn.address, comment = insn.text)]
        target = branch_target(insn)
        if target is None: 
            return [IROp(kind = "UNSUPPORTED", args = [insn.opcode, *insn.operands], 
                         address = insn.address, comment = "unresolved branch target: "+insn.text)]

        return [
            IROp(
                kind = "BRANCH", 
                target = target, 
                address = insn.address, 
                comment = branch_condition(
                    insn
                ), 
            )
        ]


    # ========================================================
    # Direct jump
    # ========================================================

    if op == "j": 
        target = branch_target(insn)
        if target is None: 
            return [IROp(kind = "UNSUPPORTED", args = [insn.opcode, *insn.operands], address = insn.address, comment = "unresolved jump target: "+insn.text)]
        return [IROp(kind = "JUMP", target = target, address = insn.address)]


    # ========================================================
    # Call
    # ========================================================

    if op == "jal": 
        target = branch_target(insn)
        if target is None: 
            return [IROp(kind = "UNSUPPORTED", args = [insn.opcode, *insn.operands], address = insn.address, comment = "unresolved call target: "+insn.text)]
        return [IROp(kind = "CALL", target = target, address = insn.address)]


    if op == "ret": 

        return [
            IROp(
                kind = "RETURN", 
                address = insn.address, 
            )
        ]


    # ========================================================
    # Temporarily unsupported
    # ========================================================

    return [
        IROp(
            kind = "UNSUPPORTED", 
            args = [
                insn.opcode, 
                *insn.operands, 
            ], 
            address = insn.address, 
            comment = insn.text, 
        )
    ]


def lower_cfg(
    cfg: CFG, 
) -> list[IRBlock]: 

    result = []


    for block in cfg.blocks: 

        ops = []

        for insn in block.instructions: 

            ops.extend(
                lower_instruction(
                    insn
                )
            )


        result.append(
            IRBlock(
                id = block.id, 
                start = block.start, 
                ops = ops, 
                successors = 
                    list(block.successors), 
                predecessors = 
                    list(block.predecessors), 
            )
        )


    return result