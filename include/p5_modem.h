/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __P5_MODEM_H
#define __P5_MODEM_H
int p5_modem_boot_enabled(void);
int p5_modem_reset_boot_enabled(void);
void p5_modem_apply_boot(void);
int p5_modem_action(const char *action);
#endif
