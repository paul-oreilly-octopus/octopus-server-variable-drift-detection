#!/usr/bin/env python3
"""Compare two Octopus Deploy variable export YAML files using recursive set difference.

Replaces the unified-diff approach of variable-diff.py. Treats dicts as keyed
maps and lists as multisets, so the comparison is independent of the order in
which keys or list elements appear in either input file. The result is a
structured report of:
  - subtrees present only in A
  - subtrees present only in B
  - scalar values that differ at the same path

Two scoped variable entries that contain identical data but appear in a
different list position in each file will NOT be reported as a difference.

Usage:
    python variable-compare.py onprem-variables.yaml cloud-variables.yaml
    python variable-compare.py --decode onprem-variables.yaml cloud-variables.yaml
    python variable-compare.py --decode onprem-variables.yaml

With --decode, base64-encoded variable values are decoded in memory before
comparison or display. With a single file and --decode, the file's variables
are displayed with decoded values (no comparison is performed).

Exit codes:
    0  no differences (or single-file display mode)
    1  one or more differences
    2  usage / file error
"""

import argparse
import base64
import sys
from collections import Counter
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


def canonical(obj):
    """Return a hashable, order-independent representation of obj.

    - dicts become frozensets of (key, canonical(value)) pairs
    - lists become frozensets of (canonical(item), count) pairs (multiset)
    - scalars are returned as ("scalar", obj) for type-safe comparison
    """
    if isinstance(obj, dict):
        return ("dict", frozenset((k, canonical(v)) for k, v in obj.items()))
    if isinstance(obj, list):
        counts = Counter(canonical(x) for x in obj)
        return ("list", frozenset(counts.items()))
    return ("scalar", obj)


def yaml_block(data, indent: int = 4) -> str:
    """Format data as a YAML block, indented by `indent` spaces."""
    text = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=True,
        width=120,
    )
    prefix = " " * indent
    return "".join(prefix + line for line in text.splitlines(keepends=True))


def fmt_path(path: list) -> str:
    return " > ".join(str(p) for p in path) if path else "(root)"


class Report:
    def __init__(self):
        self.only_a: list[tuple[list, object]] = []
        self.only_b: list[tuple[list, object]] = []
        self.scalar: list[tuple[list, object, object]] = []
        self.type_mismatch: list[tuple[list, object, object]] = []

    def is_empty(self) -> bool:
        return not (self.only_a or self.only_b or self.scalar or self.type_mismatch)

    def render(self, label_a: str, label_b: str) -> str:
        out: list[str] = []
        out.append("# Comparison")
        out.append(f"A: {label_a}")
        out.append(f"B: {label_b}")
        out.append("")

        if self.only_a:
            out.append(f"## Only in A ({label_a})")
            out.append("")
            for path, val in self.only_a:
                out.append(f"  {fmt_path(path)}:")
                out.append(yaml_block(val, indent=4).rstrip("\n"))
                out.append("")

        if self.only_b:
            out.append(f"## Only in B ({label_b})")
            out.append("")
            for path, val in self.only_b:
                out.append(f"  {fmt_path(path)}:")
                out.append(yaml_block(val, indent=4).rstrip("\n"))
                out.append("")

        if self.scalar:
            out.append("## Differing values")
            out.append("")
            for path, av, bv in self.scalar:
                out.append(f"  {fmt_path(path)}:")
                out.append(f"    A: {av!r}")
                out.append(f"    B: {bv!r}")
            out.append("")

        if self.type_mismatch:
            out.append("## Type mismatches")
            out.append("")
            for path, av, bv in self.type_mismatch:
                out.append(f"  {fmt_path(path)}:")
                out.append(f"    A ({type(av).__name__}): {av!r}")
                out.append(f"    B ({type(bv).__name__}): {bv!r}")
            out.append("")

        out.append("## Summary")
        out.append(f"  Only in A: {len(self.only_a)}")
        out.append(f"  Only in B: {len(self.only_b)}")
        out.append(f"  Differing values: {len(self.scalar)}")
        if self.type_mismatch:
            out.append(f"  Type mismatches: {len(self.type_mismatch)}")
        return "\n".join(out) + "\n"


def compare(a, b, path: list, report: Report) -> None:
    """Recursively compare a and b, recording differences in report."""
    if isinstance(a, dict) and isinstance(b, dict):
        a_keys = set(a.keys())
        b_keys = set(b.keys())
        for k in sorted(a_keys - b_keys, key=str):
            report.only_a.append((path + [k], a[k]))
        for k in sorted(b_keys - a_keys, key=str):
            report.only_b.append((path + [k], b[k]))
        for k in sorted(a_keys & b_keys, key=str):
            compare(a[k], b[k], path + [k], report)
        return

    if isinstance(a, list) and isinstance(b, list):
        a_counts = Counter(canonical(x) for x in a)
        b_counts = Counter(canonical(x) for x in b)
        only_a = a_counts - b_counts
        only_b = b_counts - a_counts

        a_repr: dict = {}
        for x in a:
            c = canonical(x)
            a_repr.setdefault(c, x)
        b_repr: dict = {}
        for x in b:
            c = canonical(x)
            b_repr.setdefault(c, x)

        for c, count in only_a.items():
            for _ in range(count):
                report.only_a.append((path, a_repr[c]))
        for c, count in only_b.items():
            for _ in range(count):
                report.only_b.append((path, b_repr[c]))
        return

    if type(a) is not type(b):
        # Treat None vs missing keys as a "type mismatch" only when both sides
        # exist but with different container types. Simple scalar-type mixing
        # (e.g. int vs float) is rare in our data; fall through to equality.
        if {type(a), type(b)} & {dict, list}:
            report.type_mismatch.append((path, a, b))
            return

    if a != b:
        report.scalar.append((path, a, b))


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
        description="Compare two Octopus variable export YAML files "
                    "(order-independent recursive set difference)"
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
        help="Decode base64 variable values in memory before comparison or display",
    )
    parser.add_argument(
        "--label-a",
        default=None,
        help="Label for first file in report (default: filename)",
    )
    parser.add_argument(
        "--label-b",
        default=None,
        help="Label for second file in report (default: filename)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write report to file instead of stdout",
    )
    args = parser.parse_args()

    if args.file_b is None and not args.decode:
        parser.error("file_b is required unless --decode is used with a single file")

    path_a = Path(args.file_a)
    if not path_a.exists():
        print(f"Error: File not found: {path_a}", file=sys.stderr)
        sys.exit(2)

    if args.file_b is None:
        data = yaml.safe_load(path_a.read_text(encoding="utf-8"))
        decode_values_in_place(data)
        sys.stdout.write(yaml_dump(data))
        sys.exit(0)

    path_b = Path(args.file_b)
    if not path_b.exists():
        print(f"Error: File not found: {path_b}", file=sys.stderr)
        sys.exit(2)

    data_a = yaml.safe_load(path_a.read_text(encoding="utf-8")) or {}
    data_b = yaml.safe_load(path_b.read_text(encoding="utf-8")) or {}

    if args.decode:
        decode_values_in_place(data_a)
        decode_values_in_place(data_b)

    label_a = args.label_a or str(path_a)
    label_b = args.label_b or str(path_b)

    report = Report()
    compare(data_a, data_b, [], report)

    if report.is_empty():
        msg = f"No differences found between {label_a} and {label_b}.\n"
        if args.output:
            Path(args.output).write_text(msg, encoding="utf-8")
            print(f"Report written to: {args.output}")
        else:
            sys.stdout.write(msg)
        sys.exit(0)

    rendered = report.render(label_a, label_b)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Report written to: {args.output}")
    else:
        sys.stdout.write(rendered)
    sys.exit(1)


if __name__ == "__main__":
    main()
