/* SPDX-License-Identifier: GPL-2.0+ */
/* Runs from scratch RAM. No stack accesses, external calls or literal pool. */
.syntax unified
.section .text.p5_chain,"ax"
.arm
.balign 4
.global p5_chain_start
.global p5_chain_end
.type p5_chain_start,%function
p5_chain_start:
 mov sp, r3
 mov r11, r2
 cpsid if
 dsb sy
 isb sy
1:
 cmp r1, #0
 beq 6f
 ldmia r0!, {r4,r5,r6}
 cmp r6, #0
 beq 5f
 cmp r5, #0
 beq 3f
2:
 ldrb r7, [r5], #1
 strb r7, [r4], #1
 subs r6, r6, #1
 bne 2b
 b 5f
3:
 mov r7, #0
4:
 strb r7, [r4], #1
 subs r6, r6, #1
 bne 4b
5:
 subs r1, r1, #1
 b 1b
6:
 dsb sy
 mrc p15, 0, r0, c1, c0, 0
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
 adr lr, 7f
 bx r11
7:
 b 7b
p5_chain_end:
.size p5_chain_start,.-p5_chain_start
