# P5 build and host tests

Run from the U-Boot source directory. The two build wrappers accept one absolute compiler prefix. They build local files only and never contact the router.

## Dependencies

The successful [CI workflow](../../.github/workflows/build-p5.yml) uses Ubuntu 22.04 and Node.js 22 with npm.
Install Git, gzip, and the workflow's host packages on the PC:

```fish
sudo apt-get update
sudo apt-get install -y git gzip build-essential libncurses-dev gawk gettext libssl-dev python3-venv zlib1g-dev libfdt-dev wget cpio flex bison bc rsync zopfli device-tree-compiler
```

With Node.js 22 and npm available, install the same web tools and test dependencies:

```fish
set p5_node "$HOME/.local/share/p5-node"
npm install --prefix "$p5_node" --no-audit --no-fund html-minifier-terser@7.2.0 clean-css@5.3.3 terser@5.44.0 svgo@4.0.0 playwright@1.61.1
set -gx NODE_PATH "$p5_node/node_modules"
set -gx PATH "$p5_node/node_modules/.bin" $PATH
playwright install --with-deps chromium
python3 -m venv "$HOME/.local/share/p5-python"
source "$HOME/.local/share/p5-python/bin/activate.fish"
python3 -m pip install unicorn==2.1.4
```

## Pinned toolchain and local builds

CI uses [1980490718/toolchain-arm_cortex-a7_gcc-5.2.0](https://github.com/1980490718/toolchain-arm_cortex-a7_gcc-5.2.0)
at commit `0daf0879219130c46812cd5029fbcc2940316b16`.
The compiler is OpenWrt GCC 5.2.0 with musl 1.1.16. From the U-Boot source directory, run:

```fish
git clone https://github.com/1980490718/toolchain-arm_cortex-a7_gcc-5.2.0.git "$HOME/p5-toolchain"
git -C "$HOME/p5-toolchain" checkout --detach 0daf0879219130c46812cd5029fbcc2940316b16
set p5_cross "$HOME/p5-toolchain/toolchain-arm_cortex-a7_gcc-5.2.0_musl-1.1.16_eabi/bin/arm-openwrt-linux-"
./tools/p5/build-ramtest.sh "$p5_cross"
./tools/p5/build-flash-candidate.sh "$p5_cross"
```

| Local directory | Contents |
|---|---|
| `out-p5-ramtest/ram-only/` | RAM-test ELF, memory report, and checksum manifest. |
| `out-p5-flash/candidate/` | `p5-uboot.mbn`, memory report, checksum manifest, and local `u-boot.strip` intermediate. |
| `out-p5-ramtest/`, `out-p5-flash/` | Complete local build trees, including the original `u-boot` ELF and generated web assets. |

The CI ZIP preserves the two export directory paths under its extraction root, but omits the local intermediates and build trees.
See [artifact contents and checksum commands](../../INSTALL.md#1-prepare-the-files) for the exact ZIP structure.
Use that installation guide for all router procedures.

The RAM-test ELF supports stock `bootelf -s`. The MBN supports the custom recovery RAM test and permanent installation.
Never flash the RAM-test ELF as APPSBL. The MBN has no standalone stock RAM launcher.

Both wrappers use the same Make-owned web asset dependency. Generated resources live at `out-p5-*/failsafe/fsdata-generated.c`. Each generation uses private scratch files and publishes atomically. To verify embedded assets, give `extract-web-ui.py` the original `out-p5-*/u-boot`, not a copy in another directory.

## Host tests

```text
python3 tools/p5/test-install.py
python3 tools/p5/test-modem.py
python3 tools/p5/test-flash-package.py
python3 tools/p5/test-factory.py
python3 tools/p5/test-memory-report.py
python3 tools/p5/test-host-strings.py
python3 tools/p5/test-storage.py
python3 tools/p5/test-storage-packages.py
python3 tools/p5/test-storage-http.py
python3 tools/p5/test-web-assets.py
python3 tools/p5/test-chain.py
python3 tools/p5/test-network.py
python3 tools/p5/test-autoboot.py
python3 tools/p5/test-ram-image.py out-p5-ramtest/u-boot
node tools/p5/test-recovery.cjs
node tools/p5/test-web-ui.cjs
node tools/p5/test-gpio-ui.cjs
```

C tests need the C compiler and OpenSSL, zlib, and libfdt headers listed above.
Chain emulation needs Unicorn. Browser tests need Playwright and Chromium.
`PYTHONPATH` or `NODE_PATH` can select an existing dependency installation.

The local MIBIB fixture is verified before use. Vendor-reference emulation prints an explicit skip if the original tool or Unicorn is absent. Set `P5_VENDOR_PARTITION_TOOL` to select a vendor tool outside this workspace.

The build artifacts must exist for package and browser tests. All host tests use temporary files, mocks or emulation. None writes router flash.

Build wrappers save `memory-report.txt` beside the final ELF or MBN and include it
in `SHA256SUMS`. The report measures linked sections and reserved memory windows.
It excludes live heap and stack use.

The manual P5 CI workflow builds both variants and tests source and embedded web assets.
It uploads images and memory reports without publishing a release.
