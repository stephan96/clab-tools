#!/usr/bin/env python3
"""
ospf-wizard.py
==============

Automates OSPF configuration for Cisco XRd routers in a Containerlab lab.

OVERVIEW
--------
- Reads Loopback0 IPv4 from device configuration (used as router-id).
- Configures OSPF processes per router role.
- Loopback0 is configured as passive in all OSPF processes.
- Enables OSPF on router-to-router links according to predefined rules.
- Commits configuration and exits.

ROUTER ROLE CLASSIFICATION
--------------------------
- ce : Customer Edge routers (name starts with "CE")
- ch : Core/HQ routers (name starts with "ch")
- cc : Core/Compute routers (name starts with "cc")
- cr : Core Route Reflector routers (name starts with "cr")
- sa : Service/Aggregation routers (name starts with "sa")
- dh : Distribution High routers (name starts with "dh")
- ds : Distribution Secondary routers (name starts with "ds")
- ah : Aggregation/Hybrid routers (name starts with "ah")
- as : Access routers (name starts with "as")
- core : Generic core routers (name starts with "c" or "s" not matched above)
- d : Generic distribution routers (name starts with "d")
- other : Routers with unknown role (any other name pattern)

OSPF PROCESS ALLOCATION
-----------------------
- cc, cr, sa, core : OSPF process 1
- ch : OSPF processes 1 and 10
- dh, ds : OSPF process 10
- ah : OSPF processes 10 and 100
- as : OSPF process 100
- ce : no OSPF process
- other : no OSPF process (unless overridden with force_all option)

LOOPBACK0 CONFIGURATION
-----------------------
- Loopback0 interface is always included in OSPF processes.
- Configured as passive.
- Router-ID is set to Loopback0 IPv4 address.

LINK-TO-OSPF RULES
------------------
All rules assume link endpoints are parsed as (local_node:local_iface, neighbor_node:neighbor_iface):

1. Core & Service Links
   - Links between all "c" and "s" routers: process 1, area OSPF_AREA_CORE
   - Links between ch routers:
       - If interface ends with 0/0/0/0: process 1, area OSPF_AREA_CORE
       - Otherwise: process 10, area OSPF_AREA_DISTRIBUTION
   - Links between c and d routers: process 10, area OSPF_AREA_DISTRIBUTION

2. Distribution & Aggregation Links
   - Between all d routers and between d and ah routers: process 10, area OSPF_AREA_DISTRIBUTION

3. Access & Aggregation Links
   - Between all a routers and between ah and a routers: process 100, area OSPF_AREA_ACCESS
   - Special case between ahrg and ahrb:
       - First link: process 10, area 0.0.0.10
       - Second link: process 100, area OSPF_AREA_ACCESS

4. CE Links
   - Links involving CE routers are ignored (no OSPF)

5. Fallback Option
   - If *all* routers are classified as "other" and user agrees, process 1 / area 0 is applied to all routers and links.

DEBUGGING
---------
- Set `DEBUG = True` at the top of the script to enable detailed link parsing output.
- LLDP neighbor parsing is used to detect router-to-router links dynamically.

USAGE
-----
1. Ensure containerlab lab is deployed.
2. Run script:
    $ ./ospf-wizard.py
3. Follow prompt if all routers are "other" to force OSPF on all devices.

AUTHOR
------
Stephan
"""

import subprocess
import json
import ipaddress
import re
from scrapli import Scrapli
import sys
import time
import threading
import itertools

DEBUG = False  # set to True or False to enable or suppress debug output

# Define Areas to be used per network region
OSPF_AREA_CORE = "0.0.0.0"
OSPF_AREA_DISTRIBUTION = "0.0.0.0"
OSPF_AREA_ACCESS = "0.0.0.0"


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
            return (10, OSPF_AREA_DISTRIBUTION)
        else:
            return (100, OSPF_AREA_ACCESS)

    # Core–Core
    if (n1[0] in "cs") and (n2[0] in "cs"):
        return (1, OSPF_AREA_CORE)

    # ch–ch dual process
    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, OSPF_AREA_CORE)
        else:
            return (10, OSPF_AREA_DISTRIBUTION)

    # c–d and d–d links
    if (n1.startswith("c") and n2.startswith("d")) or (n2.startswith("c") and n1.startswith("d")) or (n1.startswith("d") and n2.startswith("d")):
        return (10, OSPF_AREA_DISTRIBUTION)

    # a–a, ah–a, ah–ah links
    if (n1.startswith("a") and n2.startswith("a")) or \
       (n1.startswith("ah") and n2.startswith("a")) or \
       (n2.startswith("ah") and n1.startswith("a")):
        return (100, OSPF_AREA_ACCESS)

    if n1.startswith("ah") and n2.startswith("ah"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (10, OSPF_AREA_DISTRIBUTION)
        else:
            return (100, OSPF_AREA_ACCESS)

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
        return (1, OSPF_AREA_CORE)

    # ch↔ch
    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, OSPF_AREA_CORE)
        else:
            return (10, OSPF_AREA_DISTRIBUTION)

    # d↔d or d↔ah
    if (n1.startswith("d") and n2.startswith("d")) or (
        (n1.startswith("d") and n2.startswith("ah")) or (n2.startswith("d") and n1.startswith("ah"))
    ):
        return (10, OSPF_AREA_DISTRIBUTION)

    # a↔a or ah↔a
    if (n1.startswith("a") and n2.startswith("a")) or (
        (n1.startswith("ah") and n2.startswith("a")) or (n2.startswith("a") and n1.startswith("ah"))
    ):
        return (100, OSPF_AREA_ACCESS)

    # ah↔ah → distribute across OSPF 10 and 100
    if n1.startswith("ah") and n2.startswith("ah"):
    #if n1.startswith("ahrg") and n2.startswith("ahrb"):
        if sorted([i1, i2])[0] in (i1, i2):  # pick consistently
            return (10, OSPF_AREA_DISTRIBUTION)
        else:
            return (100, OSPF_AREA_ACCESS)

    # NEW: c↔d rule
    if (n1.startswith("c") and n2.startswith("d")) or (n1.startswith("d") and n2.startswith("c")):
        return (10, OSPF_AREA_DISTRIBUTION)

    return None


def link_to_ospf_bak(endpoints: list) -> tuple | None:
    n1, i1 = endpoints[0].split(":")
    n2, i2 = endpoints[1].split(":")

    if n1.startswith("CE") or n2.startswith("CE"):
        return None

    if (n1[0] in "cs") and (n2[0] in "cs"):
        return (1, OSPF_AREA_CORE)

    if n1.startswith("ch") and n2.startswith("ch"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (1, OSPF_AREA_CORE)
        else:
            return (10, OSPF_AREA_DISTRIBUTION)

    if (n1.startswith("d") and n2.startswith("d")) or (
        (n1.startswith("d") and n2.startswith("ah"))
        or (n2.startswith("d") and n1.startswith("ah"))
    ):
        return (10, OSPF_AREA_DISTRIBUTION)

    if (n1.startswith("a") and n2.startswith("a")) or (
        (n1.startswith("ah") and n2.startswith("a"))
        or (n2.startswith("a") and n1.startswith("ah"))
    ):
        return (100, OSPF_AREA_ACCESS)

    if n1.startswith("ah") and n2.startswith("ah"):
        if i1.endswith("0/0/0/0") or i2.endswith("0/0/0/0"):
            return (10, OSPF_AREA_DISTRIBUTION)
        else:
            return (100, OSPF_AREA_ACCESS)

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
            pid, area = 1, OSPF_AREA_CORE
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


def welcome_screen():
    wizard_ascii = r"""


 ██████  ███████ ██████  ███████     ██     ██ ██ ███████  █████  ██████  ██████
██    ██ ██      ██   ██ ██          ██     ██ ██    ███  ██   ██ ██   ██ ██   ██
██    ██ ███████ ██████  █████       ██  █  ██ ██   ███   ███████ ██████  ██   ██
██    ██      ██ ██      ██          ██ ███ ██ ██  ███    ██   ██ ██   ██ ██   ██
 ██████  ███████ ██      ██           ███ ███  ██ ███████ ██   ██ ██   ██ ██████

    """
    print(wizard_ascii)
    print("✨ Welcome to the OSPF Wizard! ✨")
    print("This tool helps you configure OSPF on your Containerlab XRd routers.\n")


# Spinner helper
class Spinner:
    def __init__(self, message="Working..."):
        self.spinner = itertools.cycle(["|", "/", "-", "\\"])
        self.stop_running = False
        self.thread = None
        self.message = message

    def start(self):
        def run():
            sys.stdout.write(self.message + " ")
            sys.stdout.flush()
            while not self.stop_running:
                sys.stdout.write(next(self.spinner))
                sys.stdout.flush()
                time.sleep(0.1)
                sys.stdout.write("\b")
        self.thread = threading.Thread(target=run)
        self.thread.start()

    def stop(self):
        self.stop_running = True
        if self.thread:
            self.thread.join()
        sys.stdout.write("\b Done!\n")
        sys.stdout.flush()


# ---------------- Main ----------------

def main():

    welcome_screen()  # Show welcome at start

    data = run_containerlab_inspect()
    lab_name = list(data.keys())[0]

    # --- Ask upfront which mode to run ---
    print("Select OSPF configuration mode:")
    print("  a) Rule-based allocation (with automatic fallback if no known roles are found)")
    print("  b) Full fallback (all routers OSPF 1, Area 0)")
    mode = input("Choose mode (a/b): ").strip().lower()

    force_all = False
    enforce_fallback = False
    if mode == "b":
        enforce_fallback = True

    roles = []
    routers = []
    for node in data[lab_name]:
        if node["kind"] != "cisco_xrd":
            continue
        name = node["name"].replace(f"clab-{lab_name}-", "")
        role = get_router_role(name)
        roles.append(role)
        routers.append((name, role, node["ipv4_address"].split("/")[0]))

    # --- Auto fallback if roles are unknown and user picked mode a ---
    if not enforce_fallback and all(r == "other" for r in roles):
        answer = input("⚠️ No known router types found. Configure OSPF process 1/area 0 on all routers and links? (y/N): ").strip().lower()
        if answer == "y":
            force_all = True

    # --- Preview allocations ---
    print("\n🔎 Planned OSPF allocations:")
    preview = {}

    spinner = Spinner(" Collecting router info")
    spinner.start()

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

        # Decide processes
        if enforce_fallback or force_all:
            processes = [1]
        else:
            processes = ospf_processes_for_role(role)

        preview[name] = {"host": host, "role": role, "rid": rid, "processes": processes, "neighbors": []}

        spinner.stop()

        # Loopback always passive
        preview[name]["neighbors"].append(("Loopback0", "passive in all"))

        # Assign links
        for local_intf, neigh, neigh_intf in neighbors:
            if enforce_fallback or force_all:
                pid, area = 1, OSPF_AREA_CORE
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

    # --- Apply configs ---
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
        configure_ospf(conn, name, role, rid, neighbors, force_all=(enforce_fallback or force_all))
        conn.close()

    print("\n✅ Done.")


if __name__ == "__main__":
    main()




# def main_old():
#     data = run_containerlab_inspect()
#     lab_name = list(data.keys())[0]

#     roles = []
#     routers = []
#     for node in data[lab_name]:
#         if node["kind"] != "cisco_xrd":
#             continue
#         name = node["name"].replace(f"clab-{lab_name}-", "")
#         role = get_router_role(name)
#         roles.append(role)
#         routers.append((name, role, node["ipv4_address"].split("/")[0]))

#     # Fallback if all routers are "other"
#     force_all = False
#     if all(r == "other" for r in roles):
#         answer = input("⚠️ No known router types found. Configure OSPF process 1/area 0 on all routers and links? (y/N): ").strip().lower()
#         if answer == "y":
#             force_all = True

#     # Preview allocations
#     print("\n🔎 Planned OSPF allocations:")
#     preview = {}
#     for name, role, host in routers:
#         conn = Scrapli(
#             host=host,
#             auth_username="clab",
#             auth_password="clab@123",
#             platform="cisco_iosxr",
#             auth_strict_key=False,
#         )
#         conn.open()
#         rid = get_loopback_ip(conn) or "1.1.1.1"
#         neighbors = get_lldp_neighbors(conn, name)
#         conn.close()

#         processes = [1] if force_all else ospf_processes_for_role(role)
#         preview[name] = {"host": host, "role": role, "rid": rid, "processes": processes, "neighbors": []}

#         # Loopback always passive
#         preview[name]["neighbors"].append(("Loopback0", "passive in all"))

#         for local_intf, neigh, neigh_intf in neighbors:
#             if force_all:
#                 pid, area = 1, OSPF_AREA_CORE
#             else:
#                 pid_area = link_to_ospf([f"{name}:{local_intf}", f"{neigh}:{neigh_intf}"])
#                 if not pid_area:
#                     continue
#                 pid, area = pid_area
#             preview[name]["neighbors"].append((local_intf, f"OSPF {pid} area {area} → {neigh} {neigh_intf}"))

#     for name, info in preview.items():
#         print(f"- {name} ({info['host']}) role={info['role']} RID={info['rid']} → processes {info['processes']}")
#         for intf, desc in info["neighbors"]:
#             print(f"   {intf} → {desc}")

#     confirm = input("\nProceed to push these configs to devices? (y/N): ").strip().lower()
#     if confirm != "y":
#         print("❌ Aborted.")
#         return

#     # Apply configs
#     for name, role, host in routers:
#         conn = Scrapli(
#             host=host,
#             auth_username="clab",
#             auth_password="clab@123",
#             platform="cisco_iosxr",
#             auth_strict_key=False,
#         )
#         conn.open()
#         rid = get_loopback_ip(conn) or "1.1.1.1"
#         neighbors = get_lldp_neighbors(conn, name)
#         print(f"📡 Configuring {name} ({host}) ...")
#         configure_ospf(conn, name, role, rid, neighbors, force_all=force_all)
#         conn.close()

#     print("\n✅ Done.")


# if __name__ == "__main__":
#     main()

