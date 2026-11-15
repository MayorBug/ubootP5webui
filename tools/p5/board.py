"""Read literal constants from the C P5 board profile; never evaluate C code."""
from pathlib import Path
import re

HEADER = Path(__file__).resolve().parents[2]/'include/p5_board.h'
CONSTANTS = {name: int(value, 0) for name, value in re.findall(
    r'^#define\s+(P5_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|[0-9]+)[UL]*\s*$',
    HEADER.read_text(), re.MULTILINE)}
