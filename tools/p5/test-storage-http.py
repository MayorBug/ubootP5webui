#!/usr/bin/env python3
"""Run actual HTTP queue handlers against mocks. No network or device access."""
import ctypes as C
from pathlib import Path
import subprocess,tempfile
source_path=Path(__file__).resolve().parents[2]/'failsafe/failsafe_httpd.c'
source=source_path.read_text()
handlers=source[source.index('static void httpd_handle_p5_storage_status'):source.index('static void httpd_handle_p5_boot')]
parse=source[source.index('static int httpd_parse_content_length'):source.index('static int httpd_parse_boundary')]
state=source[source.index('static char p5_action[240]'):source.index('#endif',source.index('static char p5_action[240]'))]
prelude=r"""
static int p5_modem_action(const char *s){(void)s;return 0;}
static int p5_storage_busy(void){return 0;}
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
typedef unsigned long ulong;
#define CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES 1
struct failsafe_httpd_state {unsigned long long upload_total;};
static int p5_boot_pending;static void *hs_global;
static ulong simple_strtoull(char *p,char **e,int b){return strtoull(p,e,b);}
static ulong get_timer(ulong base){return 1000-base;}
static void print_error(const char *text){(void)text;}
static char reply[512];
static void httpd_send_static(struct failsafe_httpd_state *hs,const char *text,int n){(void)hs;if(n>511)abort();memcpy(reply,text,n);reply[n]=0;}
"""
wrapper=r"""
int queue(const char *request){struct failsafe_httpd_state hs={0};char text[2048];strcpy(text,request);httpd_handle_p5_storage(&hs,text,strlen(text));return p5_action_pending;}
void reset(void){p5_action_pending=p5_action_sequence=p5_boot_pending=0;hs_global=0;}
void busy(int boot,int transfer){p5_boot_pending=boot;hs_global=transfer?(void*)1:0;}
const char *response(void){return reply;}
const char *action(void){return p5_action;}
void modem(const char *request){struct failsafe_httpd_state hs={0};char text[2048];strcpy(text,request);httpd_handle_p5_modem(&hs,text,strlen(text));}
void status(void){struct failsafe_httpd_state hs={0};httpd_handle_p5_storage_status(&hs);}
"""
with tempfile.TemporaryDirectory(prefix='p5-http-test-') as d:
    p=Path(d);(p/'test.c').write_text(prelude+state+parse+handlers+wrapper)
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test.so')],check=True)
    lib=C.CDLL(str(p/'test.so'));lib.queue.argtypes=[C.c_char_p];lib.response.restype=lib.action.restype=C.c_char_p
    def request(body=b'abort',header=b'X-P5-Recovery: 1\r\n',length=None):
        return b'POST /p5_storage HTTP/1.1\r\n'+header+b'Content-Length: '+str(len(body) if length is None else length).encode()+b'\r\n\r\n'+body
    for req in [request(header=b''),request(header=b'X-P5-Recovery: 0\r\n'),request(body=b''),request(body=b'a'*240),request(body=b'abort\n'),request(length=4),request(length=6)]:
        lib.reset();assert lib.queue(req)==0 and b'409' in lib.response()
    for boot,transfer in [(1,0),(0,1)]:
        lib.reset();lib.busy(boot,transfer);assert lib.queue(request())==0
    lib.reset();assert lib.queue(request(header=b'x-p5-recovery: 1\r\n'))==1
    assert lib.action()==b'abort' and b'202' in lib.response()
    assert lib.queue(request(b'restore-finish'))==1 and lib.action()==b'abort' and b'409' in lib.response()
    lib.status();assert b'"busy":true' in lib.response() and b'"sequence":1' in lib.response()
    lib.reset();lib.modem(b'POST /p5_modem HTTP/1.1\r\nX-P5-Recovery: 1\r\n\r\npower-on');assert b'200' in lib.response()
    lib.reset();lib.modem(b'POST /p5_modem HTTP/1.1\r\n\r\npower-on');assert b'409' in lib.response()
print('PASS: actual HTTP handlers require explicit header, exact bounded body, reject concurrent boot/transfer/storage, preserve queued action and expose sequence status')


# The real receive callback must retain fragmented P5 commands per connection.
state_header=(source_path.parent/'failsafe_httpd_types.h').read_text()
state_header='\n'.join(line for line in state_header.splitlines() if not line.startswith('#include'))
boot=source[source.index('static void httpd_handle_p5_boot'):source.index('\n#endif',source.index('static void httpd_handle_p5_boot'))]
receiver=source[source.index('static err_t httpd_recv(void *arg'):source.index('static void httpd_err(')]
reset_state=source[source.index('static void httpd_state_reset'):source.index('static void httpd_conn_abort')]
helper_start=source.find('/* Retain small P5 command requests')
helper='' if helper_start < 0 else source[helper_start:source.index('static err_t httpd_recv(void *arg',helper_start)]
http_macros=source[source.index('#define ISO_space'):source.index('#define WEBFAILSAFE_PROGRESS_START')]
boot_constants='\n'.join(line for line in (source_path.parents[1]/'include/p5_recovery.h').read_text().splitlines() if line.startswith('#define P5_'))
receive_prelude=r"""
static int p5_modem_action(const char *s){(void)s;return 0;}
static int p5_storage_busy(void){return 0;}
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
typedef unsigned long ulong;
typedef uint8_t u8_t;typedef uint16_t u16_t;typedef uint32_t u32_t;typedef uint32_t u32;typedef uint64_t u64_t;
typedef int err_t;
#define CONFIG_IPQ5018_CUDY_P5 1
#define CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES 1
#define ERR_OK 0
#define ERR_ABRT -1
#define TCP_PRIO_MIN 0
#define TCP_PRIO_NORMAL 1
#define WEBFAILSAFE_UPLOAD_RAM_ADDRESS 0x4b000000UL
struct ip4_addr;
struct tcp_pcb{int unused;};
struct pbuf{struct pbuf *next;void *payload;u16_t len,tot_len;};
static ulong simple_strtoull(char *p,char **e,int b){return strtoull(p,e,b);}
static ulong simple_strtoul(char *p,char **e,int b){return strtoul(p,e,b);}
static ulong get_timer(ulong base){return 1000-base;}
static void print_error(const char *p){(void)p;}
static u32 crc32(u32 seed,const unsigned char *p,unsigned n){(void)seed;(void)p;(void)n;return 0;}
static int p5_boot_check(int kind,ulong addr,ulong size){(void)kind;(void)addr;(void)size;return 0;}
static ulong p5_ram_size,p5_boot_size,p5_boot_deadline;
static u32 p5_ram_crc,p5_boot_crc;
static int p5_boot_pending,other_route,aborted,received;
static void tcp_setprio(struct tcp_pcb *p,int n){(void)p;(void)n;}
static void tcp_close(struct tcp_pcb *p){(void)p;}
static void tcp_recved(struct tcp_pcb *p,unsigned n){(void)p;received+=n;}
static void pbuf_free(struct pbuf *p){(void)p;}
static void led_on(const char *p){(void)p;}
static void led_off(const char *p){(void)p;}
static struct{int done,failed,packet_counter,data_start_found;unsigned upload;} upload;
static struct{int unused;} backup;
static void (*flashread_yield_fn)(void);
static char *boundary_value;
static void *webfailsafe_data_pointer;
#define webterm_http_handler(...) ((void)other_route++)
#define httpd_handle_upgrade_status(...) ((void)other_route++)
#define httpd_handle_partitions(...) ((void)other_route++)
#define httpd_handle_backup(...) ((void)other_route++)
#define httpd_handle_about(...) ((void)other_route++)
#define httpd_handle_mac_info(...) ((void)other_route++)
#define httpd_handle_led(...) ((void)other_route++)
#define httpd_handle_btn_detect(...) ((void)other_route++)
#define httpd_handle_file_request(...) ((void)other_route++)
#define httpd_handle_env_set(...) ((void)other_route++)
#define httpd_handle_mac_set(...) ((void)other_route++)
#define httpd_handle_ram_status(...) ((void)other_route++)
#define httpd_parse_boundary(...) (-1)
#define httpd_init_upload_ram(...) (-1)
#define httpd_findandstore_firstchunk(...) 0
#define httpd_check_upload_size(...) 0
#define httpd_check_upload_complete(...) ((void)0)
#define httpd_handle_upload_data(...) ((void)0)
"""
receive_state=r"""
static struct failsafe_httpd_state *hs_global;
static char reply[512];
static void httpd_send_static(struct failsafe_httpd_state *hs,const char *text,int n){
 if(n>511)abort();
 memcpy(reply,text,n);reply[n]=0;hs->state=STATE_FILE_REQUEST;
}
"""
receive_abort=r"""
static err_t httpd_recv_abort(struct failsafe_httpd_state *hs,struct tcp_pcb *pcb,char *data,int need_free,struct pbuf *p){
 (void)pcb;aborted++;httpd_state_reset(hs);if(need_free)free(data);pbuf_free(p);return ERR_ABRT;
}
static void httpd_conn_abort(struct failsafe_httpd_state *hs,struct tcp_pcb *pcb){(void)pcb;aborted++;httpd_state_reset(hs);}
"""
receive_wrapper=r"""
static struct failsafe_httpd_state connections[2];static struct tcp_pcb pcb;
void reset(void){memset(connections,0,sizeof(connections));p5_action_pending=p5_action_sequence=p5_boot_pending=0;hs_global=0;reply[0]=0;other_route=aborted=received=0;}
int feed(unsigned which,const void *text,unsigned n){struct pbuf p={NULL,(void*)text,n,n};return httpd_recv(&connections[which],&pcb,&p,ERR_OK);}
void busy(int boot,int transfer){p5_boot_pending=boot;hs_global=transfer?&connections[1]:NULL;}
void disconnect(unsigned which){httpd_recv(&connections[which],&pcb,NULL,ERR_OK);}
int pending(void){return p5_action_pending;}
int boot_pending(void){return p5_boot_pending;}
int aborts(void){return aborted;}
int others(void){return other_route;}
const char *response(void){return reply;}
"""
with tempfile.TemporaryDirectory(prefix='p5-http-receive-test-') as d:
    p=Path(d);(p/'test.c').write_text(receive_prelude+http_macros+boot_constants+'\n'+state_header+receive_state+state+parse+handlers+boot+reset_state+receive_abort+helper+receiver+receive_wrapper)
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-variable','-Wno-unused-function',str(p/'test.c'),'-o',str(p/'test.so')],check=True)
    lib=C.CDLL(str(p/'test.so'));lib.feed.argtypes=[C.c_uint,C.c_char_p,C.c_uint];lib.response.restype=C.c_char_p
    def feed(data,which=0):return lib.feed(which,data,len(data))
    for route,body,header in [(b'p5_storage',b'restore-finish',b'X-P5-Recovery: 1\r\n'),(b'p5_boot',b'installed',b'')]:
        request=b'POST /'+route+b' HTTP/1.1\r\n'+header+b'Content-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
        pending=lib.pending if route==b'p5_storage' else lib.boot_pending
        for split in range(1,len(request)):
            lib.reset();assert feed(request[:split])==0
            assert not pending() and not lib.response(),(route,split,lib.response())
            assert feed(request[split:])==0 and pending() and b'202' in lib.response(),(route,split,lib.response())
        lib.reset()
        for index,byte in enumerate(request):
            assert feed(bytes([byte]))==0
            if index<len(request)-1:assert not pending() and not lib.response()
        assert pending() and b'202' in lib.response()
        # Closing a partial request clears the per-connection accumulation.
        lib.reset();feed(request[:-2]);lib.disconnect(0)
        assert feed(request)==0 and pending() and b'202' in lib.response()
        # Incomplete or excessive headers/body cannot queue or overflow the buffer.
        for invalid in [b'POST /'+route+b' HTTP/1.1\r\nX-Long: '+b'a'*4096,
                        request.replace(b'Content-Length: '+str(len(body)).encode(),b'Content-Length: 4096'),
                        request.replace(b'Content-Length: '+str(len(body)).encode(),b'Content-Length: '+str(2**32+len(body)).encode()),
                        *[request.replace(b'Content-Length: '+str(len(body)).encode(),b'Content-Length: '+bad) for bad in (b'-1',b'+14',b'14x',b'18446744073709551616')],
                        request+b'x']:
            lib.reset();feed(invalid);assert not pending() and (lib.aborts() or b'40' in lib.response())
        lib.reset();feed(b'POST /'+route+b' HTTP/1.1\r\nX-Long: ')
        for _ in range(40):
            feed(b'a'*64)
            if lib.aborts():break
        assert lib.aborts() and not pending()
    # Independent connections accumulate independently; the existing queue refuses concurrency.
    one=b'POST /p5_storage HTTP/1.1\r\nX-P5-Recovery: 1\r\nContent-Length: 5\r\n\r\nabort'
    cut=one.index(b'\r\n\r\n')+4
    lib.reset();feed(one[:cut],0);feed(one[:cut],1)
    assert not lib.pending();feed(one[cut:],1);assert lib.pending() and b'202' in lib.response()
    feed(one[cut:],0);assert lib.pending() and b'409' in lib.response()
    # Header and transfer/boot concurrency checks still apply after assembly.
    lib.reset();feed(one.replace(b'X-P5-Recovery: 1\r\n',b''));assert not lib.pending() and b'409' in lib.response()
    for boot_busy,transfer_busy in [(1,0),(0,1)]:
        lib.reset();lib.busy(boot_busy,transfer_busy);feed(one[:cut]);feed(one[cut:]);assert not lib.pending() and b'409' in lib.response()
    lib.reset();feed(b'GET /about HTTP/1.1\r\n\r\n');assert lib.others()==1
    lib.reset();feed(b'GE');feed(b'T /about HTTP/1.1\r\n\r\n');assert lib.others()==1
print('PASS: actual receive callback assembles both P5 endpoints at every split and bytewise, isolates connections, clears disconnected requests, rejects oversized headers/bodies and preserves queue/header checks')
