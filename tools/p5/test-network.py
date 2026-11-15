from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[2]
s=(r/'board/qca/arm/common/p5_recovery.c').read_text();f=s[s.index('static int do_p5net('):s.index('U_BOOT_CMD(p5net,')]
pre='''#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <arpa/inet.h>
typedef int cmd_tbl_t;
struct in_addr net_ip,net_server_ip,net_netmask;
static char keys[8][32],values[8][40];static int n;
static int fake_setenv(const char *k,const char *v){strcpy(keys[n],k);strcpy(values[n++],v);return 0;}
#define setenv fake_setenv
static struct in_addr getenv_ip(const char *k){struct in_addr a={0};for(int i=0;i<n;i++)if(!strcmp(keys[i],k))inet_pton(AF_INET,values[i],&a);return a;}
'''
post='''int main(void){inet_pton(AF_INET,"192.168.1.85",&net_server_ip);do_p5net(0,0,0,0);assert(net_server_ip.s_addr==inet_addr("192.168.1.88"));assert(net_ip.s_addr==inet_addr("192.168.1.1"));assert(net_netmask.s_addr==inet_addr("255.255.255.0"));puts("PASS production p5net updates live addresses despite skipped callbacks");}'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(pre+f+post);subprocess.run(['cc','-Wall','-Wextra','-Wno-unused-parameter',str(p/'test.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
