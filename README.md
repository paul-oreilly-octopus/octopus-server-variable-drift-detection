# Variable Drift Detection

Detects variable drift between two Octopus Deploy servers, or one server over time. Can be used during migration from on-prem to cloud.

## How It Works

Two Python scripts, used independently:

1. **`variable-export.py`** — Connects to one Octopus server and exports all variables (library variable sets, project variables, tenant variables) to a structured, deterministic YAML file.
2. **`variable-diff.py`** — Compares two YAML exports and shows differences in unified diff format.

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

### Compare two exports

```bash
python scripts/octopus-variable-diff.py \
    octopus-onprem.mydomain.com.2026-03-20-143022-variables.yaml \
    mydomain.octopus.app.2026-03-20-143045-variables.yaml
```

Options:
- `--label-a` / `--label-b` — Custom labels in diff output (e.g. `--label-a "On-Prem" --label-b "Cloud"`)
- `--context-lines N` — Lines of context around changes (default: 3)
- `-o FILE` — Write diff to a file instead of stdout

### Track changes over time

Run the export periodically and diff consecutive snapshots:

```bash
python scripts/octopus-variable-diff.py \
    mydomain.octopus.app.2026-03-18-090000-variables.yaml \
    mydomain.octopus.app.2026-03-21-093000-variables.yaml
```

## Windows Usage

The scripts work on Windows with Python 3.10+ installed. For comparing YAML files, you can also use:

- **VS Code** — Open both files and use `File > Compare Active File With...`
- **WinMerge** — Free, open-source diff tool: https://winmerge.org
- **Beyond Compare** — Commercial diff tool with excellent YAML support
- **PowerShell** — `Compare-Object (Get-Content file1.yaml) (Get-Content file2.yaml)`

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
- Sensitive variables show `==UNABLE_TO_DECODE==` (the API does not return their values)
- All names and scopes are sorted alphabetically for deterministic diff output
- Scope IDs are resolved to human-readable names (environment names, machine names, etc.)
