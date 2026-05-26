# Variable Drift Detection

Detects variable drift between two Octopus Deploy servers, or one server over time. Can be used during migration from on-prem to cloud.

## How It Works

Two Python scripts, used independently:

1. **`variable-export.py`** — Connects to one Octopus server and exports all variables (library variable sets, project variables, tenant variables) to a structured, deterministic YAML file.
2. **`variable-compare.py`** — Compares two YAML exports using a recursive set difference and reports drift. Dicts are compared by key set; lists are compared as multisets, so two scoped entries with identical content but in different list positions are NOT reported as a difference.

The YAML output is sorted and deterministic, so running the export twice against the same server produces identical output (assuming no changes occurred). This makes it suitable for tracking variable changes over time.

## Setup

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## Usage

### Export variables from each server

```bash
# On-prem server
python scripts/octopus-variable-export.py \
    --server-url https://octopus-onprem.mydomain.com \
    --api-key-file /path/to/onprem-api-key.txt

# Cloud server
python scripts/octopus-variable-export.py \
    --server-url https://mydomain.octopus.app \
    --api-key-file /path/to/cloud-api-key.txt
```

Each run produces a file like `octopus-onprem.mydomain.com.2026-03-20-143022-variables.yaml`.

### Export specific spaces only

```bash
python scripts/octopus-variable-export.py \
    --server-url https://octopus-onprem.mydomain.com \
    --api-key-file /path/to/key.txt \
    --space "Default" --space "Production"
```

### Share-safe export (hashed values)

When you need to share a variable export outside the team that owns the Octopus servers — with a consultant, a vendor, or just a wider audience inside your org — you usually don't want the raw values going with it. `--hash-values` replaces every variable value with its SHA-256 hash. Drift detection still works because the **same input always produces the same hash**: if two servers have the same value, they'll have the same hash; if a value changes, its hash changes.

#### Quick hashed export (no salt)

```bash
python scripts/variable-export.py \
    --server-url https://octopus-onprem.mydomain.com \
    --api-key-file /path/to/key.txt \
    --hash-values
```

Output goes to `…-variables-hashed.yaml`. Every `value:` becomes `sha256:<hex>`. Sensitive variables still appear as `==UNABLE_TO_DECODE==`.

**This alone is not very safe.** Hashing is one-way, but **low-entropy values are trivially recovered by brute force**. If a variable contains `Production`, `5432`, `true`, or any short/predictable string, an attacker can hash a list of candidate values and find the match in milliseconds. Variable *names* are also visible — `DatabasePort` next to `sha256:c775e7…` is a dead giveaway. Only high-entropy values (random passwords, GUIDs, long connection strings) are meaningfully protected by `--hash-values` on its own.

For anything you'd hesitate to email a stranger, use a salt as well — see below.

#### Recommended: hashed export with a salt

Adding `--salt-file PATH` switches the hashing to HMAC-SHA256, using the file's contents as the key. The same salt must be used on every server you want to compare. Without the salt, even short, predictable values like `Production` become unrecoverable. **With** the salt, anyone who has both the file *and* the salt can still brute-force low-entropy values — so treat the salt as a shared secret.

Workflow:

**Step 1 — Generate a salt once.** Use any source of cryptographic randomness; `openssl rand -hex 32` gives you 32 random bytes encoded as a 64-character hex string, which is plenty:

```bash
openssl rand -hex 32 > /path/to/shared-salt.txt
chmod 600 /path/to/shared-salt.txt
```

**Step 2 — Share the salt file securely with anyone who needs to compare exports.** Use whatever channel you'd use for any other shared secret — a password manager, an encrypted file share, age/SOPS, or in-person handover. Do **not** put it in the same place as the hashed exports; the whole point is that the two travel separately. Anyone who has both can recover low-entropy values.

**Step 3 — Run the export on every server using the same salt file.** Both runs must use byte-identical salt contents — otherwise identical values will hash differently and drift detection will produce false positives for every variable.

```bash
# On-prem server
python scripts/variable-export.py \
    --server-url https://octopus-onprem.mydomain.com \
    --api-key-file /path/to/onprem-api-key.txt \
    --hash-values --salt-file /path/to/shared-salt.txt

# Cloud server
python scripts/variable-export.py \
    --server-url https://mydomain.octopus.app \
    --api-key-file /path/to/cloud-api-key.txt \
    --hash-values --salt-file /path/to/shared-salt.txt
```

Each run produces a `…-variables-hashed.yaml` file. The two files can be safely shared with anyone you'd trust with the variable *names* (since names are not hashed) but not the values.

**Step 4 — Compare the two hashed exports.** No special flag is needed; `variable-compare.py` works on hashed files the same way it works on base64 files:

```bash
python scripts/variable-compare.py \
    octopus-onprem.mydomain.com.2026-03-20-143022-variables-hashed.yaml \
    mydomain.octopus.app.2026-03-20-143045-variables-hashed.yaml
```

Identical hashes mean identical inputs — variables match across the two servers. Different hashes mean different inputs — drift. Note that with hashed exports you can see *that* a value differs, but not *what* it differs to; if you need to inspect actual values you'll need an unhashed export.

#### Why the salt file matters

| Without `--salt-file` | With `--salt-file` |
|---|---|
| `Production` → `sha256:1f3df…` (same on every machine, in every export, forever) | `Production` → `sha256:7a2b9…` with one salt, `sha256:e4c11…` with another |
| Attacker hashes a wordlist offline and recovers low-entropy values in seconds | Attacker must obtain the salt before they can build a wordlist |
| Two unrelated organisations comparing notes can cross-reference values | Each organisation's hashes are independent |

A salt does **not** make hashed values "secret" — it makes them *unlinkable* across exports that use different salts. If you lose the salt along with the export, you're back to the no-salt threat model.

#### Common pitfalls

- **Different salts on different servers** — every variable will look different, even when nothing has changed. Symptom: a "diff" with hundreds of `Only in A` / `Only in B` entries. Fix: use the same `--salt-file` everywhere.
- **Editing the salt file** — even a stray newline changes the bytes and breaks comparison. Lock the file (`chmod 444`) once it's set, or store it in a read-only secret store.
- **Treating the hashed export as fully safe** — variable *names* are not hashed. If `DatabaseAdminPassword` shows up in your file, the existence of that variable is now public; only its value is protected.
- **Losing the salt** — without the salt you cannot re-export and produce matching hashes, so any later comparison will be useless. Back up the salt file alongside (but separately from) your secrets.

### Compare two exports

```bash
python scripts/variable-compare.py \
    octopus-onprem.mydomain.com.2026-03-20-143022-variables.yaml \
    mydomain.octopus.app.2026-03-20-143045-variables.yaml
```

Output is grouped into "Only in A", "Only in B", and "Differing values" sections, each labelled with the full path (e.g. `Default > library_variable_sets > set-name > variables > VarName`). Entries that exist in both files but appear in a different list position are treated as equal and not reported.

Options:
- `--decode` — Decode base64 variable values in memory before comparison or display
- `--label-a` / `--label-b` — Custom labels in the report (e.g. `--label-a "On-Prem" --label-b "Cloud"`)
- `-o FILE` — Write the report to a file instead of stdout

Exit codes: `0` (no differences), `1` (differences found), `2` (usage/file error).

### Decode a single file

```bash
python scripts/variable-compare.py --decode onprem-variables.yaml
```

With one file and `--decode`, the script prints the YAML with base64-encoded values decoded — useful for spot-checking what's actually inside an export.

### Track changes over time

Run the export periodically and compare consecutive snapshots:

```bash
python scripts/variable-compare.py \
    mydomain.octopus.app.2026-03-18-090000-variables.yaml \
    mydomain.octopus.app.2026-03-21-093000-variables.yaml
```

## Windows Usage

The scripts work on Windows with Python 3.10+ installed. For ad-hoc comparison of YAML files, you can also use:

- **VS Code** — Open both files and use `File > Compare Active File With...` (line-by-line; will flag reordering as drift)
- **WinMerge** — Free, open-source diff tool: https://winmerge.org
- **Beyond Compare** — Commercial diff tool with excellent YAML support

For drift detection that ignores list ordering, prefer `variable-compare.py`.

## YAML Structure

```yaml
"Space Name":
  library_variable_sets:
    "Set Name":
      variables:
        "VariableName":
          - value: "base64-encoded-value"    # or "==UNABLE_TO_DECODE==" for secrets
            scope:
              environment: ["Production"]
              role: ["web-server"]
  projects:
    "Project Name":
      variables:
        "ConnectionString":
          - value: "base64-encoded-value"
            scope:
              environment: ["Staging"]
  tenants:
    "Tenant Name":
      variables:
        "TenantVar":
          - value: "base64-encoded-value"
            project: "Project Name"
            environment: "Production"
```

- Variable values are base64-encoded to safely handle multi-line strings and special characters
- With `--hash-values`, values are replaced by `sha256:<hex>` (or HMAC-SHA256 under `--salt-file`) and the filename gains a `-hashed` suffix
- Sensitive variables show `==UNABLE_TO_DECODE==` (the API does not return their values) — this marker is preserved as-is in hashed exports
- All names and scopes are sorted alphabetically for deterministic diff output
- Scope IDs are resolved to human-readable names (environment names, machine names, etc.)
