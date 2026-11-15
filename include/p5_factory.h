/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __P5_FACTORY_H
#define __P5_FACTORY_H
#include <p5_board.h>
int p5_factory_check(ulong address, ulong size);
int p5_factory_run(ulong address, ulong size);
int p5_factory_boot_blocked(void);
#endif
