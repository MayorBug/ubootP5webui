/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __P5_RECOVERY_H
#define __P5_RECOVERY_H
#include <p5_board.h>
#define P5_BOOT_INSTALLED 1
#define P5_BOOT_INITRAMFS 2
#define P5_BOOT_UBOOT 3
#define P5_INSTALL_CHECK 4
#define P5_BOOT_FLASH_TEST 5
int p5_boot_check(int kind, ulong address, ulong size);
int p5_boot_run(int kind, ulong address, ulong size);
void p5_network_start(void);
int p5_install_check(ulong address, ulong size);
int p5_install_preflight(ulong address, ulong size);
int p5_install_run(ulong address, ulong size);
int p5_install_boot_blocked(void);
#endif
