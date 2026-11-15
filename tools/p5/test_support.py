"""Portable fixture/dependency lookup for host-only P5 tests."""
from pathlib import Path
import gzip
import hashlib
import os

ROOT = Path(__file__).resolve().parents[2]

def mibib_fixture():
    data = gzip.decompress((Path(__file__).parent/'fixtures/mibib-dual.bin.gz').read_bytes())
    if hashlib.sha256(data).hexdigest() != "f9e81c832926efc3f36aab18221bd397b4014f1bf5413e5f90e6c5332fda270c":
        raise ValueError('MIBIB fixture hash mismatch')
    return data

def vendor_partition_tool():
    default = ROOT.parents[1]/'Vendor/openwrt_p5_gpl/18.06/qca/sdk_image_ipq50xx/boot_images/core/bsp/tools/flash/partition_tool_exe/build/partition_tool'
    return Path(os.environ.get('P5_VENDOR_PARTITION_TOOL', str(default)))
