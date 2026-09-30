#include "bio.h"

/* Divide the GPIO16 event rate by four and emit a 75% duty waveform on GPIO18. */
__attribute__((naked)) void main(void)
{
    __asm__ volatile(
        "li x6, 0x10000\n"
        "li x7, 0x40000\n"
        "li x11, 0xfffbffff\n"
        "li x8, 0\n"
        "li x9, 0\n"
        "mv x24, x7\n"
        "1:\n"
        "mv x5, x21\n"
        "and x5, x5, x6\n"
        "beq x5, x8, 1b\n"
        "mv x8, x5\n"
        "beqz x5, 1b\n"
        "beqz x9, 2f\n"
        "li x10, 1\n"
        "beq x9, x10, 3f\n"
        "mv x22, x7\n"
        "j 4f\n"
        "2: mv x23, x11\n"
        "j 4f\n"
        "3: mv x22, x7\n"
        "4: addi x9, x9, 1\n"
        "andi x9, x9, 3\n"
        "j 1b\n"
    );
}
