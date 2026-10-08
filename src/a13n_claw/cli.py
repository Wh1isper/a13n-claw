"""Report package identity without implying a working agent runtime."""

import argparse
from collections.abc import Sequence

from a13n_claw import __version__


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="a13n-claw",
        description="a13n Claw placeholder. Agent execution is not implemented yet.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
