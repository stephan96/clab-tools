#!/usr/bin/env python3
"""
ospf-wizard11.py
================

Automates OSPF configuration for Cisco XRd routers in a Containerlab lab.

- Reads Loopback0 IPv4 from config
- Configures OSPF processes per role
- Enables OSPF on Loopback0 (passive) and LLDP-discovered links
- Preview mappings before pushing
- Commit and exit

Author: Stephan
"""

import subprocess
import json
import ipaddress
import re
from scrapli import Scrapli



DEBUG = False  # set to True or False to enable or suppress debug output


# ---------------- Containerlab ----------------

def run_containerlab_inspect() -> dict:
    result = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


# ---------------- Role classification ----------------

def get_router_role(name: str) -> str:
    if name.startswith("CE"):
        return "ce"
    if name.startswith("ch"):
        return "ch"
    if name.startswith("cc"):
        return "cc"
    if name.startswith("cr"):
        return "cr"
    if name.startswith("sa"):
        return "sa"
    if name.startswith("dh"):
        return "dh"
    if name.startswith("ds"):
        return "ds"
    if name.startswith("ah"):
        return "ah"
    #if name.startswith("ahrb"):
    #    return "ahrb"
    if name.startswith("as"):
        return "as"
    if name.startswith("c") or name.startswith("s"):
        return "core"
    if name.startswith("d"):
        return "d"
    return "other"


def ospf_processes_for_role(role: str) -> list:
    if role in ("cc", "cr", "sa", "core"):
        return [1]
    if role == "ch":
        return [1, 10]
    if role in ("dh", "ds"):
        return [10]
    if role == "ah":
    #if role == ("ahrg", "ahrb"):
        return [10, 100]
    if role == "as":
        return [100]
    return []  # CE and others → no OSPF unless override


link_count_tracker = {}

def link_to_ospf_bak2(endpoints: list) -> tuple[int, str] | None:
    n1, i1 = endpoints[0].split(":")
    n2, i2 = endpoints[1].split(":")

    # CE routers never OSPF
    if n1.startswith("CE") or n2.startswith("CE"):
        return None

    # Special dual-link case
    if (("ahrb" in n1 and "ahrg" in n2) or ("ahrb" in n2 and "ahrg" in n1)):
        pair_key = tuple(sorted([n1, n2]))
        count = link_count_tracker.get(pair_key, 0)
        link_count_tracker[pair_key] = count + 1
        if count == 0:
            return (10, "0.0.0.10")
        else:
            return (100, "0.0.0.100")

    # Core–Core
    if (n1[0] in "cs") and (n2[0] in "cs"):
        return (1, "0.0.0.0")

    # ch–ch dual process
    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, "0.0.0.0")
        else:
            return (10, "0.0.0.10")

    # c–d and d–d links
    if (n1.startswith("c") and n2.startswith("d")) or (n2.startswith("c") and n1.startswith("d")) or (n1.startswith("d") and n2.startswith("d")):
        return (10, "0.0.0.10")

    # a–a, ah–a, ah–ah links
    if (n1.startswith("a") and n2.startswith("a")) or \
       (n1.startswith("ah") and n2.startswith("a")) or \
       (n2.startswith("ah") and n1.startswith("a")):
        return (100, "0.0.0.100")

    if n1.startswith("ah") and n2.startswith("ah"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (10, "0.0.0.10")
        else:
            return (100, "0.0.0.100")

    return None



def link_to_ospf(endpoints: list) -> tuple | None:
    n1, i1 = endpoints[0].split(":")
    n2, i2 = endpoints[1].split(":")

    # DEBUG: show which endpoints are analyzed
    if DEBUG:
        print(f"🔍 Analyzing link endpoints: {n1}:{i1} ↔ {n2}:{i2}")

    if n1.startswith("CE") or n2.startswith("CE"):
        return None

    # core↔core
    if (n1[0] in "cs") and (n2[0] in "cs"):
        return (1, "0.0.0.0")

    # ch↔ch
    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, "0.0.0.0")
        else:
            return (10, "0.0.0.10")

    # d↔d or d↔ah
    if (n1.startswith("d") and n2.startswith("d")) or (
        (n1.startswith("d") and n2.startswith("ah")) or (n2.startswith("d") and n1.startswith("ah"))
    ):
        return (10, "0.0.0.10")

    # a↔a or ah↔a
    if (n1.startswith("a") and n2.startswith("a")) or (
        (n1.startswith("ah") and n2.startswith("a")) or (n2.startswith("a") and n1.startswith("ah"))
    ):
        return (100, "0.0.0.100")

    # ah↔ah → distribute across OSPF 10 and 100
    if n1.startswith("ah") and n2.startswith("ah"):
    #if n1.startswith("ahrg") and n2.startswith("ahrb"):
        if sorted([i1, i2])[0] in (i1, i2):  # pick consistently
            return (10, "0.0.0.10")
        else:
            return (100, "0.0.0.100")

    # NEW: c↔d rule
    if (n1.startswith("c") and n2.startswith("d")) or (n1.startswith("d") and n2.startswith("c")):
        return (10, "0.0.0.10")

    return None


def link_to_ospf_bak(endpoints: list) -> tuple | None:
    n1, i1 = endpoints[0].split(":")
    n2, i2 = endpoints[1].split(":")

    if n1.startswith("CE") or n2.startswith("CE"):
        return None

    if (n1[0] in "cs") and (n2[0] in "cs"):
        return (1, "0.0.0.0")

    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, "0.0.0.0")
        else:
            return (10, "0.0.0.10")

    if (n1.startswith("d") and n2.startswith("d")) or (
        (n1.startswith("d") and n2.startswith("ah"))
        or (n2.startswith("d") and n1.startswith("ah"))
    ):
        return (10, "0.0.0.10")

    if (n1.startswith("a") and n2.startswith("a")) or (
        (n1.startswith("ah") and n2.startswith("a"))
        or (n2.startswith("a") and n1.startswith("ah"))
    ):
        return (100, "0.0.0.100")

    if n1.startswith("ah") and n2.startswith("ah"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (10, "0.0.0.10")
        else:
            return (100, "0.0.0.100")

    return None


# ---------------- Scrapli helpers ----------------

def get_loopback_ip(conn) -> str | None:
    result = conn.send_command("show running-config interface Loopback0")
    for line in result.result.splitlines():
        m = re.search(r"ipv4 address (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            return m.group(1)
    return None


#def get_lldp_neighbors_bak3(conn, name=None) -> list[tuple[str, str, str]]:
    """Return list of (local_interface, neighbor_name, neighbor_interface)."""
    result = conn.send_command("show lldp neighbors")
    neighbors = []
    for line in result.result.splitlines():
        line = line.strip()
        # Skip empty lines and known headers
        if not line or line.startswith("Device") or line.startswith("Capability") or line.startswith("Total") or line.startswith("Fri") or line.startswith("Mon") or line.startswith("Tue") or line.startswith("Wed") or line.startswith("Thu") or line.startswith("Oct") or line.startswith("Nov") or line.startswith("Dec") or line.startswith("Jan") or line.startswith("Feb") or line.startswith("Mar") or line.startswith("Apr") or line.startswith("May") or line.startswith("Jun") or line.startswith("Jul") or line.startswith("Aug") or line.startswith("Sep"):
            continue
        
        parts = line.split()
        if len(parts) >= 5:
            neighbor = parts[0]
            local_intf = parts[1]
            neighbor_intf = parts[-1]
            neighbors.append((local_intf, neighbor, neighbor_intf))
            if DEBUG:
                print(f"Detected LLDP neighbor: {local_intf} → {neighbor} {neighbor_intf}")
    return neighbors


def get_lldp_neighbors(conn, name=None) -> list[tuple[str, str, str]]:
    """Return list of (local_interface, neighbor_name, neighbor_interface)."""
    # prevent router from showing time and date in output
    result = conn.send_command("terminal exec prompt no-timestamp")
    # get lldp neighbors
    result = conn.send_command("show lldp neighbors | include GigabitEthernet")
    neighbors = []
    pattern = re.compile(
        r"^(?P<neighbor>\S+)\s+(?P<local_intf>\S+)\s+\d+\s+\S+\s+(?P<neighbor_intf>\S+)$"
    )
    for line in result.result.splitlines():
        m = pattern.match(line.strip())
        if m:
            neighbors.append((m.group("local_intf"), m.group("neighbor"), m.group("neighbor_intf")))
            if DEBUG:
                print(f"🔍 Parsed LLDP neighbor: {m.group('local_intf')} ↔ {m.group('neighbor')}:{m.group('neighbor_intf')}")
    return neighbors


def get_lldp_neighbors_bak_latest(conn, name=None) -> list[tuple[str, str, str]]:
    """
    Return list of (local_interface, neighbor_name, neighbor_interface)
    Only valid LLDP neighbor lines are included.
    """
    #result = conn.send_command("show lldp neighbors")
    result = conn.send_command("show lldp neighbors | include GigabitEthernet")
    neighbors = []
    # ccrb1           GigabitEthernet0/0/0/1          120        R               GigabitEthernet0/0/0/2
    #pattern = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)$")
    pattern = re.compile(r"^(?=.*GigabitEthernet)(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)$")
    #pattern = re.compile(r"^(\S+)\s+(\S+)\s+\d+\s+\S+\s+(\S+)$")

    for line in result.result.splitlines():
        # debug
        #print(f"Parsing line: {line}")  # <-- debug print
        m = pattern.match(line.strip())
        if m:
            neighbor_name = m.group(1)
            local_intf = m.group(2)
            neighbor_intf = m.group(3)
            neighbors.append((local_intf, neighbor_name, neighbor_intf))
    return neighbors


def get_lldp_neighbors_bak2(conn, name: str) -> list[tuple[str, str, str]]:
    """Return list of (local_interface, neighbor_name, neighbor_interface)."""
    result = conn.send_command("show lldp neighbors")
    neighbors = []
    for line in result.result.splitlines():
        # Skip headers and footer
        if line.strip().startswith("Device") or line.strip().startswith("Total"):
            continue
        parts = line.split()
        # LLDP table rows have at least 5 fields: <DeviceID> <LocalIntf> <Hold-time> <Capability> <PortID>
        if len(parts) >= 5:
            neighbor = parts[0]
            local_intf = parts[1]
            neighbor_intf = parts[-1]
            neighbors.append((local_intf, neighbor, neighbor_intf))
    return neighbors


def get_lldp_neighbors_bak(conn, name: str) -> list[tuple[str, str, str]]:
    """Return list of (local_interface, neighbor_name, neighbor_interface)."""
    result = conn.send_command("show lldp neighbors")
    neighbors = []
    for line in result.result.splitlines():
        if line.strip().startswith("Device") or line.strip().startswith("Total"):
            continue
        parts = line.split()
        if len(parts) >= 5:
            neighbor = parts[0]
            local_intf = parts[1]
            neighbor_intf = parts[-1]
            neighbors.append((local_intf, neighbor, neighbor_intf))
    return neighbors


def configure_ospf(conn, name: str, role: str, router_id: str, neighbors: list, force_all: bool = False):
    if force_all:
        processes = [1]
    else:
        processes = ospf_processes_for_role(role)

    if not processes:
        print(f"🚫 Skipping {name} (role {role}, no OSPF).")
        return

    cfg_lines = []
    for pid in processes:
        cfg_lines.append(f"router ospf {pid}")
        cfg_lines.append(f" router-id {router_id}")
        cfg_lines.append(" mpls ldp sync")
        cfg_lines.append(" area 0.0.0.0")
        cfg_lines.append("  network point-to-point")
        cfg_lines.append("  interface Loopback0")
        cfg_lines.append("   passive enable")
        cfg_lines.append("  !")

    for local_intf, neighbor, neigh_intf in neighbors:
        if force_all:
            pid, area = 1, "0.0.0.0"
        else:
            pid_area = link_to_ospf([f"{name}:{local_intf}", f"{neighbor}:{neigh_intf}"])
            if not pid_area:
                continue
            pid, area = pid_area

        cfg_lines.append(f"router ospf {pid}")
        cfg_lines.append(f" area {area}")
        cfg_lines.append(f"  interface {local_intf}")
        cfg_lines.append(" !")

    conn.send_configs(cfg_lines)
    conn.send_config("commit")
    print(f"✅ Configured OSPF on {name} (RID={router_id})")


# ---------------- Main ----------------

def main():
    data = run_containerlab_inspect()
    lab_name = list(data.keys())[0]

    roles = []
    routers = []
    for node in data[lab_name]:
        if node["kind"] != "cisco_xrd":
            continue
        name = node["name"].replace(f"clab-{lab_name}-", "")
        role = get_router_role(name)
        roles.append(role)
        routers.append((name, role, node["ipv4_address"].split("/")[0]))

    # Fallback if all routers are "other"
    force_all = False
    if all(r == "other" for r in roles):
        answer = input("⚠️ No known router types found. Configure OSPF process 1/area 0 on all routers and links? (y/N): ").strip().lower()
        if answer == "y":
            force_all = True

    # Preview allocations
    print("\n🔎 Planned OSPF allocations:")
    preview = {}
    for name, role, host in routers:
        conn = Scrapli(
            host=host,
            auth_username="clab",
            auth_password="clab@123",
            platform="cisco_iosxr",
            auth_strict_key=False,
        )
        conn.open()
        rid = get_loopback_ip(conn) or "1.1.1.1"
        neighbors = get_lldp_neighbors(conn, name)
        conn.close()

        processes = [1] if force_all else ospf_processes_for_role(role)
        preview[name] = {"host": host, "role": role, "rid": rid, "processes": processes, "neighbors": []}

        # Loopback always passive
        preview[name]["neighbors"].append(("Loopback0", "passive in all"))

        for local_intf, neigh, neigh_intf in neighbors:
            if force_all:
                pid, area = 1, "0.0.0.0"
            else:
                pid_area = link_to_ospf([f"{name}:{local_intf}", f"{neigh}:{neigh_intf}"])
                if not pid_area:
                    continue
                pid, area = pid_area
            preview[name]["neighbors"].append((local_intf, f"OSPF {pid} area {area} → {neigh} {neigh_intf}"))

    for name, info in preview.items():
        print(f"- {name} ({info['host']}) role={info['role']} RID={info['rid']} → processes {info['processes']}")
        for intf, desc in info["neighbors"]:
            print(f"   {intf} → {desc}")

    confirm = input("\nProceed to push these configs to devices? (y/N): ").strip().lower()
    if confirm != "y":
        print("❌ Aborted.")
        return

    # Apply configs
    for name, role, host in routers:
        conn = Scrapli(
            host=host,
            auth_username="clab",
            auth_password="clab@123",
            platform="cisco_iosxr",
            auth_strict_key=False,
        )
        conn.open()
        rid = get_loopback_ip(conn) or "1.1.1.1"
        neighbors = get_lldp_neighbors(conn, name)
        print(f"📡 Configuring {name} ({host}) ...")
        configure_ospf(conn, name, role, rid, neighbors, force_all=force_all)
        conn.close()

    print("\n✅ Done.")


if __name__ == "__main__":
    main()

