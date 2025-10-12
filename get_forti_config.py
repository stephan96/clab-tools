#!/usr/bin/env python3
"""
get_fortigate_config.py
=======================

-> Better use the get_fortigate_config_tftp.py
-> It is more reliable when backing up large configurations
-> scrapli based backup sometimes cuts parts of the configuration

Retrieve and save running configuration from FortiGate devices in a Containerlab
environment, and update the lab topology file with startup-config entries (optional).

Overview
--------
This script:
1. Runs `containerlab inspect` in the current lab directory.
2. Identifies FortiGate nodes from the device list.
3. Connects via SSH (paramiko) using Scrapli.
4. Executes `show` to retrieve the running config.
5. Saves the configuration to `clab-<labname>/<node>/config/<node>.cfg`.
6. Updates the `<labname>.clab.yml` with `startup-config` entries (startup-config not yet implemented for Fortigate in Containerlab)
7. Creates a backup of the original `.clab.yml`.

Requirements
------------
- Python 3.8+
- [Scrapli](https://pypi.org/project/scrapli/)
- [PyYAML](https://pypi.org/project/PyYAML/)
- Containerlab installed and working
- SSH connectivity to FortiGate nodes

Usage
-----
From inside your Containerlab lab directory:

    get_fortigate_config.py

Author
------
Stephan Baenisch
"""

import subprocess
import os
import yaml
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException
from datetime import datetime


def run_containerlab_inspect():
    """Run `containerlab inspect` and return parsed JSON (dict)."""
    result = subprocess.run(
        ["containerlab", "inspect", "--format", "json"],
        capture_output=True, text=True, check=True
    )
    data = yaml.safe_load(result.stdout)
    return data


def get_fortigate_nodes(data: dict):
    """Extract FortiGate nodes from containerlab inspect output."""
    nodes = []
    # The top-level key is the lab name
    lab_name = list(data.keys())[0]
    for node in data[lab_name]:
        if "fortigate" in node.get("kind", "").lower():
            nodes.append(node)
    return nodes, lab_name


def fetch_fortigate_config(host: str, username: str, password: str) -> str:
    """Connect to FortiGate via SSH and retrieve running config."""
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        auth_strict_key=False,
        platform="fortinet_fortios",
        transport="paramiko",
    )
    try:
        conn.open()
        #response = conn.send_command("show full-configuration")
        response = conn.send_command("show")
        return response.result
    except ScrapliException as e:
        print(f"❌ Failed to fetch config from {host}: {e}")
        return ""
    finally:
        conn.close()


def save_config(labname: str, node: str, config: str, max_backups: int = 5):
    """
    Save device config to lab config directory.
    - Keeps existing configs by rotating them:
      fg1.cfg → fg1.cfg.bak1 → fg1.cfg.bak2 → ...
    - The latest config always remains fg1.cfg.
    - Keeps up to `max_backups` backup versions.
    """
    config_dir = os.path.join(f"clab-{labname}", node, "config")
    os.makedirs(config_dir, exist_ok=True)

    base_path = os.path.join(config_dir, f"{node}.cfg")

    # Rotate backups
    for i in range(max_backups, 0, -1):
        bak_path = f"{base_path}.bak{i}"
        prev_bak = f"{base_path}.bak{i-1}" if i > 1 else base_path
        if os.path.exists(prev_bak):
            os.rename(prev_bak, bak_path)
            #print(f"🔁 Rotated: {prev_bak} → {bak_path}")

    # Save new config as the main file
    with open(base_path, "w") as f:
        f.write(config)

    print(f"✅ Saved current config for {node} -> {base_path}")
    return base_path


def backup_topology(topology_file: str):
    """Backup topology file before modification."""
    backup_file = topology_file + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    subprocess.run(["cp", topology_file, backup_file], check=True)
    print(f"📦 Backup created: {backup_file}")


def update_topology(labname: str, nodes: list, config_map: dict):
    """Update topology file with startup-config entries for FortiGate nodes (with automatic backup)."""
    topology_file = f"{labname}.clab.yml"
    backup_topology(topology_file)

    with open(topology_file, "r") as f:
        topo = yaml.safe_load(f)

    nodes_section = topo.get("topology", {}).get("nodes", {})
    for nodename, node in nodes_section.items():
        if "fortigate" in node.get("kind", "").lower() and nodename in config_map:
            node["startup-config"] = config_map[nodename]
            print(f"🧩 Added startup-config for {nodename}")

    with open(topology_file, "w") as f:
        yaml.safe_dump(topo, f, sort_keys=False)

    print(f"🔄 Topology updated (backup created): {topology_file}")


def main():
    """Main execution workflow."""
    lab_data = run_containerlab_inspect()
    nodes, labname = get_fortigate_nodes(lab_data)

    if not nodes:
        print("⚠️ No FortiGate nodes found.")
        return 1

    # Ask once whether to update topology (backup included)
    do_update = input("🧩 Update topology with startup-config entries (a backup will be created)? (y/N): ").strip().lower() == "y"

    config_map = {}
    for node in nodes:
        host = node["ipv4_address"].split("/")[0]
        name = node["name"]
        print(f"▶ Fetching config from {name} ({host})...")
        config = fetch_fortigate_config(
            host=host,
            username="admin",  # Default FortiGate username
            password="admin"   # Default password (adjust as needed)
        )
        if config:
            path = save_config(labname, name, config)
            config_map[name] = path

    if config_map and do_update:
        update_topology(labname, nodes, config_map)

    return 0



if __name__ == "__main__":
    raise SystemExit(main())

