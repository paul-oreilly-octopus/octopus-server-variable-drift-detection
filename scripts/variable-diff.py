#!/usr/bin/env python3
"""Compare two Octopus Deploy variable export YAML files and show drift.

Takes two YAML files produced by octopus-variable-export.py and produces
a unified diff showing where variables differ between servers.

Usage:
    python octopus-variable-diff.py onprem-variables.yaml cloud-variables.yaml

The output uses unified diff format, identical to `diff -u` on Linux.
Any standard diff viewer (VS Code, WinMerge, Beyond Compare) can also
be used directly on the YAML files.
"""

import argparse
import difflib
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Compare two Octopus variable export YAML files"
    )
    parser.add_argument(
        "file_a",
        help="First YAML export (e.g. on-prem server)",
    )
    parser.add_argument(
        "file_b",
        help="Second YAML export (e.g. cloud server)",
    )
    parser.add_argument(
        "--label-a",
        default=None,
        help="Label for first file in diff output (default: filename)",
    )
    parser.add_argument(
        "--label-b",
        default=None,
        help="Label for second file in diff output (default: filename)",
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=3,
        help="Number of context lines in diff output (default: 3)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write diff to file instead of stdout",
    )
    args = parser.parse_args()

    path_a = Path(args.file_a)
    path_b = Path(args.file_b)

    for path in (path_a, path_b):
        if not path.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)

    lines_a = path_a.read_text(encoding="utf-8").splitlines(keepends=True)
    lines_b = path_b.read_text(encoding="utf-8").splitlines(keepends=True)

    label_a = args.label_a or str(path_a)
    label_b = args.label_b or str(path_b)

    diff = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=label_a,
            tofile=label_b,
            n=args.context_lines,
        )
    )

    if not diff:
        print("No differences found.")
        sys.exit(0)

    output_text = "".join(diff)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(output_text, encoding="utf-8")
        print(f"Diff written to: {output_path}")
    else:
        sys.stdout.write(output_text)

    # Exit 1 when differences exist (same convention as diff)
    sys.exit(1)


if __name__ == "__main__":
    main()
