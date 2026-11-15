/* SPDX-License-Identifier: GPL-2.0+ */
#include <common.h>
#include <asm/errno.h>
#include <command.h>
#include <elf.h>
#include <image.h>
#include <net.h>
#include <asm/io.h>
#include <p5_recovery.h>
#include <p5_storage.h>

#define CHAIN_BASE P5_CHAIN_BASE
#define MOVE_BASE  P5_MOVE_BASE
#define CHAIN_SP   P5_CHAIN_SP
#define IMAGE_BASE P5_RAM_BASE
#define IMAGE_END  P5_RAM_END
#define MAX_MOVES 32
struct p5_move { u32 destination, source, length; };
extern const u8 p5_chain_start[], p5_chain_end[];

static int bounded(ulong offset, ulong length, ulong limit)
{
	return offset <= limit && length <= limit - offset;
}

/* Matches the fixed-address RAM target, including BSS outside PT_LOAD. */
static int p5_elf_plan(ulong address, ulong size, struct p5_move *moves, unsigned *count)
{
	const Elf32_Ehdr *h = (const void *)address;
	unsigned i, j, n = 0;
	int entry_found = 0, load_found = 0;

	if (size < sizeof(*h) || memcmp(h->e_ident, "\177ELF\1\1\1", 7) ||
	    h->e_type != ET_EXEC || h->e_machine != EM_ARM || h->e_version != 1 ||
	    h->e_entry != IMAGE_BASE + 1 || h->e_ehsize != sizeof(*h) ||
	    (h->e_shoff & 3) || (h->e_phoff & 3) ||
	    h->e_shentsize != sizeof(Elf32_Shdr) || !h->e_shnum || h->e_shnum > MAX_MOVES ||
	    h->e_phentsize != sizeof(Elf32_Phdr) || !h->e_phnum || h->e_phnum > MAX_MOVES ||
	    !bounded(h->e_shoff, h->e_shnum * sizeof(Elf32_Shdr), size) ||
	    !bounded(h->e_phoff, h->e_phnum * sizeof(Elf32_Phdr), size))
		return -EINVAL;
	for (i = 0; i < h->e_phnum; i++) {
		const Elf32_Phdr *p = (const void *)(address + h->e_phoff + i * sizeof(*p));
		if (p->p_type == PT_DYNAMIC || p->p_type == PT_INTERP)
			return -EINVAL;
		if (p->p_type != PT_LOAD)
			continue;
		if (p->p_paddr != p->p_vaddr || p->p_filesz > p->p_memsz ||
		    p->p_paddr < IMAGE_BASE || !bounded(p->p_paddr, p->p_memsz, IMAGE_END) ||
		    !bounded(p->p_offset, p->p_filesz, size))
			return -EINVAL;
		load_found++;
	}
	for (i = 0; i < h->e_shnum; i++) {
		const Elf32_Shdr *s = (const void *)(address + h->e_shoff + i * sizeof(*s));
		if (!(s->sh_flags & SHF_ALLOC) || !s->sh_size)
			continue;
		if (s->sh_addr < IMAGE_BASE || !bounded(s->sh_addr, s->sh_size, IMAGE_END) ||
		    (s->sh_type != SHT_NOBITS &&
		     (s->sh_type != SHT_PROGBITS || !bounded(s->sh_offset, s->sh_size, size))))
			return -EINVAL;
		for (j = 0; j < n; j++)
			if (s->sh_addr < moves[j].destination + moves[j].length &&
			    moves[j].destination < s->sh_addr + s->sh_size)
				return -EINVAL;
		moves[n].destination = s->sh_addr;
		moves[n].source = s->sh_type == SHT_NOBITS ? 0 : address + s->sh_offset;
		moves[n++].length = s->sh_size;
		if (s->sh_addr == IMAGE_BASE && s->sh_type == SHT_PROGBITS &&
		    s->sh_size >= 4 && (s->sh_flags & SHF_EXECINSTR) &&
		    !memcmp((const void *)(address + s->sh_offset), "\170\107\000\277", 4))
			entry_found = 1;
	}
	if (!entry_found || !load_found || !n)
		return -EINVAL;
	*count = n;
	return 0;
}

int p5_boot_check(int kind, ulong address, ulong size)
{
	struct p5_move moves[MAX_MOVES];
	unsigned count;

	if (kind == P5_BOOT_INSTALLED)
		return (p5_storage_boot_blocked() || p5_install_boot_blocked()) ? -EBUSY : 0;
	if (address != P5_UPLOAD_BASE || !size || size > P5_UPLOAD_LIMIT)
		return -EINVAL;
	if (kind == P5_BOOT_FLASH_TEST)
		return p5_install_check(address, size);
	if (kind == P5_INSTALL_CHECK)
		return p5_install_preflight(address, size);
	if (kind == P5_BOOT_UBOOT)
		return p5_elf_plan(address, size, moves, &count);
	if (kind == P5_BOOT_INITRAMFS)
		return 0; /* Use bootm's normal image handling, as after TFTP. */
	return -EINVAL;
}

int p5_boot_run(int kind, ulong address, ulong size)
{
	struct p5_move moves[MAX_MOVES];
	unsigned count;
	char argument[112];
	char *argv[] = { "bootm", argument, NULL };
	ulong length = p5_chain_end - p5_chain_start;
	ulong entry = IMAGE_BASE + 1;
	void (*jump)(ulong, unsigned, ulong, ulong) = (void *)CHAIN_BASE;

	if (kind == P5_BOOT_UBOOT) {
		if (address != P5_UPLOAD_BASE || !size || size > P5_UPLOAD_LIMIT ||
		    p5_elf_plan(address, size, moves, &count)) return CMD_RET_FAILURE;
	} else if (p5_boot_check(kind, address, size))
		return CMD_RET_FAILURE;
	if (kind == P5_BOOT_INSTALLED)
		return run_command("bootipq", 0);
	if (kind == P5_BOOT_INITRAMFS) {
		snprintf(argument, sizeof(argument), "%lx", address);
		setenv("verify", "yes");
		setenv("fdt_high", "44ffffff");
		setenv("initrd_high", "44ffffff");
		return do_bootm(NULL, 0, 2, argv);
	}
	if (kind == P5_BOOT_FLASH_TEST) {
		const Elf32_Ehdr *h = (const void *)address;
		const Elf32_Phdr *p = (const void *)(address + h->e_phoff);
		moves[0].destination = p[2].p_paddr;
		moves[0].source = address + p[2].p_offset;
		moves[0].length = p[2].p_filesz;
		count = 1;
		entry = h->e_entry;
	}
	if (!length || length > 1024) return CMD_RET_FAILURE;
	memcpy((void *)CHAIN_BASE, p5_chain_start, length);
	memcpy((void *)MOVE_BASE, moves, count * sizeof(*moves));
	printf("P5 RAM chainload: %u sections; scratch loader at %08lx\n", count, CHAIN_BASE);
	eth_halt();
	disable_interrupts();
	flush_dcache_all();
	dcache_disable();
	icache_disable();
	jump(MOVE_BASE, count, entry, CHAIN_SP);
	return CMD_RET_FAILURE;
}

static int do_p5net(cmd_tbl_t *cmdtp, int flag, int argc, char * const argv[])
{
	#ifndef CONFIG_IPQ5018_CUDY_P5_RAMTEST
	puts("P5-FLASH-TARGET-v1\n");
#endif
	setenv("ethact", "eth1");
	setenv("ipaddr", "192.168.1.1");
	setenv("serverip", "192.168.1.88");
	setenv("netmask", "255.255.255.0");
	/* Programmatic setenv skips the network environment callbacks. */
	net_ip = getenv_ip("ipaddr");
	net_server_ip = getenv_ip("serverip");
	net_netmask = getenv_ip("netmask");
	setenv("autostart", "no");
	puts("P5 LAN: eth1, router 192.168.1.1, TFTP server 192.168.1.88\n"
	     "Web: httpd 192.168.1.1\n"
	     "Before TFTP: httpd s\n"
	     "Then: ping 192.168.1.88\n"
	     "      tftpboot 0x44000000 <filename>\n"
	     "Network setup is not saved automatically. Use saveenv only when you want persistence. Run p5net to restore these defaults.\n");
	return 0;
}
U_BOOT_CMD(p5net, 1, 0, do_p5net, "configure P5 recovery LAN in RAM", "");

void p5_network_start(void)
{
	do_p5net(NULL, 0, 0, NULL);
	puts("Starting P5 web recovery at http://192.168.1.1/\n");
	run_command("httpd 192.168.1.1", 0);
}
