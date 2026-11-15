# MIBIB reference fixture

`mibib-dual.bin.gz` is a compressed copy of the saved P5 vendor MIBIB backup. It contains partition metadata, not calibration or device identity data. The original backup is unchanged.

Uncompressed SHA256: `f9e81c832926efc3f36aab18221bd397b4014f1bf5413e5f90e6c5332fda270c`.

Tests verify this digest before using the fixture. Optional vendor machine-code comparison uses `P5_VENDOR_PARTITION_TOOL`, or the adjacent workspace Vendor directory when present. Install Unicorn or set PYTHONPATH to its installation for CPU emulation.
