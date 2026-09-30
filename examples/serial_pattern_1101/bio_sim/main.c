#include "bio.h"

/* Emit the fixed serial pattern 1,1,0,1 on GPIO18, one bit per GPIO16 rising edge. */
__attribute__((naked)) void main(void)
{
    __asm__ volatile(
        "li x6, 0x10000\n"
        "li x7, 0x40000\n"
        "li x11, 0xfffbffff\n"
        "li x8, 0\n"
        "li x9, 0xb\n"
        "li x10, 0\n"
        "mv x24, x7\n"
        "1:\n"
        "mv x5, x21\n"
        "and x5, x5, x6\n"
        "beq x5, x8, 1b\n"
        "mv x8, x5\n"
        "beqz x5, 1b\n"
        "andi x5, x9, 1\n"
        "beqz x5, 2f\n"
        "mv x22, x7\n"
        "j 3f\n"
        "2: mv x23, x11\n"
        "3: srli x9, x9, 1\n"
        "addi x10, x10, 1\n"
        "li x12, 4\n"
        "bne x10, x12, 1b\n"
        "4: j 4b\n"
    );
}
