#!/usr/bin/env python3
"""
ospf-wiper.py
=============

Remove OSPF processes from Cisco XRd routers in a Containerlab lab.

What it does
------------
1. Runs `containerlab inspect -f json` in the current directory and discovers lab nodes.
2. Filters nodes for Cisco XRd devices (kind == "cisco_xrd").
3. SSHs to each XRd device with Scrapli (username "clab", password "clab@123").
4. Runs a safe inspection sequence:
     - `terminal exec prompt no-timestamp`
     - `show running-config router ospf | include router ospf`
   to discover configured OSPF process IDs.
5. Presents a per-router summary of discovered OSPF processes to the user.
6. Asks once whether to remove the detected OSPF processes from all routers.
   If the user confirms, removes them using:
     - `no router ospf <id>` (for each detected id)
     - `commit`
   then closes the connection.

Usage
-----
Run this from inside the Containerlab lab directory that contains the .clab.yml file:

    python3 ospf-wiper.py

Prompts:
- The script will show detected OSPF processes per router and then ask once:
    "Remove detected OSPF processes from all routers? (y/N): "
  Answer `y` to perform the removals, anything else to abort.

Requirements
------------
- Python 3.8+
- scrapli (`pip install scrapli`)
- containerlab available on PATH
- SSH reachability from the host running this script to the containerlab node names/addresses

Author
------
Stephan
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Dict, List, Tuple

from scrapli import Scrapli

# Credentials for Cisco XRd in Containerlab
USERNAME = "clab"
PASSWORD = "clab@123"

# Simple on/off debugging
DEBUG = False


def run_containerlab_inspect() -> dict:
    """Run `containerlab inspect -f json` and return parsed JSON dict."""
    result = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def discover_xrd_nodes(clab_json: dict) -> Dict[str, str]:
    """
    From containerlab inspect JSON return dict { short_name: ipv4 } for cisco_xrd nodes.

    short_name is derived from the node "name" by stripping the leading "clab-<labname>-"
    if present; otherwise uses the raw node name.
    """
    lab_name = list(clab_json.keys())[0]
    nodes = {}
    for node in clab_json[lab_name]:
        if node.get("kind") != "cisco_xrd":
            continue
        raw_name = node.get("name")
        prefix = f"clab-{lab_name}-"
        short = raw_name[len(prefix) :] if raw_name.startswith(prefix) else raw_name
        ipv4 = node.get("ipv4_address", "")
        ip = ipv4.split("/")[0] if ipv4 else None
        if ip:
            nodes[short] = ip
    return nodes


def gather_ospf_processes(ip: str) -> List[str]:
    """
    Connect to device at ip, run inspection commands and return list of discovered OSPF process IDs as strings.

    Commands executed:
      - terminal exec prompt no-timestamp
      - show running-config router ospf | include router ospf
    """
    conn = Scrapli(
        host=ip,
        auth_username=USERNAME,
        auth_password=PASSWORD,
        platform="cisco_iosxr",
        auth_strict_key=False,
    )
    conn.open()
    # avoid timestamps in outputs (makes parsing cleaner)
    conn.send_command("terminal exec prompt no-timestamp")
    # collect lines that include "router ospf"
    result = conn.send_command("show running-config router ospf | include router ospf")
    output = result.result or ""
    if DEBUG:
        print(f"[{ip}] raw ospf discovery output:\n{output}")
    conn.close()

    pids: List[str] = []
    for line in output.splitlines():
        line = line.strip()
        m = re.match(r"router ospf (\d+)", line)
        if m:
            pids.append(m.group(1))
    return pids


def remove_ospf_processes(ip: str, pids: List[str]) -> Tuple[bool, str]:
    """
    Connect to device at ip and remove the specified OSPF processes one by one.
    Returns (success_bool, message).
    """
    if not pids:
        return True, "no processes to remove"

    conn = Scrapli(
        host=ip,
        auth_username=USERNAME,
        auth_password=PASSWORD,
        platform="cisco_iosxr",
        auth_strict_key=False,
    )
    conn.open()

    # enter config mode and issue removals
    for pid in pids:
        print(f"🧹 Removing OSPF {pid} on {ip}...")
        conn.send_config(f"no router ospf {pid}")

    # commit *inside config mode*
    conn.send_config("commit")

    conn.close()
    return True, f"removed processes: {', '.join(pids)}"





def main() -> None:
    try:
        clab = run_containerlab_inspect()
    except subprocess.CalledProcessError as exc:
        print(f"❌ Failed to run containerlab inspect: {exc}")
        sys.exit(1)

    xrd_nodes = discover_xrd_nodes(clab)
    if not xrd_nodes:
        print("No Cisco XRd nodes found in this lab. Nothing to do.")
        return

    # Gather OSPF processes on each router
    ospf_map: Dict[str, List[str]] = {}
    print("\n🔎 Discovering existing OSPF processes on XRd routers...")
    for name, ip in xrd_nodes.items():
        try:
            pids = gather_ospf_processes(ip)
        except Exception as exc:
            print(f"⚠️ {name} ({ip}): failed to inspect: {exc}")
            pids = []
        ospf_map[name] = pids

    # Present summary to user
    print("\nDetected OSPF processes:")
    any_found = False
    for name, pids in ospf_map.items():
        if pids:
            any_found = True
            print(f"- {name} ({xrd_nodes[name]}): processes {', '.join(pids)}")
        else:
            print(f"- {name} ({xrd_nodes[name]}): none")

    if not any_found:
        print("\n✅ No OSPF processes detected on any XRd routers. Nothing to remove.")
        return

    # Ask once whether to remove all detected OSPF processes
    answer = input("\nRemove detected OSPF processes from all routers? (y/N): ").strip().lower()
    if answer != "y":
        print("Aborted by user. No changes made.")
        return

    # Proceed with removal
    print("\n🧹 Removing OSPF processes on all routers (where detected)...")
    for name, pids in ospf_map.items():
        ip = xrd_nodes[name]
        if not pids:
            print(f"- {name} ({ip}): nothing to remove")
            continue
        success, msg = remove_ospf_processes(ip, pids)
        if success:
            print(f"- {name} ({ip}): ✅ {msg}")
        else:
            print(f"- {name} ({ip}): ❌ {msg}")

    print("\n✔️ OSPF removal run complete.")


if __name__ == "__main__":
    main()
