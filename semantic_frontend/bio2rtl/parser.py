import re
from dataclasses import dataclass, field
from pathlib import Path

from .riscv import Instruction


# ============================================================
# Objdump syntax
# ============================================================


# Typical objdump label:
#
# 00000000 <_start>:
#
LABEL_RE = re.compile(
    r"^\s*"
    r"([0-9a-fA-F]+)"
    r"\s+"
    r"<([^>]+)>:"
    r"\s*$"
)


# Typical objdump instruction:
#
#    4: 00100513   li a0,1
#
# Baochip/BIO objdump can also replace an address-looking token
# with a special register alias, for example:
#
#   x10:    6441       lui  x8,0x10
#   x12:    0084f0b3   and  x1,x9,x8
#   fifo0:  02031863   bnez x6,d6
#
# Therefore the field before ":" must NOT be restricted to hex.
#
# The real address is recovered later from the previous
# instruction when this token is not hexadecimal.
#
INSN_RE = re.compile(
    r"^\s*"
    r"([A-Za-z0-9_.$]+)"
    r":\s+"
    r"([0-9a-fA-F ]+?)"
    r"\s{2,}"
    r"([a-zA-Z0-9_.]+)"
    r"(?:\s+(.*?))?"
    r"\s*$"
)


HEX_ADDRESS_RE = re.compile(
    r"^[0-9a-fA-F]+$"
)


@dataclass
class Disassembly: 
    instructions: list[Instruction] = field(
        default_factory = list
    )

    labels: dict[str, int] = field(
        default_factory = dict
    )

    address_to_label: dict[int, str] = field(
        default_factory = dict
    )


# ============================================================
# Helpers
# ============================================================


def split_operands(
    text: str, 
) -> list[str]: 

    if not text: 
        return []

    return [
        part.strip()

        for part in text.split(",")

        if part.strip()
    ]


def instruction_size(
    raw_bytes: str, 
) -> int: 

    cleaned = (
        raw_bytes
        .replace(" ", "")
        .strip()
    )

    if not cleaned: 

        raise RuntimeError(
            "instruction has no raw bytes"
        )

    if len(cleaned) % 2 != 0: 

        raise RuntimeError(
            "instruction raw-byte field "
            f"has odd hex length: {cleaned}"
        )

    size = (
        len(cleaned)
        // 2
    )

    if size <= 0: 

        raise RuntimeError(
            "invalid instruction size"
        )

    return size


def infer_address(
    address_token: str, 
    previous_instruction: Instruction | None, 
    line_number: int, 
) -> int: 

    # --------------------------------------------------------
    # Normal objdump address
    # --------------------------------------------------------

    if HEX_ADDRESS_RE.fullmatch(
        address_token
    ): 

        return int(
            address_token, 
            16, 
        )

    # --------------------------------------------------------
    # BIO register-alias token
    #
    # Example:
    #
    #   9c:    0084f333 ...
    #   x10:   6441 ...
    #
    # x10 is not the address. The address is:
    #
    #   previous address
    #   + previous instruction byte length
    #
    # --------------------------------------------------------

    if previous_instruction is None: 

        raise RuntimeError(
            "cannot infer instruction address "
            f"for token '{address_token}' "
            f"at source line {line_number}: "
            "no previous instruction"
        )

    previous_size = instruction_size(
        previous_instruction.raw_bytes
    )

    return (
        previous_instruction.address
        + previous_size
    )


# ============================================================
# Parser
# ============================================================


def parse_disassembly(
    filename: str | Path, 
) -> Disassembly: 

    filename = Path(
        filename
    )

    result = Disassembly()

    pending_label: str | None = None

    lines = filename.read_text(
        encoding = "utf-8", 
        errors = "replace", 
    ).splitlines()

    previous_instruction: (
        Instruction
        | None
    ) = None

    used_addresses: set[int] = set()


    for (
        line_number, 
        line, 
    ) in enumerate(
        lines, 
        start = 1, 
    ): 

        # ====================================================
        # Symbol label
        # ====================================================

        label_match = (
            LABEL_RE.match(
                line
            )
        )

        if label_match: 

            address = int(
                label_match.group(1), 
                16, 
            )

            label = (
                label_match.group(2)
            )

            result.labels[
                label
            ] = address

            result.address_to_label[
                address
            ] = label

            pending_label = (
                label
            )

            continue


        # ====================================================
        # Instruction
        # ====================================================

        insn_match = (
            INSN_RE.match(
                line
            )
        )

        if not insn_match: 
            continue


        address_token = (
            insn_match
            .group(1)
            .strip()
        )

        raw_bytes = (
            insn_match
            .group(2)
            .replace(" ", "")
        )

        opcode = (
            insn_match
            .group(3)
            .lower()
        )

        operand_text = (
            insn_match.group(4)
            or ""
        )


        # ====================================================
        # Address recovery
        # ====================================================

        address = infer_address(
            address_token, 
            previous_instruction, 
            line_number, 
        )


        # ====================================================
        # Sanity checks
        # ====================================================

        if address in used_addresses: 

            raise RuntimeError(
                "duplicate instruction address "
                f"0x{address:x} "
                f"at source line {line_number}: "
                f"{line.strip()}"
            )


        # When the source contains a normal explicit address,
        # check continuity with the preceding instruction.
        #
        # Do not require continuity across a symbol label because
        # objdump may contain separate functions/regions.
        #
        if (
            previous_instruction
            is not None
            and
            HEX_ADDRESS_RE.fullmatch(
                address_token
            )
            and
            pending_label is None
        ): 

            expected_address = (
                previous_instruction.address
                + instruction_size(
                    previous_instruction.raw_bytes
                )
            )

            if (
                address
                < expected_address
            ): 

                raise RuntimeError(
                    "instruction addresses move backwards "
                    f"at source line {line_number}: "
                    f"expected >= 0x{expected_address:x}, "
                    f"got 0x{address:x}"
                )


        # ====================================================
        # Build instruction
        # ====================================================

        insn = Instruction(
            address = address, 
            raw_bytes = raw_bytes, 
            opcode = opcode, 
            operands = split_operands(
                operand_text
            ), 
            text = line.strip(), 
            source_line = line_number, 
            label = pending_label, 
        )

        result.instructions.append(
            insn
        )

        used_addresses.add(
            address
        )

        previous_instruction = (
            insn
        )

        pending_label = None


    # ========================================================
    # Final checks
    # ========================================================

    if not result.instructions: 

        raise RuntimeError(
            f"No instructions parsed from "
            f"{filename}"
        )


    # --------------------------------------------------------
    # Verify monotonically increasing instruction addresses
    # within parsed order.
    # --------------------------------------------------------

    for (
        previous, 
        current, 
    ) in zip(
        result.instructions, 
        result.instructions[1:], 
    ): 

        if (
            current.address
            <= previous.address
        ): 

            raise RuntimeError(
                "parsed instruction order is "
                "not strictly increasing: "
                f"0x{previous.address:x} -> "
                f"0x{current.address:x}"
            )


    return result