#!/usr/bin/env python3
"""Exercise web build dependencies without a compiler or firmware build."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="p5-web-assets-") as directory:
    work = Path(directory)
    source = work / "source"
    shutil.copytree(root / "failsafe/html", source / "failsafe/html",
                    ignore=shutil.ignore_patterns("debug_output", "node_modules"))
    shutil.copy(root / "failsafe/Makefile", source / "failsafe/Makefile")
    # Small fixtures still exercise class renaming, minification, and gzip.
    shutil.rmtree(source / "failsafe/html/pig")
    (source / "failsafe/html/pig").mkdir()
    asset = source / "failsafe/html/pig/index.html"
    asset.write_text('<html><body class="hello">Asset test</body></html>')
    harness = work / "Makefile"
    harness.write_text(f"srctree := {source}\nCONFIG_LWIP_HTTPD := y\n"
                       f"include {source}/failsafe/Makefile\n")
    def generate(name):
        output = work / name
        (output / "include/config").mkdir(parents=True, exist_ok=True)
        for config in ("include/config/auto.conf", "include/autoconf.mk"):
            (output / config).touch(exist_ok=True)
        target = output / "failsafe/fsdata-generated.c"
        command = ["make", "--no-print-directory", "-f", str(harness),
                   f"obj={output}/failsafe", str(target)]
        # mkdir is normally supplied by Kbuild.
        target.parent.mkdir(exist_ok=True)
        subprocess.run(command, cwd=output, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
        return target, command
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(generate, ("ram", "flash")))
    first, command = results[0]
    assert first.read_bytes() == results[1][0].read_bytes()
    stamp = first.stat().st_mtime_ns
    subprocess.run(command, cwd=first.parents[1], check=True, stdout=subprocess.PIPE)
    assert first.stat().st_mtime_ns == stamp, "unchanged inputs regenerated assets"
    asset.write_text('<html><body>Changed asset</body></html>')
    subprocess.run(command, cwd=first.parents[1], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert first.stat().st_mtime_ns != stamp, "asset edit did not regenerate"
    assert not (source / "failsafe/fsdata.c").exists()
    assert not list(work.glob("*/failsafe/*.work.*")), "scratch directory leaked"
    # A disabled server must not expose the generator rule.
    disabled = subprocess.run(command + ["CONFIG_LWIP_HTTPD=" , "-n"],
                              cwd=first.parents[1], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    assert b"makefsdatac" not in disabled.stdout
print("PASS: concurrent outputs, incremental rebuilds, source isolation, config guard")
