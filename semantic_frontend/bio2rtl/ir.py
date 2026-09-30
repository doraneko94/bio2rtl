from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IROp: 
    kind: str

    dst: Optional[str] = None

    args: list[str] = field(
        default_factory = list
    )

    target: Optional[int] = None

    address: Optional[int] = None

    comment: str = ""

    def __str__(self) -> str: 

        prefix = ""

        if self.address is not None: 
            prefix = f"0x{self.address:04x}: "


        if self.kind == "ASSIGN": 

            return (
                f"{prefix}"
                f"{self.dst} := {self.args[0]}"
            )


        if self.kind == "CONST": 

            return (
                f"{prefix}"
                f"{self.dst} := {self.args[0]}"
            )


        if self.kind in {
            "ADD", 
            "AND", 
            "OR", 
            "SHL", 
            "SHR", 
        }: 

            operator = {
                "ADD": "+", 
                "AND": "&", 
                "OR": "|", 
                "SHL": "<<", 
                "SHR": ">>", 
            }[self.kind]

            return (
                f"{prefix}"
                f"{self.dst} := "
                f"{self.args[0]} "
                f"{operator} "
                f"{self.args[1]}"
            )


        if self.kind == "LOAD": 

            return (
                f"{prefix}"
                f"{self.dst} := "
                f"MEM[{self.args[0]}]"
            )


        if self.kind == "STORE": 

            return (
                f"{prefix}"
                f"MEM[{self.args[0]}] := "
                f"{self.args[1]}"
            )


        if self.kind == "GPIO_READ": 

            return (
                f"{prefix}"
                f"{self.dst} := GPIO_IN"
            )


        if self.kind == "GPIO_SET": 

            return (
                f"{prefix}"
                f"GPIO_SET({self.args[0]})"
            )


        if self.kind == "GPIO_CLEAR_N": 

            return (
                f"{prefix}"
                f"GPIO_CLEAR_N({self.args[0]})"
            )


        if self.kind == "GPIO_DIR_SET": 

            return (
                f"{prefix}"
                f"GPIO_DIR_SET({self.args[0]})"
            )


        if self.kind == "GPIO_DIR_CLEAR": 

            return (
                f"{prefix}"
                f"GPIO_DIR_CLEAR({self.args[0]})"
            )


        if self.kind == "GPIO_MASK": 

            return (
                f"{prefix}"
                f"GPIO_MASK := {self.args[0]}"
            )


        if self.kind == "BRANCH": 

            return (
                f"{prefix}"
                f"IF {self.comment} "
                f"-> 0x{self.target:04x}"
            )


        if self.kind == "JUMP": 

            return (
                f"{prefix}"
                f"GOTO 0x{self.target:04x}"
            )


        if self.kind == "CALL": 

            return (
                f"{prefix}"
                f"CALL 0x{self.target:04x}"
            )


        if self.kind == "RETURN": 

            return (
                f"{prefix}RETURN"
            )

        if self.kind == "STATE_WRITE": 

            return (
                f"{prefix}"
                f"{self.dst} := "
                f"{self.args[0]}"
            )


        return (
            f"{prefix}"
            f"{self.kind} "
            f"{', '.join(self.args)}"
        )


@dataclass
class IRBlock: 
    id: int
    start: int

    ops: list[IROp]

    successors: list[int]
    predecessors: list[int]