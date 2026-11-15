# Install on a Cudy P5

Use a P5 with 256 MiB RAM and 128 MiB NAND. Keep a working UART connection
at 115200 baud throughout the first installation. Connect the PC by LAN.

## 1. Prepare the files

Download the artifact ZIP from a successful [manual CI run](https://github.com/MayorBug/ubootP5webui/actions/workflows/build-p5.yml).
Alternatively, use the [local build instructions](tools/p5/README.md).
CI exports these files:

```text
out-p5-ramtest/ram-only/
  p5-ramtest-NOT-FOR-FLASH.elf
  memory-report.txt
  SHA256SUMS
out-p5-flash/candidate/
  p5-uboot.mbn
  memory-report.txt
  SHA256SUMS
```

Local builds export the same named files under the repository root.
A downloaded ZIP contains these export directories under its extraction root, without the local build intermediates.
The two memory reports and checksum manifests are separate files. Keep each in its original directory.

From the extraction root or local repository root, run these PC commands:

```fish
cd out-p5-ramtest/ram-only
sha256sum -c SHA256SUMS
cd ../../out-p5-flash/candidate
sha256sum -c SHA256SUMS
```

Stop if any checksum fails. Use the images as follows:

| File | Use |
|---|---|
| `p5-ramtest-NOT-FOR-FLASH.elf` | Start recovery from stock U-Boot with `bootelf -s`. Never flash this ELF. |
| `p5-uboot.mbn` | RAM-test through the custom recovery bootloader or install permanently. |
| P5 `*-initramfs-uImage.itb` | Start OpenWrt from RAM. |
| P5 `*-squashfs-sysupgrade.bin` | Install OpenWrt from initramfs. |
| P5 `*-squashfs-factory.ubi` | Install OpenWrt directly from web recovery. |

Use OpenWrt images for your selected single or dual layout. The matching kernel
must enable modem power; this U-Boot leaves the modem off by default.

## 2. Start recovery from stock U-Boot

Set the PC LAN address to `192.168.1.88/24`. Start a TFTP server there and place
`p5-ramtest-NOT-FOR-FLASH.elf` in its serving directory. Stop stock U-Boot at the UART prompt.

```text
setenv ethact eth1
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.88
setenv netmask 255.255.255.0
setenv autostart no
ping 192.168.1.88
tftpboot 0x44000000 192.168.1.88:p5-ramtest-NOT-FOR-FLASH.elf
crc32 0x44000000 ${filesize}
```

Compare the byte count and CRC with the ELF from the same build. Stop if either differs.
Then run:

```text
setenv autostart yes
bootelf -s 0x44000000
```

The RAM build starts web recovery automatically. Open `http://192.168.1.1/`.
Never flash the ELF or use `go` to launch the MBN from stock U-Boot.

## 3. Save backups

Open **Back up partitions**. Download a full NAND backup and a full NAND backup
with OOB. Keep them outside the router. Also download the named partitions and
their manifests if you want to use guided partition restoration later.

Before layout conversion, save named backups and manifests for `0:MIBIB`, `0:BOOTCONFIG`, and `0:BOOTCONFIG1`.
Keep the firmware backups and matching OpenWrt images for the target layout.

OOB means the extra bytes stored beside each NAND page. The OOB backup is an
archive for low-level recovery. This interface does not replay raw OOB or restore
a full NAND backup in one step.

## 4. Test and install U-Boot

1. Open **Test / Flash U-boot**, then **Flash**.
2. Select `p5-uboot.mbn` and choose **Check image**.
3. Choose **Test from RAM**. Wait for recovery to load again.
4. Check the web UI, backups, and **Boot installed firmware**.
5. Return to recovery and select the same MBN again. Choose **Check image**, then **Install U-Boot**.
6. Confirm only if you have backups. Keep power connected until readback verification completes.
7. Restart manually. Keep the UART log that shows the new U-Boot starting from flash.

Installation replaces APPSBL only. It preserves the second U-Boot copy, environment,
firmware, calibration, and device identity. RAM boot alone does not prove flash startup.

## 5. Choose the OpenWrt layout

Skip this step if you want to keep the current layout.

1. Open **Change flash layout**.
2. Select single rootfs or original dual rootfs.
3. Choose **Check conversion** and compare both maps.
4. Choose **Apply layout** and confirm.
5. Restart before installing firmware.

Single-layout conversion first normalizes both BOOTCONFIG copies to primary `rootfs` when needed.
It reads back both copies before updating MIBIB. The affected metadata partitions are `0:BOOTCONFIG`, `0:BOOTCONFIG1`, and `0:MIBIB`.
Conversion to dual layout changes MIBIB only.

Conversion does not rewrite existing firmware data or move secondary firmware into primary storage.
Restart to refresh SMEM, the boot information shared by the boot chain.
Then enter recovery and install firmware for the selected layout. Conversion itself does not restore an OpenWrt configuration.

The earlier dual-to-single conversion ran on one P5. The BOOTCONFIG normalization sequence passed simulated tests only.
Conversion back to dual still needs a hardware test.

For an existing single layout, select **Single rootfs (72 MiB)** and choose **Apply layout** to repair BOOTCONFIG when needed.
The backend leaves MIBIB unchanged when both copies already match. The existing backup confirmation still applies.
Restart after any BOOTCONFIG repair. An unchanged dual-layout request remains blocked by the WebUI.

## 6. Install OpenWrt

### Through initramfs

1. Open **Boot / Flash OpenWrt**, then **Initramfs**.
2. Upload the matching P5 initramfs and boot it.
3. In OpenWrt, upload the matching sysupgrade image. Install it without keeping settings.

This path is hardware-tested. Use it if the direct factory image check refuses your file.

### Direct factory UBI

1. Open **Boot / Flash OpenWrt**, then **Factory UBI**.
2. Select a P5 `*-squashfs-factory.ubi` for the current layout, at most 32 MiB.
3. Choose **Flash factory UBI** and confirm the loss of firmware and settings.
4. Wait for image checks, writing, and readback verification to finish.
5. Restart manually, then check OpenWrt over UART and LAN.

The action writes the active firmware region. It preserves bootloader and device
data. The 32 MiB limit applies to the uploaded file, even for the 72 MiB single layout.
The backend requires the following image structure before erase:

- A 128 KiB-aligned file of at least 512 KiB, with 2 KiB VID offsets and 4 KiB data offsets.
- Valid UBI header/table CRCs, a consistent image sequence, and matching redundant volume tables.
- Complete dynamic `kernel` and `rootfs` volumes, plus an empty autoresizing `rootfs_data` reservation. The rootfs must start with a SquashFS header. Extra volumes are refused.
- An embedded FIT, the kernel and device-tree container, with configuration `config@mp03.3` and a Linux/ARM64 kernel.
- An uncompressed device tree for `cudy,p5` with firmware partition boundaries that match the current layout.
- Matching hashes for both FIT components. At least one hash per component is required. Supported algorithms are CRC32, SHA1, and SHA256.

A `.ubi` filename alone does not satisfy these requirements. The backend refuses incompatible images before erase.
Targets with NAND bad blocks are currently refused. Use initramfs instead.
Direct factory UBI flashing is hardware-tested on the single-rootfs layout.
After OpenWrt starts, you can restore its configuration backup through LuCI.
The dual-layout path passed simulated tests for both active slots. It still needs a hardware test.

## If an operation fails

Save the UART output and stay in recovery. Do not assume that a timed-out browser
request means no write occurred. Do not retry a write automatically.
The web terminal runs unrestricted U-Boot commands; its commands bypass guided checks.
Use web recovery only on a trusted local network.
