#!/bin/sh
# Build the RAM recovery image with explicit write tools. Never flash its ELF.
set -eu
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /absolute/path/to/arm-openwrt-linux-" >&2
    exit 2
fi
cross=$1
case "$cross" in /*) ;; *) echo 'Use an absolute compiler prefix' >&2; exit 2;; esac
"${cross}gcc" --version | head -n 1
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
out="$root/out-p5-ramtest"
STAGING_DIR="$(dirname -- "$(dirname -- "$cross")")"
export STAGING_DIR
cd "$root"
make O="$out" ARCH=arm CROSS_COMPILE="$cross" ipq5018_cudy_p5_ramtest_defconfig
make O="$out" ARCH=arm CROSS_COMPILE="$cross" -j4 all
python3 tools/p5/check-ram-image.py "$out/u-boot"
python3 tools/p5/test-ram-image.py "$out/u-boot"
python3 tools/p5/check-embedded-dtb.py "$out/u-boot"
mkdir -p "$out/ram-only"
cp "$out/u-boot" "$out/ram-only/p5-ramtest-NOT-FOR-FLASH.elf"
rm -f "$out/ram-only/p5-ramtest-NOT-FOR-FLASH.bin"
python3 tools/p5/memory-report.py "$out/u-boot" > "$out/ram-only/memory-report.txt"
cat "$out/ram-only/memory-report.txt"
(cd "$out/ram-only" && sha256sum p5-ramtest-NOT-FOR-FLASH.elf memory-report.txt > SHA256SUMS)
echo "Built $out/ram-only. Do not launch until the stock RAM map is checked."
