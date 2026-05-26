#!/usr/bin/env python3
"""Export all variables from an Octopus Deploy server to a structured YAML file.

Connects to a single Octopus Deploy instance and exports every variable
(library variable sets, project variables, tenant variables) into a
deterministic, diff-friendly YAML structure.

Usage:
    python octopus-variable-export.py --server-url https://octopus.example.com --api-key-file /path/to/key

Output:
    A file named [SERVER_URL].[YYYY]-[MM]-[DD]-[HHMMSS]-variables.yaml
    in the current directory (or --output-dir if specified).
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
import yaml


def sanitize_filename(url: str) -> str:
    """Convert a server URL into a safe filename prefix."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    port = parsed.port
    if port and port not in (80, 443):
        return f"{host}-{port}"
    return host


Encoder = Callable[[Optional[str]], str]


def make_encoder(hash_values: bool, salt: Optional[bytes] = None) -> Encoder:
    """Return a function that encodes a single variable value for the export.

    hash_values=False -> reversible base64 (default; readable with
                         variable-compare --decode).
    hash_values=True  -> one-way SHA-256 (or HMAC-SHA256 if a salt is provided)
                         prefixed with 'sha256:'. Drift detection still works
                         because identical inputs produce identical digests.

    NOTE: This is hashing, not encryption -- low-entropy values (env names,
    port numbers, small enums) remain recoverable by hashing candidates. A
    salt blocks pre-computed rainbow tables but does not protect against an
    attacker who also has the salt.
    """
    if hash_values:
        if salt is not None:
            def _hash(value: Optional[str]) -> str:
                if value is None:
                    return "==UNABLE_TO_DECODE=="
                digest = hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()
                return f"sha256:{digest}"
            return _hash

        def _plain_hash(value: Optional[str]) -> str:
            if value is None:
                return "==UNABLE_TO_DECODE=="
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            return f"sha256:{digest}"
        return _plain_hash

    def _b64(value: Optional[str]) -> str:
        if value is None:
            return "==UNABLE_TO_DECODE=="
        return base64.b64encode(value.encode("utf-8")).decode("ascii")
    return _b64


class OctopusClient:
    """Minimal Octopus Deploy API client."""

    def __init__(self, server_url: str, api_key: str):
        self.base_url = server_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-Octopus-ApiKey"] = api_key

    def get(self, path: str) -> dict | list:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_all_pages(self, path: str) -> list:
        """Fetch all pages of a paginated Octopus API endpoint."""
        items = []
        while path:
            data = self.get(path)
            if isinstance(data, list):
                return data
            items.extend(data.get("Items", []))
            # Octopus pagination uses Links.Page.Next
            links = data.get("Links", {})
            next_link = links.get("Page.Next")
            if next_link:
                path = next_link
            else:
                break
        return items

    def get_spaces(self) -> list[dict]:
        return self.get_all_pages("/api/spaces")

    def get_library_variable_sets(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/libraryvariablesets/all")

    def get_variables(self, space_id: str, variable_set_id: str) -> dict:
        return self.get(f"/api/{space_id}/variables/{variable_set_id}")

    def get_projects(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/projects/all")

    def get_tenants(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/tenants/all")

    def get_tenant_variables(self, space_id: str, tenant_id: str) -> dict:
        return self.get(f"/api/{space_id}/tenants/{tenant_id}/variables")

    def get_environments(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/environments/all")

    def get_channels(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/channels/all")

    def get_machines(self, space_id: str) -> list[dict]:
        return self.get_all_pages(f"/api/{space_id}/machines/all")

    def get_machine_roles(self, space_id: str) -> list[str]:
        """Fetch all machine roles (target tags) in a space."""
        return self.get(f"/api/{space_id}/machineroles/all")

    def get_deployment_processes(self, space_id: str, process_id: str) -> dict:
        return self.get(f"/api/{space_id}/deploymentprocesses/{process_id}")


def build_scope_lookup(client: OctopusClient, space_id: str) -> dict:
    """Build ID-to-name lookup tables for scope resolution."""
    lookup = {"Environment": {}, "Machine": {}, "Channel": {}, "Role": {}}

    for env in client.get_environments(space_id):
        lookup["Environment"][env["Id"]] = env["Name"]

    for machine in client.get_machines(space_id):
        lookup["Machine"][machine["Id"]] = machine["Name"]

    for channel in client.get_channels(space_id):
        lookup["Channel"][channel["Id"]] = channel["Name"]

    return lookup


def resolve_scope(scope_values: dict, lookup: dict) -> dict:
    """Convert a variable's ScopeValues from IDs to human-readable names."""
    resolved = {}
    for scope_type, ids in sorted(scope_values.items()):
        if not ids:
            continue
        if scope_type in lookup:
            names = sorted(lookup[scope_type].get(id_, id_) for id_ in ids)
        elif scope_type == "Role":
            # Roles are already names, not IDs
            names = sorted(ids)
        elif scope_type == "Action":
            # Actions are step IDs — keep as-is (resolving would need deployment process)
            names = sorted(ids)
        elif scope_type == "ProcessOwner":
            # Skip internal scope types
            continue
        else:
            names = sorted(ids)
        resolved[scope_type.lower()] = names
    return resolved


def format_variable(var: dict, lookup: dict, encode: Encoder) -> dict:
    """Format a single Octopus variable into our YAML structure."""
    entry = {}

    if var.get("IsSensitive"):
        entry["value"] = "==UNABLE_TO_DECODE=="
    else:
        entry["value"] = encode(var.get("Value"))

    scope_values = var.get("Scope", {})
    scope = resolve_scope(scope_values, lookup)
    entry["scope"] = scope if scope else {}

    return entry


def extract_variables(variable_set: dict, lookup: dict, encode: Encoder) -> dict:
    """Extract variables from a variable set response into our YAML structure."""
    variables: dict[str, list] = {}

    for var in variable_set.get("Variables", []):
        name = var.get("Name", "")
        if not name:
            continue
        entry = format_variable(var, lookup, encode)
        variables.setdefault(name, []).append(entry)

    # Sort entries within each variable by scope for deterministic output
    for name in variables:
        variables[name].sort(key=lambda e: str(e.get("scope", {})))

    return dict(sorted(variables.items()))


def export_space(client: OctopusClient, space: dict, encode: Encoder) -> dict:
    """Export all variables from a single space."""
    space_id = space["Id"]
    space_name = space["Name"]
    print(f"  Processing space: {space_name} ({space_id})")

    # Build scope lookup for this space
    print("    Building scope lookup tables...")
    lookup = build_scope_lookup(client, space_id)

    space_data = {
        "library_variable_sets": {},
        "projects": {},
        "tenants": {},
    }

    # Library variable sets
    print("    Exporting library variable sets...")
    lib_sets = client.get_library_variable_sets(space_id)
    for lib_set in lib_sets:
        set_name = lib_set["Name"]
        var_set_id = lib_set.get("VariableSetId")
        if not var_set_id:
            continue
        try:
            variable_set = client.get_variables(space_id, var_set_id)
            variables = extract_variables(variable_set, lookup, encode)
            if variables:
                space_data["library_variable_sets"][set_name] = {
                    "variables": variables
                }
        except requests.HTTPError as e:
            print(f"    WARNING: Failed to fetch variables for set '{set_name}': {e}")

    # Projects
    print("    Exporting project variables...")
    projects = client.get_projects(space_id)
    for project in projects:
        project_name = project["Name"]
        var_set_id = project.get("VariableSetId")
        if not var_set_id:
            continue
        try:
            variable_set = client.get_variables(space_id, var_set_id)
            variables = extract_variables(variable_set, lookup, encode)
            if variables:
                space_data["projects"][project_name] = {"variables": variables}
        except requests.HTTPError as e:
            print(f"    WARNING: Failed to fetch variables for project '{project_name}': {e}")

    # Tenants
    print("    Exporting tenant variables...")
    tenants = client.get_tenants(space_id)
    for tenant in tenants:
        tenant_name = tenant["Name"]
        tenant_id = tenant["Id"]
        try:
            tenant_vars = client.get_tenant_variables(space_id, tenant_id)
            tenant_data = {}

            # Tenant project variables
            project_vars = tenant_vars.get("ProjectVariables", {})
            for proj_id, proj_data in project_vars.items():
                proj_name = proj_data.get("ProjectName", proj_id)
                for var_name, env_values in proj_data.get("Variables", {}).items():
                    for env_id, value in env_values.items():
                        env_name = lookup["Environment"].get(env_id, env_id)
                        entry = {
                            "value": encode(value),
                            "project": proj_name,
                            "environment": env_name,
                        }
                        tenant_data.setdefault(var_name, []).append(entry)

            # Library variable values for tenants
            lib_vars = tenant_vars.get("LibraryVariables", {})
            for lib_id, lib_data in lib_vars.items():
                lib_name = lib_data.get("LibraryVariableSetName", lib_id)
                for var_name, env_values in lib_data.get("Variables", {}).items():
                    for env_id, value in env_values.items():
                        env_name = lookup["Environment"].get(env_id, env_id)
                        entry = {
                            "value": encode(value),
                            "library_variable_set": lib_name,
                            "environment": env_name,
                        }
                        tenant_data.setdefault(var_name, []).append(entry)

            if tenant_data:
                # Sort entries and variable names
                for var_name in tenant_data:
                    tenant_data[var_name].sort(
                        key=lambda e: (
                            e.get("project", ""),
                            e.get("library_variable_set", ""),
                            e.get("environment", ""),
                        )
                    )
                space_data["tenants"][tenant_name] = {
                    "variables": dict(sorted(tenant_data.items()))
                }
        except requests.HTTPError as e:
            print(f"    WARNING: Failed to fetch variables for tenant '{tenant_name}': {e}")

    # Sort all sections
    space_data["library_variable_sets"] = dict(
        sorted(space_data["library_variable_sets"].items())
    )
    space_data["projects"] = dict(sorted(space_data["projects"].items()))
    space_data["tenants"] = dict(sorted(space_data["tenants"].items()))

    # Remove empty sections
    return {k: v for k, v in space_data.items() if v}


def main():
    parser = argparse.ArgumentParser(
        description="Export all variables from an Octopus Deploy server to YAML"
    )
    parser.add_argument(
        "--server-url",
        required=True,
        help="Octopus Deploy server URL (e.g. https://octopus.example.com)",
    )
    parser.add_argument(
        "--api-key-file",
        required=True,
        help="Path to file containing the Octopus API key",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to write the output file (default: current directory)",
    )
    parser.add_argument(
        "--space",
        action="append",
        dest="spaces",
        help="Space name to export (can be repeated; default: all spaces)",
    )
    parser.add_argument(
        "--hash-values",
        action="store_true",
        help=(
            "Replace variable values with their SHA-256 hash so the export "
            "can be shared without exposing actual values. Drift detection "
            "still works (identical inputs produce identical hashes). NOTE: "
            "this is hashing, not encryption -- low-entropy values (env "
            "names, ports, small enums) remain recoverable by brute force "
            "unless --salt-file is also used."
        ),
    )
    parser.add_argument(
        "--salt-file",
        default=None,
        help=(
            "Path to a file whose contents are used as a salt (HMAC-SHA256 "
            "key) when hashing values. The same salt must be used on every "
            "server being compared. Requires --hash-values. Reading from a "
            "file rather than the command line avoids leaking the salt via "
            "process listings."
        ),
    )
    args = parser.parse_args()

    if args.salt_file and not args.hash_values:
        parser.error("--salt-file requires --hash-values")

    salt: Optional[bytes] = None
    if args.salt_file:
        salt_path = Path(args.salt_file).expanduser()
        if not salt_path.exists():
            print(f"Error: salt file not found: {salt_path}", file=sys.stderr)
            sys.exit(1)
        salt = salt_path.read_bytes().rstrip(b"\r\n")
        if not salt:
            print(f"Error: salt file is empty: {salt_path}", file=sys.stderr)
            sys.exit(1)

    encode = make_encoder(hash_values=args.hash_values, salt=salt)

    if args.hash_values:
        msg = (
            "WARNING: --hash-values replaces variable values with SHA-256 "
            "hashes. This is ONE-WAY (hashing, not encryption) and only "
            "protects high-entropy values; environment names, port numbers, "
            "and similar low-entropy values can be recovered by hashing "
            "candidates."
        )
        if salt is not None:
            msg += (
                " A salt is in use (HMAC-SHA256) which blocks pre-computed "
                "rainbow tables, but an attacker who also has the salt can "
                "still brute-force low-entropy values."
            )
        else:
            msg += " Consider --salt-file to block rainbow-table attacks."
        print(msg, file=sys.stderr)

    # Read API key from file. Supports two formats:
    #   1. raw key on a single line ("API-XXXX...")
    #   2. structured file containing a "key: API-XXXX..." line alongside
    #      metadata (Created/Expires/User/Usage etc.)
    key_path = Path(args.api_key_file).expanduser()
    if not key_path.exists():
        print(f"Error: API key file not found: {key_path}", file=sys.stderr)
        sys.exit(1)
    key_raw = key_path.read_text()
    api_key = None
    for line in key_raw.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("key:"):
            api_key = stripped.split(":", 1)[1].strip()
            break
    if not api_key:
        api_key = key_raw.strip()

    client = OctopusClient(args.server_url, api_key)

    # Test connectivity
    print(f"Connecting to {args.server_url}...")
    try:
        server_info = client.get("/api")
        version = server_info.get("Version", "unknown")
        print(f"Connected. Server version: {version}")
    except requests.ConnectionError:
        print(f"Error: Cannot connect to {args.server_url}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"Error: API request failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Get spaces
    all_spaces = client.get_spaces()
    if args.spaces:
        spaces = [s for s in all_spaces if s["Name"] in args.spaces]
        missing = set(args.spaces) - {s["Name"] for s in spaces}
        if missing:
            print(
                f"Warning: spaces not found: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
    else:
        spaces = all_spaces

    if not spaces:
        print("No spaces to process.", file=sys.stderr)
        sys.exit(1)

    print(f"Exporting variables from {len(spaces)} space(s)...")

    # Build the full export
    export_data = {}
    for space in sorted(spaces, key=lambda s: s["Name"]):
        space_name = space["Name"]
        space_data = export_space(client, space, encode)
        if space_data:
            export_data[space_name] = space_data

    # Generate output filename
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    server_prefix = sanitize_filename(args.server_url)
    suffix = "-hashed" if args.hash_values else ""
    filename = f"{server_prefix}.{timestamp}-variables{suffix}.yaml"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    # Write YAML
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            export_data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,  # We pre-sort everything
            width=120,
        )

    print(f"\nExported to: {output_path}")
    print(f"Spaces: {len(export_data)}")
    total_vars = 0
    for space_data in export_data.values():
        for section in space_data.values():
            for group in section.values():
                total_vars += len(group.get("variables", {}))
    print(f"Total variable groups: {total_vars}")


if __name__ == "__main__":
    main()
