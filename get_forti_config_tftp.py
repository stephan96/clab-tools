#!/usr/bin/env python3
"""
get_fortigate_config.py
=======================

Retrieve and save running configuration from FortiGate devices in a Containerlab
environment using local TFTP backup.

Steps:
1. Verify TFTP server (tftpd-hpa) is running locally.
2. Parse its configuration for IP and directory.
3. Use Scrapli to connect to each FortiGate.
4. Execute `execute backup config tftp <hostname>.cfg <tftp_ip>`.
5. Move resulting file from the TFTP directory to lab node's config directory.
6. Optionally update the topology file with startup-config entries.

Author: Stephan Baenisch
"""

import subprocess
import os
import yaml
import shutil
import time
from datetime import datetime
from pathlib import Path
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException


# ---------------------------------------------------------------------
# Containerlab & Node Helpers
# ---------------------------------------------------------------------

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
    lab_name = list(data.keys())[0]
    for node in data[lab_name]:
        if "fortigate" in node.get("kind", "").lower():
            nodes.append(node)
    return nodes, lab_name


# ---------------------------------------------------------------------
# TFTP Server Handling
# ---------------------------------------------------------------------

def check_tftp_server():
    """Check if tftpd-hpa is running and parse IP + directory."""
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", "tftpd-hpa"], check=True)
    except subprocess.CalledProcessError:
        print("❌ TFTP server not active or missing.")
        print("""
To install and configure minimal TFTP server:

sudo apt install -y tftpd-hpa
sudo vi /etc/default/tftpd-hpa

TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure --create"

sudo mkdir -p /srv/tftp
sudo chown -R tftp:tftp /srv/tftp
sudo chmod -R 755 /srv/tftp
sudo systemctl restart tftpd-hpa
sudo systemctl enable tftpd-hpa
""")
        return None, None

    # Parse /etc/default/tftpd-hpa
    tftp_ip = None
    tftp_dir = None
    with open("/etc/default/tftpd-hpa") as f:
        for line in f:
            if line.startswith("TFTP_ADDRESS="):
                val = line.split("=")[1].strip().strip('"')
                tftp_ip = val.split(":")[0]
            elif line.startswith("TFTP_DIRECTORY="):
                tftp_dir = line.split("=")[1].strip().strip('"')

    if not tftp_ip or not tftp_dir:
        print("⚠️ Could not determine TFTP IP or directory.")
        return None, None

    print(f"✅ Detected TFTP server {tftp_ip}, directory {tftp_dir}")
    return tftp_ip, Path(tftp_dir)


# ---------------------------------------------------------------------
# FortiGate Config Retrieval
# ---------------------------------------------------------------------

def fetch_fortigate_config_tftp(host: str, node_name: str, username: str, password: str,
                                tftp_ip: str, tftp_dir: Path) -> str:
    """
    Connect to FortiGate and trigger TFTP backup command.
    Returns the local path of the saved configuration.
    """
    tftp_filename = f"{node_name}.cfg"
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
        cmd = f"execute backup config tftp {tftp_filename} {tftp_ip}"
        print(f"📡 Sending: {cmd}")
        response = conn.send_command(cmd)
        #conn.close()
        if "OK" not in response.result:
            print(f"⚠️ TFTP backup command did not confirm success for {node_name}")
            return ""

        # Wait briefly for file to appear
        tftp_file = tftp_dir / tftp_filename
        for _ in range(20):  # wait up to ~10 seconds
            if tftp_file.exists():
                return str(tftp_file)
            time.sleep(0.5)

        print(f"❌ No TFTP file received for {node_name}.")
        return ""
    except ScrapliException as e:
        print(f"❌ Failed to connect to {host}: {e}")
        return ""
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Config File Handling
# ---------------------------------------------------------------------

def save_config(labname: str, node: str, tftp_path: str, max_backups: int = 5):
    """
    Move config from TFTP directory into lab folder.
    Keep up to `max_backups` rotated backups.
    """
    config_dir = os.path.join(f"clab-{labname}", node, "config")
    os.makedirs(config_dir, exist_ok=True)

    dest = os.path.join(config_dir, f"{node}.cfg")

    # Rotate existing backups
    for i in range(max_backups, 0, -1):
        bak = f"{dest}.bak{i}"
        prev = f"{dest}.bak{i-1}" if i > 1 else dest
        if os.path.exists(prev):
            os.rename(prev, bak)

    # Move new config
    if tftp_path and os.path.exists(tftp_path):
        shutil.move(tftp_path, dest)
        print(f"✅ Saved config for {node} -> {dest}")
        return dest
    else:
        print(f"❌ Missing TFTP file for {node}")
        return ""


# ---------------------------------------------------------------------
# Topology Update
# ---------------------------------------------------------------------

def backup_topology(topology_file: str):
    """Backup topology file before modification."""
    backup_file = topology_file + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    subprocess.run(["cp", topology_file, backup_file], check=True)
    print(f"📦 Backup created: {backup_file}")


def update_topology(labname: str, nodes: list, config_map: dict):
    """Update topology file with startup-config entries for FortiGate nodes."""
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

    print(f"🔄 Topology updated: {topology_file}")


# ---------------------------------------------------------------------
# Main Logic
# ---------------------------------------------------------------------

def main():
    """Main execution workflow."""
    tftp_ip, tftp_dir = check_tftp_server()
    if not tftp_ip:
        return 1

    lab_data = run_containerlab_inspect()
    nodes, labname = get_fortigate_nodes(lab_data)

    if not nodes:
        print("⚠️ No FortiGate nodes found.")
        return 1

    do_update = input("🧩 Update topology with startup-config entries (y/N): ").strip().lower() == "y"

    config_map = {}
    for node in nodes:
        host = node["ipv4_address"].split("/")[0]
        name = node["name"]
        print(f"▶ Backing up config from {name} ({host}) via TFTP...")

        tftp_file = fetch_fortigate_config_tftp(
            host=host,
            node_name=name,
            username="admin",
            password="admin",
            tftp_ip=tftp_ip,
            tftp_dir=tftp_dir,
        )

        if tftp_file:
            path = save_config(labname, name, tftp_file)
            config_map[name] = path

    if config_map and do_update:
        update_topology(labname, nodes, config_map)

    print("✅ All FortiGate configurations processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

