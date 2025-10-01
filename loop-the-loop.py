#!/usr/bin/env python3
"""
loop-the-loop.py
================

Automates Loopback0 interface provisioning for XRd routers in a Containerlab lab.

- Runs `containerlab inspect -f json`
- Parses the device list (name + kind)
- Logs into each XRd router via SSH using Scrapli
- Creates Loopback0 and configures IPv4/IPv6 addresses:
  - IPv4: from 1.1.1.0/24
  - IPv6: from fd00::/8, encoding the last IPv4 octet into the IPv6 suffix
- Address allocation scheme:
  - Core routers (name starts with "c"):      start at 1.1.1.1
  - Distribution routers (name starts with "d"): start at 1.1.1.51
  - Access routers (name starts with "a"):    start at 1.1.1.101
  - Service routers (name starts with "s"):   start at 1.1.1.151
  - Customer routers (name starts with "CE"): start at 1.1.1.201

Requirements:
- Python 3.8+
- scrapli
- containerlab in $PATH
"""

import subprocess
import json
import sys
from ipaddress import ip_network
from scrapli import Scrapli

# Default XRd credentials
XR_USERNAME = "clab"
XR_PASSWORD = "clab@123"


def run_containerlab_inspect() -> list:
    """Run `containerlab inspect -f json` and return parsed JSON."""
    result = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    # Flatten lab dict into list of nodes
    nodes = []
    for _, node_list in data.items():
        nodes.extend(node_list)
    return nodes


def get_base_ip(name: str) -> int:
    """Return base IPv4 last-octet based on router type."""
    if name.startswith("CE"):
        return 201
    elif name.startswith("s"):
        return 151
    elif name.startswith("a"):
        return 101
    elif name.startswith("d"):
        return 51
    elif name.startswith("c"):
        return 1
    else:
        raise ValueError(f"Unknown router type for {name}")


def configure_loopback(node: dict, ipv4_host: str, ipv6_host: str):
    """Push loopback config to XRd router."""
    raw_ip = node["ipv4_address"]
    host = raw_ip.split("/")[0]  # strip CIDR suffix
    print(f"📡 Configuring {node['name']} ({host}) ...")

    conn = Scrapli(
        host=host,
        auth_username=XR_USERNAME,
        auth_password=XR_PASSWORD,
        platform="cisco_iosxr",
        transport="paramiko",       # required for XRd
        auth_strict_key=False,
    )
    conn.open()

    configs = [
        "interface Loopback0",
        f"ipv4 address {ipv4_host} 255.255.255.255",
        f"ipv6 address {ipv6_host}/128",
    ]
    conn.send_configs(configs)
    conn.send_command("commit")
    conn.close()

    print(f"✅ Configured Loopback0 with {ipv4_host}, {ipv6_host}")



def main():
    try:
        nodes = run_containerlab_inspect()
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to run containerlab inspect: {e}")
        sys.exit(1)

    base_net_v4 = ip_network("1.1.1.0/24")
    base_net_v6 = ip_network("fd00::/8")

    counter = {
        "c": 0,
        "d": 0,
        "a": 0,
        "s": 0,
        "CE": 0,
    }

    for node in nodes:
        if node["kind"] != "cisco_xrd":
            continue

        base_octet = get_base_ip(node["name"])

        prefix = "CE" if node["name"].startswith("CE") else node["name"][0]
        offset = counter[prefix]
        counter[prefix] += 1

        last_octet = base_octet + offset
        if last_octet > 254:
            print(f"⚠️ Skipping {node['name']}, no more IPv4 addresses available")
            continue

        ipv4_addr = f"1.1.1.{last_octet}"
        ipv6_addr = f"fd00::{last_octet}"

        configure_loopback(node, ipv4_addr, ipv6_addr)

    print("✅ Done.")


if __name__ == "__main__":
    main()

