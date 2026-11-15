#!/bin/sh
# Build and package only. Does not access a router or write flash.
set -eu
[ "$#" -eq 1 ] || { echo "Usage: $0 /absolute/path/to/arm-openwrt-linux-" >&2; exit 2; }
cross=$1
case "$cross" in /*) ;; *) exit 2;; esac
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
out="$root/out-p5-flash"
STAGING_DIR="$(dirname -- "$(dirname -- "$cross")")"
export STAGING_DIR
cd "$root"
make O="$out" ARCH=arm CROSS_COMPILE="$cross" ipq5018_cudy_p5_defconfig
make O="$out" ARCH=arm CROSS_COMPILE="$cross" -j4 all
mkdir -p "$out/candidate"
"${cross}strip" "$out/u-boot" -o "$out/candidate/u-boot.strip"
python3 tools/p5/prepare-flash-elf.py "$out/candidate/u-boot.strip"
python3 tools/p5/package-flash.py -f "$out/candidate/u-boot.strip" -o "$out/candidate/p5-uboot.mbn" -v 3
python3 tools/p5/check-flash-package.py "$out/candidate/p5-uboot.mbn"
python3 tools/p5/memory-report.py "$out/u-boot" --package "$out/candidate/p5-uboot.mbn" > "$out/candidate/memory-report.txt"
cat "$out/candidate/memory-report.txt"
(cd "$out/candidate" && sha256sum p5-uboot.mbn memory-report.txt > SHA256SUMS)
echo "Flash MBN built in $out/candidate. No installation performed."
