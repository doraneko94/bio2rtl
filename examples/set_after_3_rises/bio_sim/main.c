#include "bio.h"

/* Set GPIO18 high after the third rising edge on GPIO16. */
__attribute__((naked)) void main(void)
{
    __asm__ volatile(
        "li x6, 0x10000\n"     /* TICK mask: GPIO16 */
        "li x7, 0x40000\n"     /* OUT mask: GPIO18 */
        "li x8, 0\n"           /* previous TICK */
        "li x9, 0\n"           /* counter */
        "mv x24, x7\n"         /* dirout */
        "1:\n"
        "mv x5, x21\n"         /* gpio */
        "and x5, x5, x6\n"
        "beq x5, x8, 1b\n"
        "mv x8, x5\n"
        "beqz x5, 1b\n"
        "addi x9, x9, 1\n"
        "li x10, 3\n"
        "bne x9, x10, 1b\n"
        "mv x22, x7\n"         /* gpset */
        "2: j 2b\n"
    );
}
