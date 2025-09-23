#!/usr/bin/env python3
"""
noshutter.py
========================

Enable all GigabitEthernet interfaces on Cisco XRd routers in a Containerlab lab.
Enable LLDP globally on Cisco XRd routers in a Containerlab lab.

Steps
-----
1. Run `containerlab inspect -f json`
2. Parse node names and kinds
3. For each XRd router:
   - Log in via SSH (scrapli)
   - Run `show ip int brief`
   - Find all `GigabitEthernet` interfaces
   - Configure `no shutdown` on each
   - enable lldp
   - Commit and exit

Requirements
------------
- Python 3.8+
- Scrapli (`pip install scrapli`)
- Containerlab installed and working
"""

import json
import subprocess
from scrapli import Scrapli


def run_containerlab_inspect() -> list:
    """Run `containerlab inspect` and return list of nodes."""
    result = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    # Take first value (lab name key)
    return list(data.values())[0]


def enable_xrd_interfaces(host: str, username: str, password: str):
    """Login to XRd router, enable GigabitEthernet interfaces, commit, and exit."""
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
    )
    conn.open()

    # Show interfaces
    result = conn.send_command("show ip int brief")
    gig_ints = []
    for line in result.result.splitlines():
        if "GigabitEthernet" in line:
            iface = line.split()[0]
            gig_ints.append(iface)

    if not gig_ints:
        print(f"⚠️ No GigabitEthernet interfaces found on {host}")
        conn.close()
        return

    # Enter config and apply no shutdown
    for iface in gig_ints:
        conn.send_configs([
            f"interface {iface}",
            "no shutdown",
            f"description {iface} - tbc"
            #"no shutdown",
            #"commit"
        ])
        print(f"✅ Enabled {iface} on {host}")

    # Commit
    conn.send_config("commit")
    conn.close()


def enable_lldp(host: str, username: str, password: str):
    """Login to XRd router, enable LLDP, commit, and exit."""
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
    )
    conn.open()

    # Enable LLDP globally
    conn.send_configs(["lldp"])
    print("✅ LLDP enabled globally.")

    # Commit
    conn.send_config("commit")
    conn.close()


def main():
    nodes = run_containerlab_inspect()
    for node in nodes:
        name = node.get("name")
        kind = node.get("kind")
        if kind == "cisco_xrd":
            print(f"📡 Configuring {name} ({kind})...")
            enable_xrd_interfaces(
                host=name,
                username="clab",
                password="clab@123",
            )
            print(f"📡 Enabling LLDP {name} ({kind})...")
            enable_lldp(
                host=name,
                username="clab",
                password="clab@123",
            )

if __name__ == "__main__":
    main()
