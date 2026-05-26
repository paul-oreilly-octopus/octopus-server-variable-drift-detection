#!/usr/bin/env python3
"""End-to-end check for variable-compare.py.

Runs variable-compare against tests/test_a.yaml and tests/test_b.yaml, which
hold ~90% identical content (reordered at every level) plus a handful of
deliberate differences:

  1. SpaceOne > library_variable_sets > common-config > variables > database.host
     - Production entry value changed
       (old: cHJvZC1kYi5leGFtcGxlLmNvbQ==   "prod-db.example.com")
       (new: bmV3LXByb2QtZGIuZXhhbXBsZS5jb20=   "new-prod-db.example.com")
       This is reported as one "Only in A" entry plus one "Only in B" entry.

  2. SpaceOne > library_variable_sets > common-config > variables > database.port
     - Variable removed in B    (Only in A)

  3. SpaceOne > library_variable_sets > common-config > variables > cache.ttl
     - Variable added in B      (Only in B)

  4. SpaceOne > projects > payment-service
     - Project added in B       (Only in B)

Everything else has been reordered (top-level keys shuffled, inner dicts
shuffled, entry lists reversed, scope value lists reversed). None of that
should appear in the report.

Expected summary: Only in A = 2, Only in B = 3, Differing values = 0.

Exit codes: 0 if all assertions pass, 1 if any fail.
"""

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "scripts" / "variable-compare.py"
FILE_A = HERE / "test_a.yaml"
FILE_B = HERE / "test_b.yaml"

# Variables that were reordered in B but whose content is identical to A.
# If any of these appears in the diff report it's a false positive. We anchor
# with " > " so e.g. "port" doesn't match "database.port".
REORDERED_ONLY_NAMES = [
    "log.level",
    "worker.count",
    "slack.webhook",
    "email.sender",
    "secret.key",
    "encryption.key",
    "api.key",
    "port",
]


def section(text: str, header: str) -> str:
    """Extract the body of a `## <header>` section from the report."""
    pattern = re.compile(
        r"^## " + re.escape(header) + r".*?\n(.*?)(?=^## |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group(1) if m else ""


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {label}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)
    return condition


def main() -> int:
    if not SCRIPT.exists():
        print(f"variable-compare.py not found at {SCRIPT}", file=sys.stderr)
        return 1
    for f in (FILE_A, FILE_B):
        if not f.exists():
            print(f"fixture missing: {f}", file=sys.stderr)
            return 1

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(FILE_A), str(FILE_B),
         "--label-a", "A", "--label-b", "B"],
        capture_output=True,
        text=True,
    )
    out = result.stdout

    print("variable-compare output:")
    print("-" * 60)
    print(out, end="" if out.endswith("\n") else "\n")
    print("-" * 60)
    print()
    print("Assertions:")

    only_a = section(out, "Only in A")
    only_b = section(out, "Only in B")
    differing = section(out, "Differing values")
    summary = section(out, "Summary")

    results = []

    # Exit code
    results.append(check(
        "exit code is 1 (differences found)",
        result.returncode == 1,
        f"got exit code {result.returncode}",
    ))

    # Expected entries in Only in A
    results.append(check(
        "Only in A contains the old database.host Production entry",
        "database.host" in only_a and "cHJvZC1kYi5leGFtcGxlLmNvbQ==" in only_a,
    ))
    results.append(check(
        "Only in A contains the removed database.port variable",
        "database.port" in only_a and "NTQzMg==" in only_a,
    ))

    # Expected entries in Only in B
    results.append(check(
        "Only in B contains the new database.host Production entry",
        "database.host" in only_b and "bmV3LXByb2QtZGIuZXhhbXBsZS5jb20=" in only_b,
    ))
    results.append(check(
        "Only in B contains the added cache.ttl variable",
        "cache.ttl" in only_b and "MzAw" in only_b,
    ))
    results.append(check(
        "Only in B contains the added payment-service project",
        "payment-service" in only_b and "timeout" in only_b and "MzA=" in only_b,
    ))

    # No false positives from reordering. Anchor with " > " so substrings
    # like "port" don't match "database.port".
    full_diff_body = only_a + "\n" + only_b + "\n" + differing
    for name in REORDERED_ONLY_NAMES:
        needle = f"> {name}:"
        results.append(check(
            f"reordered-only item '{name}' is not flagged",
            needle not in full_diff_body,
            f"unexpected substring '{needle}' in diff body",
        ))

    # Also assert the unchanged values themselves don't appear, as a
    # belt-and-braces check. We anchor on "value: " to avoid the case where
    # a short base64 (e.g. "Mg==") is a suffix of a longer one (e.g.
    # "NTQzMg==").
    UNCHANGED_VALUE_LINES = [
        "value: SU5GTw==",                                  # log.level Production
        "value: REVCVUc=",                                  # log.level Dev/Staging
        "value: NA==",                                      # worker.count Production
        "value: Mg==",                                      # worker.count Staging
        "value: ODA4MA==",                                  # web-app port
        "value: bm8tcmVwbHlAZXhhbXBsZS5jb20=",              # email.sender
        "value: aHR0cHM6Ly9ob29rcy5zbGFjay5jb20vc2VydmljZXMvWFhYWVla",  # slack.webhook
    ]
    for v in UNCHANGED_VALUE_LINES:
        results.append(check(
            f"unchanged '{v[:25]}...' is not flagged",
            v not in full_diff_body,
            f"unexpected value line '{v}' in diff body",
        ))

    # No differing scalar values expected — each whole entry is a set member
    results.append(check(
        "Differing values section is empty",
        differing.strip() == "" or "Differing values: 0" in summary,
    ))

    # Summary counts
    results.append(check(
        "summary reports Only in A: 2",
        bool(re.search(r"Only in A: 2\b", summary)),
        f"summary was:\n{summary}",
    ))
    results.append(check(
        "summary reports Only in B: 3",
        bool(re.search(r"Only in B: 3\b", summary)),
        f"summary was:\n{summary}",
    ))
    results.append(check(
        "summary reports Differing values: 0",
        bool(re.search(r"Differing values: 0\b", summary)),
        f"summary was:\n{summary}",
    ))

    print()
    passed = sum(1 for r in results if r)
    total = len(results)
    if passed == total:
        print(f"OK — {passed}/{total} assertions passed.")
        return 0
    print(f"FAIL — {passed}/{total} assertions passed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
