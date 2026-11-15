/* SPDX-License-Identifier: GPL-2.0+ */
/* Stock bootelf flushes loaded data and disables D-cache before calling us. */
#include <config.h>

	.syntax unified
	.section .p5_ramtest_entry, "ax"
	.balign 4
	.thumb
	.thumb_func
	.global p5_ramtest_entry
p5_ramtest_entry:
	bx pc
	nop
	.arm
	/* Fail back to the caller rather than discard dirty cache contents. */
	mrc p15, 0, r0, c1, c0, 0
	tst r0, #4
	movne r0, #1
	bxne lr
	mov r0, #65
	bl p5_early_mark
	mrc p15, 0, r0, c1, c0, 0
	cpsid if
	dsb sy
	/* Clear M (MMU), I (I-cache), and Z (branch prediction). C is off. */
	bic r0, r0, #1
	bic r0, r0, #(1 << 12)
	bic r0, r0, #(1 << 11)
	mcr p15, 0, r0, c1, c0, 0
	isb sy
	mov r0, #0
	mcr p15, 0, r0, c7, c5, 0
	mcr p15, 0, r0, c7, c5, 6
	mcr p15, 0, r0, c8, c7, 0
	dsb sy
	isb sy
	mov r0, #66
	bl p5_early_mark
	b reset

/* Reuse the already configured stock BLSP UART. Bounded polling, no stack/gd. */
	.text
	.arm
	.global p5_early_mark
	.type p5_early_mark, %function
p5_early_mark:
	ldr r1, =0x078af000
	mov r3, #0x10000
1:
	ldr r2, [r1, #0xa4]
	tst r2, #8
	bne 2f
	subs r3, r3, #1
	bne 1b
	bx lr
2:
	mov r2, #1
	str r2, [r1, #0x40]
	mov r2, #0x300
	str r2, [r1, #0xa8]
	str r0, [r1, #0x100]
	dsb sy
	bx lr
	.size p5_early_mark, .-p5_early_mark
