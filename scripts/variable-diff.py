#!/usr/bin/env python3
"""Compare two Octopus Deploy variable export YAML files and show drift.

Takes two YAML files produced by octopus-variable-export.py and produces
a unified diff showing where variables differ between servers.

Usage:
    python variable-diff.py onprem-variables.yaml cloud-variables.yaml
    python variable-diff.py --decode onprem-variables.yaml cloud-variables.yaml
    python variable-diff.py --decode onprem-variables.yaml

The output uses unified diff format, identical to `diff -u` on Linux.
With --decode, base64-encoded values are decoded in memory before display or diff.
With a single file and --decode, the file's variables are displayed with decoded values.
"""

import argparse
import base64
import difflib
import sys
from pathlib import Path

import yaml


def try_decode_b64(s: str) -> str:
    """Return the UTF-8 decoded string if s is valid base64, otherwise return s unchanged."""
    try:
        decoded_bytes = base64.b64decode(s, validate=True)
        return decoded_bytes.decode("utf-8")
    except Exception:
        return s


def decode_values_in_place(obj) -> None:
    """Walk a parsed YAML structure and decode base64 'value' fields in memory."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "value" and isinstance(val, str):
                obj[key] = try_decode_b64(val)
            else:
                decode_values_in_place(val)
    elif isinstance(obj, list):
        for item in obj:
            decode_values_in_place(item)


def yaml_dump(data) -> str:
    return yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


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
        nargs="?",
        default=None,
        help="Second YAML export (e.g. cloud server); omit to view a single file with --decode",
    )
    parser.add_argument(
        "--decode",
        action="store_true",
        help="Decode base64 variable values in memory before display or diff",
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

    if args.file_b is None and not args.decode:
        parser.error("file_b is required unless --decode is used with a single file")

    path_a = Path(args.file_a)
    if not path_a.exists():
        print(f"Error: File not found: {path_a}", file=sys.stderr)
        sys.exit(1)

    # Single-file decode mode: display decoded YAML and exit
    if args.file_b is None:
        data = yaml.safe_load(path_a.read_text(encoding="utf-8"))
        decode_values_in_place(data)
        sys.stdout.write(yaml_dump(data))
        sys.exit(0)

    path_b = Path(args.file_b)
    if not path_b.exists():
        print(f"Error: File not found: {path_b}", file=sys.stderr)
        sys.exit(1)

    if args.decode:
        data_a = yaml.safe_load(path_a.read_text(encoding="utf-8"))
        data_b = yaml.safe_load(path_b.read_text(encoding="utf-8"))
        decode_values_in_place(data_a)
        decode_values_in_place(data_b)
        lines_a = yaml_dump(data_a).splitlines(keepends=True)
        lines_b = yaml_dump(data_b).splitlines(keepends=True)
    else:
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
