#!/usr/bin/env python3
"""
get_clab_config.py
===================

Retrieve and save running configurations from Containerlab network devices
(Huawei VRP and Cisco XRd), and update the lab topology file with startup-config
entries.

Overview
--------
This script automates configuration backup for network labs created with
[Containerlab](https://containerlab.dev/). It performs the following steps:

1. Runs `containerlab inspect` in the current lab directory.
2. Parses the device list, identifying Huawei VRP and Cisco XRd nodes.
3. Connects via SSH to each device using [Scrapli](https://carlmontanari.github.io/scrapli/),
   executes the appropriate command to retrieve the running configuration, and saves
   it in the correct `config/` directory for each node.
4. Creates a backup of the lab's `.clab.yml` topology file.
5. Inserts a `startup-config` entry for each device, pointing to the saved configuration file.

Supported Device Types
----------------------
- **Huawei VRP**
  - Username: `admin`
  - Password: `admin`
  - Command: `display current-configuration`
- **Cisco XRd**
  - Username: `clab`
  - Password: `clab@123`
  - Command: `show running-config`

Requirements
------------
- Python 3.8+
- [Scrapli](https://pypi.org/project/scrapli/)
- [PyYAML](https://pypi.org/project/PyYAML/)
- Containerlab installed and working
- SSH connectivity to all lab devices (container host must be able to resolve container names)

Usage
-----
From inside your Containerlab lab directory:

    python3 get_clab_config.py

Example:

    cd /home/user/containerlabs/hui-xrd-test-1
    python3 get_clab_config.py

After running, configurations will be saved to:

    clab-<labname>/<node>/config/<node>.cfg

And your `<labname>.clab.yml` will be updated with `startup-config` entries.
A backup of the original `.clab.yml` will be created with the `.bak` extension.

Author
------
Stephan Baenisch <stephan@baenisch.de>
"""

import os
import re
import subprocess
import yaml
from scrapli import Scrapli
from datetime import datetime
from pathlib import Path
import shutil

# Default credentials
CREDENTIALS = {
    "huawei_vrp": ("admin", "admin"),
    "cisco_xrd": ("clab", "clab@123"),
}

# Commands to retrieve running config
COMMANDS = {
    "huawei_vrp": "display current-configuration",
    "cisco_xrd": "show running-config",
}

def run_containerlab_inspect():
    result = subprocess.run(
        ["containerlab", "inspect"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"containerlab inspect failed:\n{result.stderr}")
    return result.stdout

def parse_containerlab_output(output):
    devices = []
    lines = output.splitlines()
    for line in lines:
        # Match table rows with first two columns filled
        m = re.match(r"│\s*([^\|]+?)\s*│\s*([^\|]+?)\s*│", line)
        if m:
            name = m.group(1).strip()
            kind = m.group(2).strip()
            if kind in ("huawei_vrp", "cisco_xrd"):
                devices.append({"name": name, "kind": kind})
    return devices


def scrapli_get_config(host, platform, username, password, command):
    device = {
        "host": host,
        "auth_username": username,
        "auth_password": password,
        "auth_strict_key": False,
        "platform": platform,
    }
    conn = Scrapli(**device)
    conn.open()
    result = conn.send_command(command)
    conn.close()
    return result.result

def platform_mapping(kind):
    if kind == "huawei_vrp":
        return "huawei_vrp"
    elif kind == "cisco_xrd":
        return "cisco_iosxr"
    else:
        raise ValueError(f"Unsupported kind: {kind}")

def update_topology_file(lab_folder, devices):
    yml_file = next(Path(lab_folder).glob("*.clab.yml"))
    backup_file = yml_file.with_suffix(".clab.yml.bak")
    shutil.copy(yml_file, backup_file)

    with open(yml_file) as f:
        topology = yaml.safe_load(f)

    for dev in devices:
        short_name = dev["short_name"]
        kind = dev["kind"]
        cfg_path = f"./clab-{Path(lab_folder).name}/{short_name}/config/{short_name}.cfg"
        if "startup-config" not in topology["topology"]["nodes"][short_name]:
            topology["topology"]["nodes"][short_name]["startup-config"] = cfg_path

    with open(yml_file, "w") as f:
        yaml.dump(topology, f, sort_keys=False)

def main():
    lab_folder = Path.cwd()
    lab_name = lab_folder.name

    print(f"🔍 Inspecting containerlab in {lab_folder}...")
    output = run_containerlab_inspect()
    devices = parse_containerlab_output(output)

    for dev in devices:
        # Extract short name: remove "clab-<labname>-" prefix
        short_name = dev["name"].replace(f"clab-{lab_name}-", "")
        dev["short_name"] = short_name

        host = dev["name"]  # Resolvable name
        kind = dev["kind"]
        platform = platform_mapping(kind)
        username, password = CREDENTIALS[kind]
        command = COMMANDS[kind]

        cfg_dir = lab_folder / f"clab-{lab_name}" / short_name / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / f"{short_name}.cfg"

        print(f"📡 Fetching config from {dev['name']} ({kind})...")
        try:
            config_text = scrapli_get_config(host, platform, username, password, command)
            cfg_file.write_text(config_text)
            print(f"✅ Saved config to {cfg_file}")
        except Exception as e:
            print(f"❌ Failed to get config from {dev['name']}: {e}")

    print("📝 Updating topology file...")
    update_topology_file(lab_folder, devices)
    print("✅ Topology file updated and backup created.")

if __name__ == "__main__":
    main()

