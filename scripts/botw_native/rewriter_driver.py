"""One explicit extension of a verified upstream source; no skipped assertions."""
from pathlib import Path
import sys

from .rewrite import verified_tool


def main():
    tool = verified_tool(sys.argv[1])
    source = tool.read_text()
    marker = "            assert base and width, f'jump table {ins.address:x}'"
    if source.count(marker) != 1:
        raise ValueError('Pinned rewriter extension point changed')
    extension = (
        "            if not (base and width):\n"
        "                from botw_native.branch_tables import signed_jump_range\n"
        "                proven = signed_jump_range(insl[:j + 1], IMG, TEXT_END, RO_OFF, DATA_OFF, all_instructions=insl)\n"
        "                if proven: base, width = proven\n"
    )
    source = source.replace(marker, extension + marker)
    sys.argv = [str(tool), *sys.argv[2:]]
    namespace = {'__name__': '__main__', '__file__': str(tool)}
    exec(compile(source, str(tool), 'exec'), namespace)


if __name__ == '__main__':
    main()
