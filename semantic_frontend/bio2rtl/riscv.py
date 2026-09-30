from dataclasses import dataclass
from typing import Optional
import re


# ============================================================
# Normal RISC-V register names
# ============================================================

ABI_TO_X = {
    "zero": "x0", 
    "ra": "x1", 
    "sp": "x2", 
    "gp": "x3", 
    "tp": "x4", 

    "t0": "x5", 
    "t1": "x6", 
    "t2": "x7", 

    "s0": "x8", 
    "fp": "x8", 
    "s1": "x9", 

    "a0": "x10", 
    "a1": "x11", 
    "a2": "x12", 
    "a3": "x13", 
    "a4": "x14", 
    "a5": "x15", 
}


# ============================================================
# Names inserted into .dis by official map_regs.py
# ============================================================

BIO_NAME_TO_X = {
    "fifo0": "x16", 
    "fifo1": "x17", 
    "fifo2": "x18", 
    "fifo3": "x19", 

    "quant": "x20", 

    "gpio": "x21", 
    "gpset": "x22", 
    "gpclrn": "x23", 
    "dirout": "x24", 
    "dirin": "x25", 
    "gpmask": "x26", 

    "evmask": "x27", 
    "evset": "x28", 
    "evclr": "x29", 
    "evwait": "x30", 
    "idclk": "x31", 
}


BIO_SPECIAL_REGS = {
    "x16": "FIFO0", 
    "x17": "FIFO1", 
    "x18": "FIFO2", 
    "x19": "FIFO3", 

    "x20": "QUANTUM", 

    "x21": "GPIO", 
    "x22": "GPIO_SET", 
    "x23": "GPIO_CLEAR_N", 
    "x24": "GPIO_DIR_SET", 
    "x25": "GPIO_DIR_CLEAR", 
    "x26": "GPIO_MASK", 

    "x27": "EVENT_MASK", 
    "x28": "EVENT_SET", 
    "x29": "EVENT_CLEAR", 
    "x30": "EVENT_WAIT", 
    "x31": "IDCLK", 
}


BRANCH_OPS = {
    "beq", 
    "bne", 
    "blt", 
    "bge", 
    "bltu", 
    "bgeu", 
    "beqz", 
    "bnez", 
    "blez", 
    "bgez", 
    "bltz", 
    "bgtz", 
}


UNCONDITIONAL_JUMPS = {
    "j", 
    "jr", 
    "jal", 
    "jalr", 
    "ret", 
}


CONTROL_FLOW_OPS = (
    BRANCH_OPS
    | UNCONDITIONAL_JUMPS
)


@dataclass
class Instruction: 
    address: int
    raw_bytes: str
    opcode: str
    operands: list[str]
    text: str
    source_line: int
    label: Optional[str] = None

    @property
    def size(self) -> int: 
        return len(self.raw_bytes) // 2

    @property
    def next_address(self) -> int: 
        return self.address + self.size

    @property
    def is_branch(self) -> bool: 
        return self.opcode in BRANCH_OPS

    @property
    def is_jump(self) -> bool: 
        return self.opcode in UNCONDITIONAL_JUMPS

    @property
    def is_control_flow(self) -> bool: 
        return self.opcode in CONTROL_FLOW_OPS


def normalize_register(
    name: str, 
) -> Optional[str]: 

    name = name.strip()

    if name in BIO_NAME_TO_X: 
        return BIO_NAME_TO_X[name]

    if name in ABI_TO_X: 
        return ABI_TO_X[name]

    if name.startswith("x"): 
        tail = name[1:]

        if tail.isdigit(): 
            number = int(tail)

            if 0 <= number <= 31: 
                return f"x{number}"

    return None


def registers_in_operand(
    operand: str, 
) -> set[str]: 

    found = set()

    cleaned = (
        operand
        .replace("(", " ")
        .replace(")", " ")
        .replace(",", " ")
    )

    for token in cleaned.split(): 

        reg = normalize_register(token)

        if reg is not None: 
            found.add(reg)

    return found


def instruction_registers(
    insn: Instruction, 
) -> set[str]: 

    result = set()

    for operand in insn.operands: 
        result |= registers_in_operand(
            operand
        )

    return result


def bio_registers_used(
    insn: Instruction, 
) -> dict[str, str]: 

    result = {}

    for reg in instruction_registers(insn): 

        if reg in BIO_SPECIAL_REGS: 
            result[reg] = (
                BIO_SPECIAL_REGS[reg]
            )

    return result


def parse_target_address(
    operand: str, 
) -> Optional[int]: 
    """
    Examples from objdump:

        "94 <BM_...+0x94>"
        "3ac <BM_...+0x3ac>"
        "x11"
        "0(x2)"

    Return the leading hexadecimal address when present.
    """

    operand = operand.strip()

    match = re.match(
        r"^([0-9a-fA-F]+)"
        r"(?:\s+<.*>)?$", 
        operand, 
    )

    if not match: 
        return None

    return int(
        match.group(1), 
        16, 
    )


def branch_target(
    insn: Instruction, 
) -> Optional[int]: 

    if not insn.is_control_flow: 
        return None

    if not insn.operands: 
        return None

    # Branch/j/jal target is normally the final operand.
    return parse_target_address(
        insn.operands[-1]
    )