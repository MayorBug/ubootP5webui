/* SPDX-License-Identifier: GPL-2.0+ */
/* P5 modem control: GPIO35 active-low power, GPIO30 rising-edge reset. */
#include <common.h>
#include <asm/gpio.h>
#include <p5_modem.h>

static int boot_enabled(const char *key)
{
	const char *value = getenv(key);
	return value && !strcmp(value, "1");
}

int p5_modem_boot_enabled(void)
{
	return boot_enabled("p5_modem_on_boot");
}

int p5_modem_reset_boot_enabled(void)
{
	return boot_enabled("p5_modem_reset_boot");
}

static void reset_pulse(void)
{
	gpio_set_value(30, 1);
	mdelay(1000);
	gpio_set_value(30, 0);
}

void p5_modem_apply_boot(void)
{
	gpio_set_value(35, p5_modem_boot_enabled() ? 0 : 1);
	if (p5_modem_reset_boot_enabled())
		reset_pulse();
}

int p5_modem_action(const char *action)
{
	if (!strcmp(action, "power-on"))
		gpio_set_value(35, 0);
	else if (!strcmp(action, "power-off"))
		gpio_set_value(35, 1);
	else if (!strcmp(action, "reset")) {
		reset_pulse();
	} else if (!strcmp(action, "boot-on") || !strcmp(action, "boot-off") ||
		   !strcmp(action, "reset-boot-on") || !strcmp(action, "reset-boot-off")) {
		const char *key = !strncmp(action, "reset-", 6) ?
			"p5_modem_reset_boot" : "p5_modem_on_boot";
		int enabled = !strcmp(action, "boot-on") || !strcmp(action, "reset-boot-on");
		/* Invalid/unset values are off; restore that state on save failure. */
		int previous = boot_enabled(key);
		if (setenv(key, enabled ? "1" : "0"))
			return -1;
		if (saveenv()) {
			setenv(key, previous ? "1" : "0");
			return -1;
		}
	} else
		return -1;
	return 0;
}
