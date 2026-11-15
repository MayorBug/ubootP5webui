/* SPDX-License-Identifier: GPL-2.0+ */
/* P5 factory UBI only: validated image, live firmware region, full readback. */
#include <common.h>
#include <asm/errno.h>
#include <nand.h>
#include <watchdog.h>
#include <libfdt.h>
#include <u-boot/sha1.h>
#include <u-boot/sha256.h>
#include <asm/arch-qca-common/smem.h>
#include <p5_storage.h>
#include <p5_factory.h>

DECLARE_GLOBAL_DATA_PTR;
#define PEB P5_NAND_BLOCK
#define VID P5_PAGE_BYTES
#define DATA (2U * P5_PAGE_BYTES)
#define LEB (PEB - DATA)
#define LAYOUT_ID 0x7fffefffU
#define TABLE_RECORD 172U
#define TABLE_BYTES (128U * TABLE_RECORD)
#define SCRATCH P5_READBACK_BASE
static int factory_dirty;
extern int is_sec_boot_enabled(void);
int p5_factory_boot_blocked(void) { return factory_dirty; }
static u32 be32(const void *p) { const u8 *b = p; return (u32)b[0]<<24 | (u32)b[1]<<16 | (u32)b[2]<<8 | b[3]; }
static u32 le32(const void *p) { const u8 *b = p; return (u32)b[3]<<24 | (u32)b[2]<<16 | (u32)b[1]<<8 | b[0]; }
static int bytes_equal(const u8 *p, unsigned n, u8 value) { while (n--) if (*p++ != value) return 0; return 1; }
static int read_bytes(u32 offset, u32 size, void *out)
{
 size_t length = size;
 int ret = nand_read(&nand_info[0], offset, &length, out);
 return (ret && ret != -EUCLEAN) || length != size ? -EIO : 0;
}
static int crc_ok(const u8 *p, unsigned size)
{
 return (crc32(0, p, size - 4) ^ 0xffffffffU) == be32(p + size - 4);
}
static int firmware_region(u32 *start, u32 *length, int *single)
{
 nand_info_t *m = &nand_info[0];
 int selected, active = 0;
 if (gd->bd->bi_arch_number != P5_MACHID || is_sec_boot_enabled() != 0 ||
     m->size != P5_FLASH_BYTES || m->writesize != P5_PAGE_BYTES || m->erasesize != PEB ||
     read_bytes(P5_MIBIB_OFFSET, P5_MIBIB_SIZE, (void *)SCRATCH)) return -EINVAL;
 selected = p5_mibib_select((void *)SCRATCH, P5_MIBIB_SIZE);
 if (selected < 0) return -EINVAL;
 *single = p5_mibib_validate((void *)(SCRATCH + selected * PEB), PEB);
 if (*single < 0) return -EINVAL;
 if (!*single) {
  if (smem_bootconfig_info() || qca_smem_bootconfig_info.magic_end != _SMEM_DUAL_BOOTINFO_MAGIC_END ||
      qca_smem_bootconfig_info.numaltpart > ARRAY_SIZE(qca_smem_bootconfig_info.per_part_entry)) return -EINVAL;
  active = get_rootfs_active_partition();
  if (active != 0 && active != 1) return -EINVAL;
 }
 *start = active ? 0x2bc0000U : 0x7c0000U;
 *length = *single ? 0x4800000U : 0x2400000U;
 return 0;
}
/* Bound the container before calling libfdt, including its string/structure ranges. */
static int tree_ok(const void *fdt, u32 bytes)
{
 const u8 *b = fdt, *structure, *strings;
 u32 total, offset, size, names, at = 0, depth = 0, roots = 0;
 if (bytes < 40 || be32(b) != FDT_MAGIC || (total = be32(b + 4)) > bytes || total < 40) return 0;
 offset = be32(b + 8); size = be32(b + 36);
 if (offset > total || size > total - offset || offset % 4) return 0;
 structure = b + offset;
 offset = be32(b + 12); names = be32(b + 32);
 if (offset > total || names > total - offset || fdt_check_header(fdt)) return 0;
 strings = b + offset;
 /* Older libfdt does not bound every property-name string reference. */
 while (at <= size && size - at >= 4) {
  u32 token = be32(structure + at), length, name;
  const u8 *end;
  at += 4;
  if (token == FDT_BEGIN_NODE) {
   end = memchr(structure + at, 0, size - at);
   if (!end || (depth == 0 && (roots++ || end != structure + at)) || ++depth > 256) return 0;
   at = (end - structure + 4) & ~3U;
  } else if (token == FDT_END_NODE) {
   if (!depth) return 0;
   depth--;
  } else if (token == FDT_PROP) {
   if (!depth || size - at < 8) return 0;
   length = be32(structure + at); name = be32(structure + at + 4); at += 8;
   if (length > size - at || name >= names || !memchr(strings + name, 0, names - name)) return 0;
   at += (length + 3) & ~3U;
  } else if (token == FDT_END) return roots == 1 && depth == 0;
  else if (token != FDT_NOP) return 0;
 }
 return 0;
}

static int string_property(const void *fdt, int node, const char *key, const char *expected)
{
 int length;
 const char *value = fdt_getprop(fdt, node, key, &length);
 return value && length > 0 && (unsigned)length == strlen(expected) + 1 && !memcmp(value, expected, length);
}
static int fit_component(const void *fit, int config, const char *property, const void **data, u32 *bytes)
{
 int length, images, node, child, hashed = 0;
 const char *name = fdt_getprop(fit, config, property, &length);
 if (!name || length < 2 || !memchr(name, 0, length) || strlen(name) + 1 != (unsigned)length) return -EINVAL;
 images = fdt_path_offset(fit, "/images");
 if (images < 0 || (node = fdt_subnode_offset(fit, images, name)) < 0) return -EINVAL;
 *data = fdt_getprop(fit, node, "data", &length);
 if (!*data || length <= 0) return -EINVAL;
 *bytes = length;
 for (child = fdt_first_subnode(fit, node); child >= 0; child = fdt_next_subnode(fit, child)) {
  const u8 *expected = fdt_getprop(fit, child, "value", &length);
  u8 digest[32];
  if (!expected || fdt_getprop(fit, child, "ignore", NULL)) return -EINVAL;
  if (string_property(fit, child, "algo", "crc32")) {
   if (length != 4 || be32(expected) != crc32(0, *data, *bytes)) return -EINVAL;
  } else if (string_property(fit, child, "algo", "sha1")) {
   sha1_csum_wd(*data, *bytes, digest, 4096);
   if (length != 20 || memcmp(expected, digest, 20)) return -EINVAL;
  } else if (string_property(fit, child, "algo", "sha256")) {
   sha256_csum_wd(*data, *bytes, digest, 4096);
   if (length != 32 || memcmp(expected, digest, 32)) return -EINVAL;
  } else return -EINVAL;
  hashed++;
 }
 if (child != -FDT_ERR_NOTFOUND || !hashed) return -EINVAL;
 return node;
}
static int fit_check(u32 capacity, int single)
{
 const void *fit = (void *)SCRATCH, *kernel, *dtb;
 u32 kernel_size, dtb_size;
 int conf, node, depth = 0, primary = 0, secondary = 0;
 if (!tree_ok(fit, capacity) || (conf = fdt_path_offset(fit, "/configurations/config@mp03.3")) < 0) return -EINVAL;
 node = fit_component(fit, conf, "kernel", &kernel, &kernel_size);
 if (node < 0 || !string_property(fit, node, "type", "kernel") ||
     !string_property(fit, node, "arch", "arm64") || !string_property(fit, node, "os", "linux")) return -EINVAL;
 node = fit_component(fit, conf, "fdt", &dtb, &dtb_size);
 if (node < 0 || !string_property(fit, node, "type", "flat_dt") ||
     !string_property(fit, node, "compression", "none") || dtb_size > PEB) return -EINVAL;
 /* FIT byte arrays need only four-byte alignment; libfdt may require eight. */
 memmove((void *)(SCRATCH + P5_UPLOAD_LIMIT - PEB), dtb, dtb_size);
 dtb = (void *)(SCRATCH + P5_UPLOAD_LIMIT - PEB);
 if (!tree_ok(dtb, dtb_size) || fdt_node_check_compatible(dtb, 0, "cudy,p5")) return -EINVAL;
 node = -1;
 while ((node = fdt_next_node(dtb, node, &depth)) >= 0) {
  int length;
  const u8 *reg = fdt_getprop(dtb, node, "reg", &length);
  if (string_property(dtb, node, "label", "rootfs")) {
   if (!reg || length != 8 || be32(reg) != 0x7c0000 ||
       be32(reg + 4) != (single ? 0x4800000 : 0x2400000)) return -EINVAL;
   primary++;
  }
  if (string_property(dtb, node, "label", "rootfs_1")) {
   if (!reg || length != 8 || be32(reg) != 0x2bc0000 || be32(reg + 4) != 0x2400000) return -EINVAL;
   secondary++;
  }
 }
 return node == -FDT_ERR_NOTFOUND && primary == 1 && secondary == !single ? 0 : -EINVAL;
}
static int image_check(const u8 *image, u32 size, u32 partition_size, int single)
{
 u32 at, sequence, reserved[3], count[3] = {0}, total = 2, i;
 const u8 *table;
 if (!size || size % PEB || size > partition_size || size > P5_UPLOAD_LIMIT || size < 4 * PEB) return -EINVAL;
 table = image + DATA; sequence = be32(image + 24);
 for (i = 0; i < 128; i++) {
  const u8 *r = table + i * TABLE_RECORD;
  if (!crc_ok(r, TABLE_RECORD)) return -EINVAL;
  if (i >= 3) { if (!bytes_equal(r, TABLE_RECORD - 4, 0)) return -EINVAL; continue; }
  reserved[i] = be32(r);
  if (!reserved[i] || reserved[i] > partition_size / PEB || be32(r + 4) != 1 || be32(r + 8) ||
      r[12] != 1 || r[13] || r[14] || r[15] != (i == 2 ? 11 : 6) ||
      memcmp(r + 16, i == 0 ? "kernel" : i == 1 ? "rootfs" : "rootfs_data", r[15]) ||
      r[16 + r[15]] || r[144] != (i == 2 ? 1 : 0)) return -EINVAL;
  total += reserved[i];
 }
 if (total > partition_size / PEB) return -EINVAL;
 for (at = 0; at < size; at += PEB) {
  const u8 *b = image + at, *v = b + VID, *data = b + DATA;
  u32 id = be32(v + 8), lnum = be32(v + 12);
  if (be32(b) != 0x55424923 || b[4] != 1 || !crc_ok(b, 64) ||
      be32(b + 16) != VID || be32(b + 20) != DATA || be32(b + 24) != sequence ||
      be32(v) != 0x55424921 || v[4] != 1 || v[5] != 1 || v[6] || !crc_ok(v, 64) ||
      be32(v + 20) || be32(v + 24) || be32(v + 28) || be32(v + 32)) return -EINVAL;
  if (at < 2 * PEB) {
   if (id != LAYOUT_ID || lnum != at / PEB || v[7] != 5 || memcmp(table, data, TABLE_BYTES)) return -EINVAL;
   continue;
  }
  if (id > 2 || v[7] || lnum != count[id] || count[id] >= reserved[id]) return -EINVAL;
  if (id == 0) {
   if (lnum >= (P5_UPLOAD_LIMIT - PEB) / LEB) return -EINVAL;
   memcpy((void *)(SCRATCH + lnum * LEB), data, LEB);
  } else if (id == 1 && !lnum) {
   if (memcmp(data, "hsqs", 4) || le32(data + 40) < 96 || le32(data + 44) ||
       le32(data + 40) > reserved[1] * LEB) return -EINVAL;
  }
  count[id]++; WATCHDOG_RESET();
 }
 if (count[0] != reserved[0] || count[1] != reserved[1] || count[2]) return -EINVAL;
 return fit_check(count[0] * LEB, single);
}
static int factory_preflight(ulong address, ulong size, u32 *start, u32 *length)
{
 u32 at; int single;
 if (address != P5_UPLOAD_BASE || size > P5_UPLOAD_LIMIT || firmware_region(start, length, &single)) return -EINVAL;
 for (at = *start; at < *start + *length; at += PEB)
  if (nand_block_isbad(&nand_info[0], at) != 0) return -EIO;
 return image_check((const void *)address, size, *length, single);
}
int p5_factory_check(ulong address, ulong size)
{
 u32 start, length;
 return factory_preflight(address, size, &start, &length);
}
int p5_factory_run(ulong address, ulong size)
{
 u32 start, length, at;
 if (factory_preflight(address, size, &start, &length)) return -EINVAL;
 printf("P5 factory UBI: firmware region 0x%x-0x%x; erasing only this region; do not remove power\n", start, start + length);
 for (at = 0; at < length; at += PEB) {
  struct erase_info erase;
  size_t bytes = PEB;
  int ret;
  memset(&erase, 0, sizeof(erase)); erase.mtd = &nand_info[0]; erase.addr = start + at; erase.len = PEB;
  factory_dirty = 1;
  ret = mtd_erase(&nand_info[0], &erase);
  if (!ret && at < size) ret = nand_write(&nand_info[0], start + at, &bytes, (u8 *)(address + at));
  if (!ret && bytes != PEB) ret = -EIO;
  if (!ret) ret = read_bytes(start + at, PEB, (void *)SCRATCH);
  if (!ret && (at < size ? memcmp((void *)SCRATCH, (void *)(address + at), PEB) :
      !bytes_equal((void *)SCRATCH, PEB, 0xff))) ret = -EIO;
  if (ret) { puts("P5 factory UBI write/readback failed. Stay in recovery; do not boot installed firmware.\n"); return -EIO; }
  WATCHDOG_RESET();
 }
 factory_dirty = 0;
 puts("P5 factory UBI readback matches; firmware tail is erased. No reboot requested.\n");
 return 0;
}
