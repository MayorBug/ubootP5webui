/* SPDX-License-Identifier: GPL-2.0+ */
/* Vendor partition_tool: calculate_parti_crc / crc_32_calc. */
#include <common.h>
#include <asm/errno.h>
#include <p5_storage.h>

static const struct p5_part protected[] = {
 {"0:SBL1",0,4,0xffff}, {"0:MIBIB",4,4,0xffff},
 {"0:BOOTCONFIG",8,2,0xffff}, {"0:BOOTCONFIG1",10,2,0xffff},
 {"0:QSEE",12,8,0xffff}, {"0:DEVCFG",20,2,0xffff},
 {"0:CDT",22,2,0xffff}, {"0:APPSBLENV",24,4,0xffff},
 {"0:APPSBL",28,10,0xffff}, {"0:APPSBL_1",38,10,0xffff},
 {"0:ART",48,8,0xffff}, {"0:TRAINING",56,4,0xffff},
 {"bdinfo",60,2,0xffff}
};
static const u32 user_sizes[13][2] = {
 {0x100,0x100},{0x100,0x100},{0x80,0x80},{0x80,0x80},
 {0x200,0x200},{0x80,0x80},{0x80,0x80},{0x100,0x100},
 {0x400,0x100},{0x400,0x100},{0x200,0x200},{0x100,0x100},{0x80,0x80}
};

u32 p5_mibib_crc(const u8 *bytes, u32 length)
{
 u32 crc = 0, i;
 while (length--) {
  crc ^= (u32)*bytes++ << 24;
  for (i = 0; i < 8; i++) crc = (crc << 1) ^ ((crc & 0x80000000) ? 0x04c11db7 : 0);
 }
 return crc;
}
static int filled(const u8 *p, u32 n, u8 byte)
{
 while (n--) if (*p++ != byte) return 0;
 return 1;
}
static int part_equal(const struct p5_part *p, const char *name, u32 a, u32 b, u32 attr)
{
 char expected[16] = {0};
 if (strlen(name) >= sizeof(expected)) return 0;
 memcpy(expected, name, strlen(name));
 return !memcmp(p->name, expected, 16) && p->start == a && p->blocks == b && p->attr == attr;
}
int p5_mibib_validate(const u8 *block, u32 size)
{
 const u32 *h = (const void *)block, *s, *u, *c;
 const struct p5_part *sp, *up;
 unsigned n, i;
 if (!block || size != P5_NAND_BLOCK || ((ulong)block & 3)) return -EINVAL;
 s = (const void *)(block + 2048); u = (const void *)(block + 4096); c = (const void *)(block + 6144);
 if (h[0] != 0xfe569fac || h[1] != 0xcd7f127a || h[2] != 4 ||
     s[0] != 0x55ee73aa || s[1] != 0xe35ebddb || s[2] != 4 ||
     u[0] != 0xaa7d1b9a || u[1] != 0x1f7d48bc || u[2] != 4 ||
     (s[3] != 14 && s[3] != 15) || s[3] != u[3] ||
     c[0] != 0x9d41bea1 || c[1] != 0xf1ded2ea || c[2] != 1 ||
     c[3] != p5_mibib_crc(block, 6144)) return -EINVAL;
 n = s[3]; sp = (const void *)(s + 4); up = (const void *)(u + 4);
 for (i = 0; i < 13; i++)
  if (memcmp(&sp[i], &protected[i], sizeof(*sp)) ||
      !part_equal(&up[i], protected[i].name, user_sizes[i][0], user_sizes[i][1], 0xff00ffff)) return -EINVAL;
 if (!part_equal(&sp[13], "rootfs", 62, n == 14 ? 576 : 288, 0xffff) ||
     !part_equal(&up[13], "rootfs", n == 14 ? 0x11f00 : 0x8f00, 0x100, 0xff00ffff)) return -EINVAL;
 if (n == 15 && (!part_equal(&sp[14], "rootfs_1", 350, 288, 0xffff) ||
                 !part_equal(&up[14], "rootfs_1", 0x8f00, 0x100, 0xff00ffff))) return -EINVAL;
 if (!filled(block + 16, 2048 - 16, 0xff) ||
     !filled((const u8 *)(sp + n), 0x390 - 16 - n * 28, 0) ||
     !filled((const u8 *)(up + n), 0x390 - 16 - n * 28, 0) ||
     !filled(block + 2048 + 0x390, 2048 - 0x390, 0xff) ||
     !filled(block + 4096 + 0x390, 2048 - 0x390, 0xff) ||
     !filled(block + 6144 + 16, size - 6144 - 16, 0xff)) return -EINVAL;
 return n == 14 ? 1 : 0;
}
int p5_mibib_make(u8 *out, const u8 *in, u32 size, int single, u32 age)
{
 struct p5_part *sp, *up;
 u32 *s, *u;
 if (!out || ((ulong)out & 3) || p5_mibib_validate(in, size) < 0 ||
     (single != 0 && single != 1)) return -EINVAL;
 memmove(out, in, size);
 ((u32 *)out)[3] = age;
 s = (void *)(out + 2048); u = (void *)(out + 4096);
 sp = (void *)(s + 4); up = (void *)(u + 4);
 s[3] = u[3] = single ? 14 : 15;
 memset(&sp[13], 0, 19 * sizeof(*sp)); memset(&up[13], 0, 19 * sizeof(*up));
 memcpy(sp[13].name, "rootfs", 6); sp[13].start = 62; sp[13].blocks = single ? 576 : 288; sp[13].attr = 0xffff;
 memcpy(up[13].name, "rootfs", 6); up[13].start = single ? 0x11f00 : 0x8f00; up[13].blocks = 0x100; up[13].attr = 0xff00ffff;
 if (!single) {
  memcpy(sp[14].name, "rootfs_1", 8); sp[14].start = 350; sp[14].blocks = 288; sp[14].attr = 0xffff;
  memcpy(up[14].name, "rootfs_1", 8); up[14].start = 0x8f00; up[14].blocks = 0x100; up[14].attr = 0xff00ffff;
 }
 ((u32 *)(out + 6144))[3] = p5_mibib_crc(out, 6144);
 return p5_mibib_validate(out, size) == single ? 0 : -EINVAL;
}
/* A checked but unsupported map is not a torn copy. Never overwrite it. */
static int unsupported_copy(const u8 *block)
{
 const u32 *h = (const void *)block, *c = (const void *)(block + 6144);
 if (h[0] == 0xfe569fac && h[1] == 0xcd7f127a && h[2] != 4) return 1;
 return c[0] == 0x9d41bea1 && c[1] == 0xf1ded2ea &&
        c[3] == p5_mibib_crc(block, 6144);
}
int p5_mibib_select(const u8 *mibib, u32 size)
{
 int a, b;
 u32 age_a, age_b;
 if (!mibib || size != P5_MIBIB_SIZE || ((ulong)mibib & 3)) return -EINVAL;
 if ((!filled(mibib + 2 * P5_NAND_BLOCK, P5_NAND_BLOCK, 0xff) &&
      !filled(mibib + 2 * P5_NAND_BLOCK, P5_NAND_BLOCK, 0)) ||
     (!filled(mibib + 3 * P5_NAND_BLOCK, P5_NAND_BLOCK, 0xff) &&
      !filled(mibib + 3 * P5_NAND_BLOCK, P5_NAND_BLOCK, 0))) return -EINVAL;
 a = p5_mibib_validate(mibib, P5_NAND_BLOCK); b = p5_mibib_validate(mibib + P5_NAND_BLOCK, P5_NAND_BLOCK);
 if ((a < 0 && unsupported_copy(mibib)) ||
     (b < 0 && unsupported_copy(mibib + P5_NAND_BLOCK))) return -EINVAL;
 if (a < 0 && b < 0) return -EINVAL;
 if (a < 0) return 1;
 if (b < 0) return 0;
 age_a = ((const u32 *)mibib)[3]; age_b = ((const u32 *)(mibib + P5_NAND_BLOCK))[3];
 if (age_a == age_b && memcmp(mibib, mibib + P5_NAND_BLOCK, P5_NAND_BLOCK)) return -EINVAL;
 return age_b > age_a ? 1 : 0;
}
