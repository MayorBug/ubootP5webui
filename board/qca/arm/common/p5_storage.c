/* SPDX-License-Identifier: GPL-2.0+ */
/* Checked P5 storage operations, explicitly enabled by recovery defconfigs. */
#include <common.h>
#include <asm/errno.h>
#include <command.h>
#include <nand.h>
#include <watchdog.h>
#include <u-boot/sha256.h>
#include <asm/arch-qca-common/smem.h>
#include <p5_storage.h>
#include <p5_recovery.h>
#include <p5_factory.h>

DECLARE_GLOBAL_DATA_PTR;
#define P5_STORAGE_UNCHANGED 1
#ifdef CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES
#define UPLOAD P5_UPLOAD_BASE
#define READBACK P5_READBACK_BASE
#define STAGE (READBACK + P5_MIBIB_SIZE)
#define VERIFY (STAGE + P5_NAND_BLOCK)
#define BOOTCONFIG_BUF (VERIFY + P5_NAND_BLOCK)
#define MAX_CHUNKS 1024U
struct restore_header {
 u8 magic[8]; u32 version, descriptor_size; char name[32];
 u32 start, length, page_size, erase_size, machid;
 u8 mac[6], reserved[2], old_hash[32], new_hash[32]; u32 chunks;
};
typedef char restore_header_size_must_be_144[(sizeof(struct restore_header) == 144) ? 1 : -1];
static struct {
 int active, dirty, fatal, layout_changed;
 struct restore_header header;
 u8 hashes[MAX_CHUNKS][32];
 u32 next;
 sha256_context stream;
} state;
extern int is_sec_boot_enabled(void);
extern int get_eth_mac_address(uchar *enetaddr, uint no_of_macs);

int p5_storage_busy(void) { return state.active || state.dirty || state.fatal || state.layout_changed || p5_factory_boot_blocked(); }
int p5_storage_boot_blocked(void) { return p5_storage_busy(); }
static int geometry(void)
{
 nand_info_t *m = &nand_info[0];
 return gd->bd->bi_arch_number == P5_MACHID && is_sec_boot_enabled() == 0 &&
        m->size == P5_FLASH_BYTES && m->writesize == P5_PAGE_BYTES && m->erasesize == P5_NAND_BLOCK ? 0 : -EINVAL;
}
static int read_bytes(u32 offset, u32 size, void *out)
{
 size_t length = size;
 int ret = nand_read(&nand_info[0], offset, &length, out);
 return (ret && ret != -EUCLEAN) || length != size ? -EIO : 0;
}
static int good_blocks(u32 start, u32 length)
{
 u32 at;
 if (start % P5_NAND_BLOCK || !length || length % P5_NAND_BLOCK ||
     start >= P5_FLASH_BYTES || length > P5_FLASH_BYTES - start) return -EINVAL;
 for (at = start; at < start + length; at += P5_NAND_BLOCK)
  if (nand_block_isbad(&nand_info[0], at) != 0) return -EIO;
 return 0;
}
static int parse_hash(const char *text, u8 out[32])
{
 unsigned i;
 if (!text || strlen(text) != 64) return -EINVAL;
 for (i = 0; i < 64; i++) if (!strchr("0123456789abcdef", text[i])) return -EINVAL;
 for (i = 0; i < 32; i++) { char byte[3] = {text[i*2], text[i*2+1], 0}; out[i] = simple_strtoul(byte, NULL, 16); }
 return 0;
}
static int hash_region(u32 start, u32 length, u8 hash[32])
{
 sha256_context ctx;
 u32 at;
 sha256_starts(&ctx);
 for (at = 0; at < length; at += P5_NAND_BLOCK) {
  if (read_bytes(start + at, P5_NAND_BLOCK, (void *)VERIFY)) return -EIO;
  sha256_update(&ctx, (void *)VERIFY, P5_NAND_BLOCK);
  WATCHDOG_RESET();
 }
 sha256_finish(&ctx, hash); return 0;
}
static int mibib_read(void)
{
 int selected;
 if (geometry() || good_blocks(P5_MIBIB_OFFSET, P5_MIBIB_SIZE) ||
     read_bytes(P5_MIBIB_OFFSET, P5_MIBIB_SIZE, (void *)READBACK)) return -EIO;
 selected = p5_mibib_select((void *)READBACK, P5_MIBIB_SIZE);
 return selected;
}
/* Validate and verify one eraseblock. Raw UART NAND commands remain independent. */
static int write_block(u32 offset, const void *data)
{
 struct erase_info erase;
 size_t length = P5_NAND_BLOCK;
 int ret;
 if (good_blocks(offset, P5_NAND_BLOCK)) return -EIO;
 memset(&erase, 0, sizeof(erase)); erase.mtd = &nand_info[0]; erase.addr = offset; erase.len = P5_NAND_BLOCK;
 state.dirty = 1;
 ret = mtd_erase(&nand_info[0], &erase);
 if (!ret) { ret = nand_write(&nand_info[0], offset, &length, (u8 *)data); if (length != P5_NAND_BLOCK) ret = -EIO; }
 if (!ret) ret = read_bytes(offset, P5_NAND_BLOCK, (void *)VERIFY);
 if (!ret && memcmp(data, (void *)VERIFY, P5_NAND_BLOCK)) ret = -EIO;
 if (ret) { state.fatal = 1; puts("P5 storage write/readback FAILED. Do not boot installed firmware. Save UART and remain in recovery.\n"); }
 return ret;
}
/* Fixed version-2 BOOTCONFIG record used by the P5 vendor driver and NAND. */
typedef char bootconfig_size_must_be_336[(sizeof(qca_smem_bootconfig_info_t) == 336) ? 1 : -1];
static int bootconfig_root(const u8 *block)
{
 const qca_smem_bootconfig_info_t *b = (const void *)block;
 unsigned i, j;
 int root = -1;
 if (b->magic_start == 0xffffffffU) {
  for (i = 0; i < P5_NAND_BLOCK; i++) if (block[i] != 0xff) return -EINVAL;
  return -ENOENT;
 }
 if (b->magic_start != _SMEM_DUAL_BOOTINFO_MAGIC_START ||
     b->magic_end != _SMEM_DUAL_BOOTINFO_MAGIC_END || !b->numaltpart ||
     b->numaltpart > ARRAY_SIZE(b->per_part_entry)) return -EINVAL;
 for (i = 0; i < b->numaltpart; i++) {
  const struct per_part_info *p = &b->per_part_entry[i];
  if (!p->name[0] || !memchr(p->name, 0, sizeof(p->name)) || p->primaryboot > 1) return -EINVAL;
  for (j = 0; j < i; j++) if (!strcmp(p->name, b->per_part_entry[j].name)) return -EINVAL;
  if (!strcmp(p->name, "rootfs")) root = i;
 }
 return root < 0 ? -EINVAL : root;
}
static int bootconfig_primary(void)
{
 const u32 offsets[2] = {P5_BOOTCONFIG_OFFSET, P5_BOOTCONFIG1_OFFSET};
 u8 *blocks[2] = {(void *)BOOTCONFIG_BUF, (void *)(BOOTCONFIG_BUF + P5_NAND_BLOCK)};
 qca_smem_bootconfig_info_t *b[2], *stage = (void *)STAGE;
 int roots[2], selected, target, copy, ret;
 u32 age;
 for (copy = 0; copy < 2; copy++) {
  if (good_blocks(offsets[copy], P5_NAND_BLOCK) ||
      read_bytes(offsets[copy], P5_NAND_BLOCK, blocks[copy])) return -EIO;
  b[copy] = (void *)blocks[copy]; roots[copy] = bootconfig_root(blocks[copy]);
  if (roots[copy] < 0 && roots[copy] != -ENOENT) return -EINVAL;
 }
 if (roots[0] < 0 && roots[1] < 0) return -EINVAL;
 if (roots[0] >= 0 && roots[1] >= 0 && b[0]->age == b[1]->age &&
     memcmp(b[0], b[1], sizeof(*stage))) return -EINVAL;
 selected = roots[0] < 0 || (roots[1] >= 0 && b[1]->age > b[0]->age) ? 1 : 0;
 if (roots[0] >= 0 && roots[1] >= 0 &&
     !b[0]->per_part_entry[roots[0]].primaryboot &&
     !b[1]->per_part_entry[roots[1]].primaryboot) return 0;
 age = b[selected]->age; if (age > 0xfffffffcU) return -EOVERFLOW;
 target = 1 - selected;
 for (copy = 0; copy < 2; copy++) {
  /* Preserve each copy's opaque eraseblock bytes and the newest record's other selectors. */
  memcpy((void *)STAGE, blocks[roots[target] < 0 ? selected : target], P5_NAND_BLOCK);
  memcpy(stage, b[selected], sizeof(*stage));
  stage->age = ++age; stage->per_part_entry[roots[selected]].primaryboot = 0;
  state.layout_changed = 1;
  ret = write_block(offsets[target], (void *)STAGE); if (ret) return ret;
  if (bootconfig_root((void *)VERIFY) != roots[selected]) { state.fatal = 1; return -EIO; }
  memcpy(blocks[target], (void *)VERIFY, P5_NAND_BLOCK);
  roots[target] = roots[selected]; selected = target; target = 1 - target;
 }
 puts("P5 BOOTCONFIG copies select primary rootfs; readback matches. Restart before booting installed firmware.\n");
 return 0;
}
/* Conversion updates boot selection and table geometry. Firmware bytes are untouched. */
static int convert_layout(int single, const char *old_hash)
{
 int selected, target, ret, copy, matches;
 u8 expected[32], actual[32];
 u8 *source;
 u32 age;
 if (state.active || (state.dirty && !state.layout_changed) || p5_install_boot_blocked() || parse_hash(old_hash, expected)) return -EBUSY;
 selected = mibib_read(); if (selected < 0) return selected;
 sha256_csum_wd((void *)READBACK, P5_MIBIB_SIZE, actual, 4096);
 if (memcmp(expected, actual, 32)) return -EINVAL;
 source = (void *)(READBACK + selected * P5_NAND_BLOCK);
 ret = p5_mibib_validate(source, P5_NAND_BLOCK); if (ret < 0) return ret;
 matches = ret == single &&
     p5_mibib_validate((void *)(READBACK + (1-selected) * P5_NAND_BLOCK), P5_NAND_BLOCK) == single;
 age = ((u32 *)source)[3];
 if (!matches && age > 0xfffffffcU) return -EOVERFLOW;
 if (!matches && p5_mibib_make((void *)STAGE, source, P5_NAND_BLOCK, single, age + 1)) return -EINVAL;
 if (single) { ret = bootconfig_primary(); if (ret) return ret; }
 if (matches) {
  state.fatal = 0;
  puts("P5 layout already matches in both MIBIB copies; no MIBIB writes\n");
  return state.layout_changed ? 0 : P5_STORAGE_UNCHANGED;
 }
 /* Replace the older/invalid copy first; never erase both copies together. */
 target = 1 - selected;
 for (copy = 0; copy < 2; copy++) {
  ret = p5_mibib_make((void *)STAGE, source, P5_NAND_BLOCK, single, ++age); if (ret) return ret;
  state.layout_changed = 1;
  ret = write_block(P5_MIBIB_OFFSET + target * P5_NAND_BLOCK, (void *)STAGE); if (ret) return ret;
  if (p5_mibib_validate((void *)VERIFY, P5_NAND_BLOCK) != single) { state.fatal = 1; return -EIO; }
  memcpy((void *)(READBACK + target * P5_NAND_BLOCK), (void *)VERIFY, P5_NAND_BLOCK);
  source = (void *)(READBACK + target * P5_NAND_BLOCK); target = 1 - target;
 }
 state.fatal = 0;
 puts("P5 MIBIB copies and checksums match the new layout. Firmware bytes are unchanged.\nRestart before installing firmware for the new layout. SMEM remains stale until restart.\n");
 return 0;
}
static int restore_begin(ulong size, u32 crc, const char *token)
{
 const struct restore_header *h = (const void *)UPLOAD;
 const struct p5_part *parts;
 int selected, found = 0, i, count;
 u8 mac[12], digest[32];
 if (state.active || state.layout_changed || p5_install_boot_blocked() || size < sizeof(*h) || size > sizeof(*h) + MAX_CHUNKS * 32U ||
     crc32(0, (void *)UPLOAD, size) != crc || memcmp(h->magic, "P5RST01", 8) ||
     h->version != 1 || h->descriptor_size != size || !memchr(h->name, 0, sizeof(h->name)) ||
     !h->chunks || h->chunks > MAX_CHUNKS || size != sizeof(*h) + h->chunks * 32U ||
     h->page_size != P5_PAGE_BYTES || h->erase_size != P5_NAND_BLOCK ||
     h->machid != P5_MACHID || h->reserved[0] || h->reserved[1] ||
     h->length != h->chunks * P5_NAND_BLOCK) return -EINVAL;
 if (state.fatal && (h->start != state.header.start || h->length != state.header.length ||
     strcmp(h->name, state.header.name))) return -EBUSY;
 if (get_eth_mac_address(mac, 2) < 0 || memcmp(mac, h->mac, 6)) return -EINVAL;
 selected = mibib_read(); if (selected < 0) return selected;
 parts = (void *)(READBACK + selected * P5_NAND_BLOCK + 2048 + 16);
 count = ((u32 *)(READBACK + selected * P5_NAND_BLOCK + 2048))[3];
 for (i = 0; i < count; i++) {
  int firmware_alias = (!strcmp(h->name, "rootfs") || !strcmp(h->name, "rootfs_1")) &&
      (!strcmp(parts[i].name, "rootfs") || !strcmp(parts[i].name, "rootfs_1"));
  if ((!strcmp(h->name, parts[i].name) || firmware_alias) &&
      h->start == parts[i].start * P5_NAND_BLOCK && h->length == parts[i].blocks * P5_NAND_BLOCK) found = 1;
 }
 if (!found || !strcmp(h->name, "0:MIBIB")) return -EINVAL;
 if (!strcmp(h->name, "rootfs") || !strcmp(h->name, "rootfs_1")) {
  if (strcmp(token, "RESTORE-P5-PARTITION")) return -EINVAL;
 } else if (!strcmp(h->name, "0:APPSBL") || !strcmp(h->name, "0:APPSBL_1")) {
  if (strcmp(token, "RESTORE-P5-FULL")) return -EINVAL;
 } else if (strcmp(token, "RESTORE-P5-PROTECTED")) return -EINVAL;
 if (good_blocks(h->start, h->length) || hash_region(h->start, h->length, digest)) return -EIO;
 if (memcmp(digest, h->old_hash, 32)) return -EINVAL;
 memcpy(&state.header, h, sizeof(*h)); memcpy(state.hashes, h + 1, h->chunks * 32U);
 state.next = 0; state.active = 1; state.fatal = 0; sha256_starts(&state.stream);
 puts("P5 restore session ready; no writes yet. Upload chunk 0.\n"); return 0;
}
static int restore_chunk(u32 index, ulong size, u32 crc)
{
 u8 digest[32]; int ret;
 if (!state.active || index != state.next || index >= state.header.chunks ||
     size != P5_NAND_BLOCK || crc32(0, (void *)UPLOAD, size) != crc) return -EINVAL;
 sha256_csum_wd((void *)UPLOAD, size, digest, 4096);
 if (memcmp(digest, state.hashes[index], 32)) return -EINVAL;
 ret = write_block(state.header.start + index * P5_NAND_BLOCK, (void *)UPLOAD); if (ret) return ret;
 sha256_update(&state.stream, (void *)VERIFY, size); state.next++;
 printf("P5 restore checked chunk %u/%u\n", state.next, state.header.chunks); return 0;
}
static int restore_finish(void)
{
 u8 streamed[32], readback[32]; sha256_context ctx;
 if (!state.active || state.next != state.header.chunks) return -EINVAL;
 ctx = state.stream; sha256_finish(&ctx, streamed);
 if (memcmp(streamed, state.header.new_hash, 32) ||
     hash_region(state.header.start, state.header.length, readback) ||
     memcmp(readback, state.header.new_hash, 32)) {
  state.fatal = 1; puts("P5 whole-partition check FAILED. Do not reboot.\n"); return -EIO;
 }
 state.active = state.dirty = state.fatal = 0;
 puts("P5 partition restore complete; whole-partition readback SHA256 matches. No reboot requested.\n"); return 0;
}
enum p5_operation { P5_OP_LAYOUT, P5_OP_BEGIN, P5_OP_CHUNK, P5_OP_FINISH, P5_OP_ABORT, P5_OP_INSTALL, P5_OP_FACTORY };
struct p5_request {
 enum p5_operation operation;
 ulong bytes;
 u32 crc, index;
 int single;
 const char *token, *old_hash;
};
static int parse_number(const char *text, unsigned base, ulong limit, ulong *out)
{
 ulong value = 0; unsigned digit;
 if (!text || !*text || (base != 10 && base != 16)) return -EINVAL;
 if (base == 16 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) text += 2;
 if (!*text) return -EINVAL;
 while (*text) {
  digit = *text >= '0' && *text <= '9' ? *text-'0' :
          *text >= 'a' && *text <= 'f' ? *text-'a'+10 :
          *text >= 'A' && *text <= 'F' ? *text-'A'+10 : 16;
  if (digit >= base || digit > limit || value > (limit-digit)/base) return -EINVAL;
  value = value*base+digit; text++;
 }
 *out = value; return 0;
}
static int parse_request(unsigned n, char * const v[], unsigned base, struct p5_request *r)
{
 unsigned i; ulong value;
 memset(r, 0, sizeof(*r));
 if (!n || n > 4) return -EINVAL;
 for (i = 0; i < n; i++) if (!v[i] || !*v[i]) return -EINVAL;
 if (n == 4 && !strcmp(v[0], "layout")) {
  if (strcmp(v[1], "single") && strcmp(v[1], "dual")) return -EINVAL;
  r->operation = P5_OP_LAYOUT; r->single = !strcmp(v[1], "single"); r->old_hash = v[2]; r->token = v[3]; return 0;
 }
 if (n == 1 && !strcmp(v[0], "restore-finish")) { r->operation = P5_OP_FINISH; return 0; }
 if (n == 1 && !strcmp(v[0], "abort")) { r->operation = P5_OP_ABORT; return 0; }
 if (n != 4) return -EINVAL;
 if (!strcmp(v[0], "restore-chunk")) {
  r->operation = P5_OP_CHUNK;
  if (parse_number(v[1], base, MAX_CHUNKS-1, &value)) return -EINVAL;
  r->index = value; i = 2;
 } else {
  if (!strcmp(v[0], "restore-begin")) r->operation = P5_OP_BEGIN;
  else if (!strcmp(v[0], "install")) r->operation = P5_OP_INSTALL;
  else if (!strcmp(v[0], "factory")) r->operation = P5_OP_FACTORY;
  else return -EINVAL;
  r->token = v[3]; i = 1;
 }
 if (parse_number(v[i], base, P5_UPLOAD_LIMIT, &r->bytes) || !r->bytes ||
     parse_number(v[i+1], 16, 0xffffffffUL, &value)) return -EINVAL;
 r->crc = value; return 0;
}
static int apply_request(const struct p5_request *r)
{
 switch (r->operation) {
 case P5_OP_LAYOUT:
  if (strcmp(r->token, "CONVERT-P5-LAYOUT")) return -EINVAL;
  return convert_layout(r->single, r->old_hash);
 case P5_OP_BEGIN: return restore_begin(r->bytes, r->crc, r->token);
 case P5_OP_CHUNK: return restore_chunk(r->index, r->bytes, r->crc);
 case P5_OP_FINISH: return restore_finish();
 case P5_OP_ABORT:
  state.active = 0; if (state.dirty) state.fatal = 1; return 0;
 case P5_OP_FACTORY:
  if (strcmp(r->token, "FLASH-P5-FACTORY") || p5_storage_busy() || p5_install_boot_blocked()) return -EINVAL;
  return p5_factory_run(UPLOAD, r->bytes);
 case P5_OP_INSTALL:
  if (strcmp(r->token, "INSTALL-P5-APPSBL")) return -EINVAL;
  return p5_install_run(UPLOAD, r->bytes);
 }
 return -EINVAL;
}

#else
int p5_storage_busy(void) { return 0; }
int p5_storage_boot_blocked(void) { return 0; }
#endif

/* Web and UART adapters share parsing policy and typed operation dispatch. */
int p5_storage_execute(const char *action, ulong uploaded, u32 receipt_crc)
{
#ifdef CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES
 char text[240], *v[6], *p;
 unsigned n = 0;
 struct p5_request request;
 if (!action || !*action || strlen(action) >= sizeof(text)) return -EINVAL;
 strcpy(text, action); p = text;
 do {
  if (n == ARRAY_SIZE(v)) return -EINVAL;
  v[n++] = p; p = strchr(p, ','); if (p) *p++ = 0;
 } while (p);
 if (parse_request(n, v, 10, &request)) return -EINVAL;
 if (request.operation == P5_OP_BEGIN || request.operation == P5_OP_CHUNK || request.operation == P5_OP_INSTALL || request.operation == P5_OP_FACTORY) {
  if (request.bytes != uploaded || request.crc != receipt_crc ||
      crc32(0, (void *)UPLOAD, request.bytes) != request.crc) return -EINVAL;
 }
 return apply_request(&request);
#else
 return -EPERM;
#endif
}
static int do_p5storage(cmd_tbl_t *cmdtp, int flag, int argc, char * const argv[])
{
#ifdef CONFIG_P5_EXPERIMENTAL_STORAGE_WRITES
 struct p5_request request; int ret;
 if (argc == 2 && !strcmp(argv[1], "status")) {
  printf("P5 storage: enabled; active=%d next=%u dirty=%d fatal=%d layout_changed=%d\n",
    state.active, state.next, state.dirty, state.fatal, state.layout_changed); return 0;
 }
 if (parse_request(argc-1, argv+1, 16, &request)) return CMD_RET_USAGE;
 ret = apply_request(&request);
 if (ret == P5_STORAGE_UNCHANGED) return 0;
 if (ret) printf("P5 storage refused/failed: %d. No automatic reboot. Check status and save UART.\n", ret);
 return ret ? CMD_RET_FAILURE : 0;
#else
 puts("P5 conversion/restore writes are DISABLED at build time.\n");
 return argc == 2 && !strcmp(argv[1], "status") ? 0 : CMD_RET_FAILURE;
#endif
}
U_BOOT_CMD(p5storage, 5, 0, do_p5storage, "checked P5 conversion and partition restore",
 "status\np5storage layout single|dual <old-MIBIB-sha256> CONVERT-P5-LAYOUT\n"
 "p5storage restore-begin <descriptor-hex-bytes> <crc32> RESTORE-P5-PARTITION|RESTORE-P5-PROTECTED|RESTORE-P5-FULL\n"
 "p5storage factory <hex-bytes> <crc32> FLASH-P5-FACTORY\n"
 "p5storage restore-chunk <hex-index> <hex-bytes> <crc32>\np5storage restore-finish\np5storage abort");
