/* SPDX-License-Identifier: GPL-2.0+ */
/* Deliberately narrow: existing P5 APPSBL only; no layout or backup-slot writes. */
#include <common.h>
#include <asm/errno.h>
#include <command.h>
#include <elf.h>
#include <nand.h>
#include <u-boot/sha256.h>
#include <asm/arch-qca-common/smem.h>
#include <p5_recovery.h>
#include <p5_storage.h>

DECLARE_GLOBAL_DATA_PTR;

#define SLOT_START P5_APPSBL_OFFSET
#define SLOT_SIZE  P5_APPSBL_SIZE
#define TARGET_BASE P5_FLASH_BASE
#define TARGET_END P5_FLASH_CODE_END
#define READBACK P5_READBACK_BASE
static int writing, install_dirty;
static u8 install_hash[32];
int p5_install_boot_blocked(void) { return install_dirty; }
extern int is_sec_boot_enabled(void);

static int within(ulong start, ulong size, ulong limit)
{
	return start <= limit && size <= limit - start;
}

/* Validate Qualcomm ELF/hash structure and every SHA256 entry. */
int p5_install_check(ulong address, ulong size)
{
	const Elf32_Ehdr *h = (const void *)address;
	const Elf32_Phdr *ph, *hash = NULL;
	const u32 *mbn;
	unsigned i, j, loads = 0;
	int board_marker = 0;
	u8 digest[32];
	static const u8 zero[32];

	if (address != P5_UPLOAD_BASE || size < sizeof(*h) || size > SLOT_SIZE ||
	    memcmp(h->e_ident, "\177ELF\1\1\1", 7) || h->e_type != ET_EXEC ||
	    h->e_machine != EM_ARM || h->e_version != 1 || h->e_entry != TARGET_BASE ||
	    h->e_phoff != sizeof(*h) || h->e_phentsize != sizeof(*ph) ||
	    h->e_ehsize != sizeof(*h) || h->e_phnum != 4 ||
	    !within(h->e_phoff, h->e_phnum * sizeof(*ph), size))
		return -EINVAL;
	ph = (const void *)(address + h->e_phoff);
	for (i = 0; i < h->e_phnum; i++) {
		if (!within(ph[i].p_offset, ph[i].p_filesz, size))
			return -EINVAL;
		if ((ph[i].p_flags & 0x07000000) == 0x02000000) {
			if (hash || i != 1 || ph[i].p_type != PT_NULL ||
			    ph[i].p_paddr != ph[i].p_vaddr ||
			    ph[i].p_paddr < TARGET_BASE ||
			    ph[i].p_filesz > ph[i].p_memsz ||
			    !within(ph[i].p_paddr, ph[i].p_memsz, TARGET_END))
				return -EINVAL;
			hash = &ph[i];
		} else if (ph[i].p_type == PT_LOAD) {
			if (i != 2 || ph[i].p_paddr != TARGET_BASE || ph[i].p_vaddr != TARGET_BASE ||
			    ph[i].p_filesz > ph[i].p_memsz ||
			    !within(TARGET_BASE, ph[i].p_memsz, TARGET_END))
				return -EINVAL;
			for (j = 0; j + sizeof("P5-FLASH-TARGET-v1") <= ph[i].p_filesz; j++)
				if (!memcmp((const void *)(address + ph[i].p_offset + j),
					"P5-FLASH-TARGET-v1", sizeof("P5-FLASH-TARGET-v1")))
					board_marker = 1;
			loads++;
		} else if (i == 0) {
			if (ph[i].p_type != PT_NULL || ph[i].p_offset ||
			    ph[i].p_filesz != sizeof(*h) + h->e_phnum * sizeof(*ph) ||
			    ph[i].p_paddr || ph[i].p_vaddr || ph[i].p_memsz)
				return -EINVAL;
		} else if (i != 3 || ph[i].p_type != 0x6474e551 ||
			   ph[i].p_filesz || ph[i].p_memsz || ph[i].p_paddr || ph[i].p_vaddr)
			return -EINVAL;
	}
	if (!hash || !board_marker || loads != 1 || (hash->p_offset & 3) ||
	    hash->p_filesz != 40U + h->e_phnum * 32U)
		return -EINVAL;
	mbn = (const void *)(address + hash->p_offset);
	if (mbn[0] != 21 || mbn[1] != 3 || mbn[2] ||
	    mbn[3] != hash->p_paddr + hash->p_offset + 40 ||
	    !within(hash->p_paddr, hash->p_offset + 40 + mbn[4], TARGET_END) ||
	    mbn[4] != h->e_phnum * 32U || mbn[5] != mbn[4] ||
	    mbn[6] != mbn[3] + mbn[5] || mbn[8] != mbn[6] || mbn[7] || mbn[9])
		return -EINVAL;
	for (i = 0; i < h->e_phnum; i++) {
		const void *want = (const void *)(address + hash->p_offset + 40 + i * 32);
		if (&ph[i] == hash || !ph[i].p_filesz) {
			if (memcmp(want, zero, 32)) return -EINVAL;
		} else {
			sha256_csum_wd((const void *)(address + ph[i].p_offset),
				ph[i].p_filesz, digest, 4096);
			if (memcmp(want, digest, 32)) return -EINVAL;
		}
	}
	return 0;
}

static int slot_ready(void)
{
	u32 block, blocks;
	ulong offset;
	unsigned i;
	int selected = 0;
	nand_info_t *mtd = &nand_info[0];

	if (gd->bd->bi_arch_number != P5_MACHID || is_sec_boot_enabled() != 0 ||
	    mtd->size != P5_FLASH_BYTES || mtd->writesize != P5_PAGE_BYTES || mtd->erasesize != P5_NAND_BLOCK ||
	    qca_smem_flash_info.flash_block_size != mtd->erasesize ||
	    smem_getpart("0:APPSBL", &block, &blocks) ||
	    (u64)block * mtd->erasesize != SLOT_START ||
	    (u64)blocks * mtd->erasesize != SLOT_SIZE)
		return -EINVAL;
	if (smem_bootconfig_info() || !qca_smem_bootconfig_info.numaltpart ||
	    qca_smem_bootconfig_info.numaltpart > ARRAY_SIZE(qca_smem_bootconfig_info.per_part_entry) ||
	    qca_smem_bootconfig_info.magic_end != _SMEM_DUAL_BOOTINFO_MAGIC_END) return -EINVAL;
	for (i = 0; i < qca_smem_bootconfig_info.numaltpart; i++) {
		struct per_part_info *part = &qca_smem_bootconfig_info.per_part_entry[i];
		if (!strncmp(part->name, "0:APPSBL", sizeof(part->name))) {
			if (selected || part->primaryboot) return -EINVAL;
			selected = 1;
		}
	}
	if (!selected) { puts("P5 install refused: active APPSBL selection is unknown\n"); return -EINVAL; }
	for (offset = SLOT_START; offset < SLOT_START + SLOT_SIZE; offset += mtd->erasesize)
		if (nand_block_isbad(mtd, offset) != 0) return -EIO;
	return 0;
}

int p5_install_preflight(ulong address, ulong size)
{
	int ret = p5_install_check(address, size);
	return ret ? ret : slot_ready();
}

int p5_install_run(ulong address, ulong size)
{
	nand_info_t *mtd = &nand_info[0];
	u8 digest[32], candidate[32];
	size_t length = SLOT_SIZE;
	ulong padded, i;
	int ret;
	struct erase_info erase;

	if (writing || p5_storage_busy() || p5_install_check(address, size) || slot_ready())
		return -EINVAL;
	sha256_csum_wd((void *)address, size, candidate, 4096);
	if (install_dirty && memcmp(candidate, install_hash, 32)) return -EINVAL;
	padded = ALIGN(size, mtd->writesize);
	memset((void *)(address + size), 0xff, padded - size);
	puts("P5 install: erasing APPSBL only; do not remove power\n");
	memcpy(install_hash, candidate, 32);
	install_dirty = 1;
	writing = 1;
	memset(&erase, 0, sizeof(erase));
	erase.mtd = mtd;
	erase.addr = SLOT_START;
	erase.len = SLOT_SIZE;
	ret = mtd_erase(mtd, &erase);
	if (!ret) {
		length = padded;
		ret = nand_write(mtd, SLOT_START, &length, (void *)address);
		if (!ret && length != padded) ret = -EIO;
	}
	writing = 0;
	if (ret) { puts("P5 install failed; stay in RAM recovery and save UART\n"); return ret; }
	length = SLOT_SIZE;
	ret = nand_read(mtd, SLOT_START, &length, (void *)READBACK);
	if ((ret && ret != -EUCLEAN) || length != SLOT_SIZE ||
	    memcmp((void *)address, (void *)READBACK, padded)) {
		puts("P5 readback FAILED; do not reboot. Stay in RAM recovery and save UART.\n");
		return -EIO;
	}
	for (i = padded; i < SLOT_SIZE; i++)
		if (((u8 *)READBACK)[i] != 0xff) {
			puts("P5 erased-tail verification FAILED; do not reboot. Stay in RAM recovery.\n");
			return -EIO;
		}
	sha256_csum_wd((void *)READBACK, size, digest, 4096);
	puts("P5 install: readback matches; package SHA256 ");
	for (i = 0; i < 32; i++) printf("%02x", digest[i]);
	install_dirty = 0;
	puts("\nNo reboot requested. APPSBL_1, environment and firmware unchanged.\n");
	return 0;
}

/* An explicit UART action, separate from all normal boot paths. */
static int do_p5install(cmd_tbl_t *cmdtp, int flag, int argc, char * const argv[])
{
	ulong size;
	char *end;
	if (argc != 3 && argc != 4) return CMD_RET_USAGE;
	size = simple_strtoul(argv[2], &end, 16);
	if (*end || !size) return CMD_RET_USAGE;
	if (!strcmp(argv[1], "check") && argc == 3) {
		int ret = p5_install_check(P5_UPLOAD_BASE, size);
		if (!ret) ret = slot_ready();
		printf("P5 install preflight: %s\n", ret ? "REFUSED (see UART)" : "package, board, fuse and slot checks pass; no writes");
		return ret ? CMD_RET_FAILURE : 0;
	}
	if (!strcmp(argv[1], "write") && argc == 4 && !strcmp(argv[3], "INSTALL-P5-APPSBL"))
		return p5_install_run(P5_UPLOAD_BASE, size) ? CMD_RET_FAILURE : 0;
	return CMD_RET_USAGE;
}
U_BOOT_CMD(p5install, 4, 0, do_p5install, "check/install P5 APPSBL with readback", "check <hex-bytes>\np5install write <hex-bytes> INSTALL-P5-APPSBL");
