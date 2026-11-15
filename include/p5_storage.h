/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __P5_STORAGE_H
#define __P5_STORAGE_H
#include <p5_board.h>
struct p5_part { char name[16]; u32 start, blocks, attr; };
u32 p5_mibib_crc(const u8 *bytes, u32 length);
int p5_mibib_validate(const u8 *block, u32 size);
int p5_mibib_make(u8 *out, const u8 *in, u32 size, int single, u32 age);
int p5_mibib_select(const u8 *mibib, u32 size);
int p5_storage_busy(void);
/* Returns 1 for unchanged state, 0 for other success, or a negative error. */
int p5_storage_execute(const char *action, ulong uploaded, u32 receipt_crc);
int p5_storage_boot_blocked(void);
#endif
