#!/usr/bin/env python3
"""Exercise the production countdown with timed UART and button input."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root/'common/autoboot.c').read_text()
start = source.index('static int abortboot_normal(')
body = source[start:source.index('\n# endif\t/* CONFIG_AUTOBOOT_KEYED */', start)]
prelude = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_IPQ5018_CUDY_P5 1
#define CONFIG_ZERO_BOOTDELAY_CHECK 1
static unsigned long now;
static int uart_at, button_at, reads;
static const char *button;
static unsigned long get_timer(unsigned long base){return now-base;}
static void udelay(unsigned n){now+=n/1000;}
static int tstc(void){return uart_at>=0 && now>=(unsigned)uart_at;}
static int input_getc(void){reads++;return '\n';}
#define getc input_getc
#define putc(c) putchar(c)
static int btn_pressed(char *name,int size){
 if(button_at<0 || now<(unsigned)button_at)return 0;
 snprintf(name,size,"%s",button);return 1;
}
static void led_toggle(const char *name){(void)name;}
'''
checks = r'''
static void setup(int uart,int at,const char *name){now=0;reads=0;uart_at=uart;button_at=at;button=name;}
int main(void){
 setup(-1,-1,"");assert(abortboot_normal(5)==0 && now==5000);
 setup(1500,-1,"");assert(abortboot_normal(5)==1 && reads==1 && now==1500);
 setup(-1,1500,"wps");assert(abortboot_normal(5)==1 && now==1500);
 setup(-1,0,"reset");assert(abortboot_normal(5)==1 && now==0);
 puts("PASS: normal timeout, UART key, WPS during countdown, and held Reset");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='p5-autoboot-') as directory:
    path = Path(directory)
    (path/'test.c').write_text(prelude+body+checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    str(path/'test.c'), '-o', str(path/'test')], check=True)
    subprocess.run([str(path/'test')], check=True)
