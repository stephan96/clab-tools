#!/usr/bin/env python3
#!/usr/bin/env python3
"""
loop-the-loop.py
================

Configure Loopback0 interfaces automatically on Containerlab routers.

Overview
--------
This script inspects a running Containerlab lab, connects to all Cisco XRd routers,
and configures a `Loopback0` interface with unique IPv4 and IPv6 addresses.

Addressing Scheme
-----------------
- **Core route reflectors (crr\*)** → start at `1.1.1.1`
- **Core routers (c\*, except crr)** → start at `1.1.1.10`
- **Distribution routers (d\*)** → start at `1.1.1.30`
- **Access routers (a\*)** → start at `1.1.1.60`
- **Service routers (s\*)** → start at `1.1.1.90`
- **Customer routers (CE\*)** → start at `1.1.1.120`
- **Other routers (no match)** → start at `1.1.1.150`
- **Fallback mode**: If no categories match at all, addresses are assigned sequentially
  from `1.1.1.1` upward, ignoring categories.

IPv6 Addressing
---------------
Each router additionally receives an IPv6 loopback address from the `fd00::/8` space.
The last IPv4 octet is encoded into the IPv6 address, e.g.:

- IPv4: `1.1.1.5` → IPv6: `fd00::5`
- IPv4: `1.1.1.120` → IPv6: `fd00::120`

Usage
-----
From inside your Containerlab lab directory:

    ./loop-the-loop.py

The script will:
1. Run `containerlab inspect -f json`
2. Parse all XRd routers
3. Assign and configure loopback addresses
4. Commit the configuration

Author
------
Stephan
"""

import json
import subprocess
from scrapli import Scrapli

XR_USERNAME = "clab"
XR_PASSWORD = "clab@123"

# Define IP pools per router type
POOLS = {
    "crr": {"start": 1, "counter": 0},     # Core Route Reflectors
    "c":   {"start": 10, "counter": 0},    # Core routers
    "d":   {"start": 30, "counter": 0},    # Distribution
    "a":   {"start": 60, "counter": 0},    # Access
    "s":   {"start": 90, "counter": 0},    # Service
    "CE":  {"start": 120, "counter": 0},   # Customer
    "other": {"start": 150, "counter": 0}, # Others
}

def run_containerlab_inspect():
    """Run containerlab inspect and return parsed JSON."""
    result = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)

def categorize_router(name: str) -> str:
    """Return pool key for router name."""
    if name.startswith("crr"):
        return "crr"
    elif name.startswith("c"):
        return "c"
    elif name.startswith("d"):
        return "d"
    elif name.startswith("a"):
        return "a"
    elif name.startswith("s"):
        return "s"
    elif name.startswith("CE"):
        return "CE"
    else:
        return "other"

def next_ipv4(name: str, fallback: bool) -> str:
    """Assign next IPv4 address for a given router name."""
    if fallback:
        # Sequential from 1.1.1.1 for all
        total = sum(pool["counter"] for pool in POOLS.values())
        octet = total + 1
        # Update a global counter in "other"
        POOLS["other"]["counter"] += 1
        return f"1.1.1.{octet}"

    pool_key = categorize_router(name)
    base = POOLS[pool_key]["start"]
    POOLS[pool_key]["counter"] += 1
    octet = base + POOLS[pool_key]["counter"] - 1
    return f"1.1.1.{octet}"

def ipv6_from_ipv4(ipv4: str) -> str:
    """Encode last octet of IPv4 into IPv6 fd00::/8."""
    last_octet = int(ipv4.split(".")[-1])
    return f"fd00::{last_octet}"

def configure_loopback(node: dict, ipv4_host: str, ipv6_host: str):
    """Push loopback config to XRd router."""
    raw_ip = node["ipv4_address"]
    host = raw_ip.split("/")[0]  # strip CIDR
    print(f"📡 Configuring {node['name']} ({host}) ...")

    conn = Scrapli(
        host=host,
        auth_username=XR_USERNAME,
        auth_password=XR_PASSWORD,
        platform="cisco_iosxr",
        #transport="paramiko",
        auth_strict_key=False,
    )
    conn.open()

    configs = [
        "interface Loopback0",
        f"ipv4 address {ipv4_host} 255.255.255.255",
        f"ipv6 address {ipv6_host}/128",
    ]
    conn.send_configs(configs)
    #conn.send_command("commit")
    conn.send_config("commit")
    conn.close()

    print(f"✅ Configured Loopback0 with {ipv4_host}, {ipv6_host}")

def main():
    data = run_containerlab_inspect()
    all_nodes = list(data.values())[0]

    # Filter XRd routers
    xrd_nodes = [n for n in all_nodes if n["kind"] == "cisco_xrd"]

    # Detect if there are any matches for categories
    has_matches = any(categorize_router(n["name"]) != "other" for n in xrd_nodes)

    for node in xrd_nodes:
        ipv4 = next_ipv4(node["name"], fallback=not has_matches)
        ipv6 = ipv6_from_ipv4(ipv4)
        configure_loopback(node, ipv4, ipv6)

    print("\n✅ Done.")

if __name__ == "__main__":
    main()

