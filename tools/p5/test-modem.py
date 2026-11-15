#!/usr/bin/env python3
"""Run the production modem policy against GPIO/environment mocks."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
source='\n'.join(l for l in (root/'board/qca/arm/common/p5_modem.c').read_text().splitlines() if not l.startswith('#include'))
prelude=r"""
#include <assert.h>
#include <string.h>
static char env[16],reset_env[16];static int fail_save,saves,writes,last_gpio,last_value,waits;
static const char *getenv(const char *key){const char *v=!strcmp(key,"p5_modem_on_boot")?env:reset_env;return v[0]?v:0;}
static int setenv(const char *key,const char *value){strcpy(!strcmp(key,"p5_modem_on_boot")?env:reset_env,value);return 0;}
static int saveenv(void){saves++;return fail_save;}
static void gpio_set_value(int gpio,int value){assert(gpio==30||gpio==35);writes++;last_gpio=gpio;last_value=value;}
static void mdelay(int delay){assert(delay==1000);assert(last_gpio==30&&last_value==1);waits++;}
"""
test=r"""
int main(void){
 assert(!p5_modem_boot_enabled());p5_modem_apply_boot();assert(last_gpio==35&&last_value==1);
 assert(!p5_modem_action("power-on"));assert(last_gpio==35&&last_value==0&&!saves);
 assert(!p5_modem_action("power-off"));assert(last_gpio==35&&last_value==1&&!saves);
 int n=writes;assert(!p5_modem_action("boot-on"));assert(p5_modem_boot_enabled()&&saves==1&&writes==n);
 p5_modem_apply_boot();assert(last_value==0);
 fail_save=1;assert(p5_modem_action("boot-off"));assert(p5_modem_boot_enabled());
 fail_save=0;assert(!p5_modem_action("boot-off"));assert(!p5_modem_boot_enabled());
 assert(!p5_modem_action("reset"));assert(last_gpio==30&&last_value==0&&waits==1);
 n=writes;assert(p5_modem_action("power-toggle"));assert(writes==n);
 strcpy(env,"invalid");assert(!p5_modem_boot_enabled());
 assert(!p5_modem_reset_boot_enabled());n=writes;
 assert(!p5_modem_action("reset-boot-on"));assert(writes==n&&p5_modem_reset_boot_enabled());
 fail_save=1;assert(p5_modem_action("reset-boot-off"));assert(p5_modem_reset_boot_enabled());fail_save=0;
 for(int power=0;power<2;power++)for(int reset=0;reset<2;reset++){
  assert(!p5_modem_action(power?"boot-on":"boot-off"));assert(!p5_modem_action(reset?"reset-boot-on":"reset-boot-off"));
  n=waits;int old=writes;p5_modem_apply_boot();assert(waits==n+reset&&writes==old+1+2*reset);
  if(reset)assert(last_gpio==30&&last_value==0);else assert(last_gpio==35&&last_value==!power);
 }
 return 0;
}
"""
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(prelude+source+test)
 subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS: default-off boot, active-low runtime power, isolated saved preference, save failure rollback and reset pulse')
