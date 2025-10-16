#!/usr/bin/env python3
"""
bgp-wizard.py
=============

Automates hierarchical iBGP configuration on Cisco XRd routers in a Containerlab lab.

High level:
- Reads Containerlab topology (containerlab inspect -f json)
- Selects cisco_xrd nodes
- Connects to each router, reads Loopback0 IPv4 (used as router-id & update-source)
- Classifies routers by name pattern (core / distribution / access)
- Builds a hierarchical iBGP plan with route-reflectors and clients
- Shows planned allocations (spinner while analyzing) and asks to proceed
- Pushes BGP config to devices and commits

Usage:
    bgp-wizard.py

Author: Stephan (adapted)
"""

from __future__ import annotations
import subprocess
import json
import re
import sys
import time
import threading
import itertools
from typing import Dict, List, Tuple, Optional
from scrapli import Scrapli
from pathlib import Path

# defaults
DEFAULT_AS = 65000
BGP_PASSWORD = "hurz123"  # default neighbor-group password (local lab)
SPINNER_INTERVAL = 0.12

# ---------------- Helpers ----------------

def run_containerlab_inspect() -> dict:
    """Run `containerlab inspect -f json` and return parsed JSON."""
    proc = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)

def short_name_from_node(raw_name: str, lab_name: str) -> str:
    """Strip leading clab-<lab>- prefix if present."""
    prefix = f"clab-{lab_name}-"
    return raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name

# spinner
class Spinner:
    def __init__(self, message: str = "Working..."):
        self._spinner = itertools.cycle(["|", "/", "-", "\\"])
        self._stop = threading.Event()
        self._thread = None
        self.message = message

    def start(self):
        def run():
            sys.stdout.write(self.message + " ")
            sys.stdout.flush()
            while not self._stop.is_set():
                sys.stdout.write(next(self._spinner))
                sys.stdout.flush()
                time.sleep(SPINNER_INTERVAL)
                sys.stdout.write("\b")
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write("\b Done!\n")
        sys.stdout.flush()

# ---------------- Name parsing & classification ----------------

def parse_name_parts(name: str) -> Tuple[str, Optional[int]]:
    """
    Split name into alpha prefix and numeric region.
    Example: "chrg12" -> ("chrg", 12)
             "xrd1" -> ("xrd", 1)
             "CE1" -> ("CE", 1)
    """
    m = re.match(r"^([A-Za-z]+)(\d+)?", name)
    if not m:
        return name, None
    prefix = m.group(1)
    digits = m.group(2)
    region = int(digits) if digits else None
    return prefix.lower(), region

def classify_router(prefix: str) -> str:
    """Return role classification string (core, crr, chr, cc, distribution, access, ce, other)."""
    # Normalize checks for common patterns from your spec
    if prefix.upper().startswith("CE"):
        return "ce"
    if prefix.startswith("crr"):
        return "crr"  # central RRs
    if prefix.startswith("ccr"):
        return "ccr"
    if prefix.startswith("chr"):
        return "chr"
    # core group: any starting with 'c' or 's' but not covered above
    if prefix.startswith("c") or prefix.startswith("s"):
        return "core"
    # distribution: start with d or dh/ds/ah etc
    if prefix.startswith("d") or prefix.startswith("dh") or prefix.startswith("ds") or prefix.startswith("ah"):
        return "distribution"
    # access: ahr, asr, as
    if prefix.startswith("ahr") or prefix.startswith("asr") or prefix.startswith("a"):
        return "access"
    return "other"

# ---------------- Device interactions ----------------

def get_loopback0_ip(host: str, username: str = "clab", password: str = "clab@123") -> Optional[str]:
    """Connect and return IPv4 address configured under Loopback0 or None."""
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
        timeout_socket=60,
        timeout_ops=60,
    )
    conn.open()
    # request running-config for loopback
    resp = conn.send_command("show running-config interface Loopback0", strip_prompt=False)
    conn.close()
    text = resp.result or ""
    # try to find ipv4 address like: ipv4 address 1.1.1.1 255.255.255.255 or ipv4 address 1.1.1.1/32
    for line in text.splitlines():
        m = re.search(r"ipv4 address (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            return m.group(1)
    # fallback: try 'show running-config | include interface Loopback0' then 'show interface Loopback0' but keep simple
    return None

def push_config(host: str, lines: List[str], username: str = "clab", password: str = "clab@123"):
    """Push a list of config lines (already in XR style) and commit."""
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
        timeout_socket=120,
        timeout_ops=300,
    )
    conn.open()
    # send in config mode using send_configs
    conn.send_configs(lines)
    # commit inside config mode
    conn.send_config("commit")
    conn.close()

# ---------------- BGP plan builder ----------------

def build_bgp_plan(nodes_info: List[dict], asn: int) -> dict:
    """
    nodes_info: list of dicts with keys: name(short), host(ip), prefix, region, role, loopback
    Returns a plan with peers and groups for each node
    """
    plan = {}
    # index nodes by classifications
    by_role = {"crr": [], "core": [], "chr": [], "ccr": [], "distribution": [], "access": [], "ce": [], "other": []}
    for n in nodes_info:
        role = n["role"]
        by_role.setdefault(role, []).append(n)

    # full-mesh among crr
    crrs = by_role.get("crr", [])
    # core-other are everything with role 'core' + 'ccr' maybe
    cores = by_role.get("core", []) + by_role.get("ccr", [])
    chrs = by_role.get("chr", [])
    dists = by_role.get("distribution", [])
    accesses = by_role.get("access", [])

    # helper to find chrs for a given distribution region (by region number)
    def find_chr_for_region(rnum: Optional[int]):
        if rnum is None:
            return chrs[:]  # fallback: all chrs
        matches = [c for c in chrs if c.get("region") == rnum]
        return matches if matches else chrs[:]  # fallback to all chrs

    # build plan per node
    for n in nodes_info:
        name = n["name"]
        plan[name] = {
            "node": n,
            "asn": asn,
            "router_id": n["loopback"],
            "neighbors": [],  # list of dicts: {peer_ip, group}
            "neighbor_groups": set(),  # RR-Mesh or RR-Client usage
        }

    # RR mesh among crrs
    for a in crrs:
        for b in crrs:
            if a["name"] == b["name"]:
                continue
            plan[a["name"]]["neighbors"].append({"peer": b["loopback"], "group": "RR-Mesh", "peer_name": b["name"]})

    # Other core nodes become clients of all crrs
    for core in cores + chrs:
        for rr in crrs:
            if core["name"] == rr["name"]:
                continue
            plan[core["name"]]["neighbors"].append({"peer": rr["loopback"], "group": "RR-Client", "peer_name": rr["name"]})

    # For distribution routers: each distribution peers with chr routers for its region
    for d in dists:
        # find chr routers responsible for this distribution region
        chrs_for = find_chr_for_region(d.get("region"))
        # make two peers when possible: choose up to two chr routers
        selected = chrs_for[:2] if chrs_for else []
        for c in selected:
            plan[d["name"]]["neighbors"].append({"peer": c["loopback"], "group": "RR-Client", "peer_name": c["name"]})
            plan[c["name"]]["neighbors"].append({"peer": d["loopback"], "group": "RR-Client", "peer_name": d["name"]})

    # access routers: find ahr for their region (try to match leading digits of region)
    def find_ahr_for_access(access_region: Optional[int]):
        if access_region is None:
            return [a for a in accesses]  # fallback (but this will be odd)
        # attempt: find ahr with region equal to first two digits if access region >= 3 digits
        if access_region >= 100:
            parent_region = access_region // 10  # heuristic: 121 -> 12
        else:
            parent_region = access_region
        matches = [a for a in accesses if a.get("region") == parent_region or a.get("region") == access_region]
        # fallback to any 'access' role router with 'ahr' prefix (hard to determine), use first two
        return matches if matches else accesses[:1]

    # For access routers: peer with ahr routers (treat ahr as RR for their access group)
    for a in accesses:
        # find matching ahr routers (we treat 'access' list includes ahr/others; this is heuristic)
        # For simplicity: choose up to 2 peers from accesses that have same region or any 'ahr' prefix
        candidates = [x for x in accesses if x.get("region") == a.get("region") and x["name"] != a["name"]]
        # fallback: use chrs (less ideal) or pick any two from accesses
        if not candidates:
            # try to find ahr-type nodes (prefix contains 'ahr')
            ahr_candidates = [x for x in accesses if x["prefix"].startswith("ahr") and x["name"] != a["name"]]
            candidates = ahr_candidates if ahr_candidates else [x for x in accesses if x["name"] != a["name"]][:2]
        for peer in candidates[:2]:
            plan[a["name"]]["neighbors"].append({"peer": peer["loopback"], "group": "RR-Client", "peer_name": peer["name"]})
            plan[peer["name"]]["neighbors"].append({"peer": a["loopback"], "group": "RR-Client", "peer_name": a["name"]})

    # ensure uniqueness in neighbor lists
    for p in plan.values():
        seen = set()
        new_neighbors = []
        for nb in p["neighbors"]:
            key = (nb["peer"], nb["group"])
            if key in seen:
                continue
            seen.add(key)
            new_neighbors.append(nb)
        p["neighbors"] = new_neighbors

    return plan

# ---------------- Printing plan ----------------

def print_plan_summary(plan: dict):
    """Print a readable plan summary."""
    print("\n🔎 Planned BGP allocations:")
    for name, entry in plan.items():
        n = entry["node"]
        print(f"- {name} ({n['host']}) role={n['role']} RID={entry['router_id']} AS={entry['asn']}")
        if not entry["neighbors"]:
            print("   (no BGP peers planned)")
        else:
            for nb in entry["neighbors"]:
                print(f"   {nb['peer']} ({nb['peer_name']}) -> use group {nb['group']}")

# ---------------- Config generation ----------------

def generate_config_lines(entry: dict) -> List[str]:
    """
    Build XR config lines for a node plan entry.
    This builds neighbor-groups RR-Mesh and RR-Client globally and neighbor statements per peer.
    """
    node = entry["node"]
    asn = entry["asn"]
    rid = entry["router_id"]
    neighbors = entry["neighbors"]

    lines = []
    lines.append(f"router bgp {asn}")
    lines.append(" nsr")
    lines.append(" timers bgp 30 90")
    lines.append(f" bgp router-id {rid}")
    lines.append(" bgp graceful-restart restart-time 120")
    lines.append(" bgp graceful-restart graceful-reset")
    lines.append(" bgp graceful-restart stalepath-time 360")
    lines.append(" bgp log neighbor changes detail")
    lines.append(" ibgp policy out enforce-modifications")
    # AF stanzas
    lines.append(" address-family vpnv4 unicast")
    lines.append("  nexthop trigger-delay critical 0")
    lines.append(" !")
    lines.append(" address-family vpnv6 unicast")
    lines.append("  nexthop trigger-delay critical 0")
    lines.append(" !")
    # Neighbor groups definitions (inline)
    lines.append(" neighbor-group RR-Mesh")
    lines.append(f"  remote-as {asn}")
    lines.append(f"  password clear {BGP_PASSWORD}")
    lines.append("  update-source Loopback0")
    lines.append("  !")
    lines.append("  address-family ipv4 labeled-unicast")
    lines.append("  !")
    lines.append("  address-family vpnv4 unicast")
    lines.append("  !")
    lines.append("  address-family vpnv6 unicast")
    lines.append("  !")
    lines.append(" !")
    lines.append(" neighbor-group RR-Client")
    lines.append(f"  remote-as {asn}")
    lines.append(f"  password clear {BGP_PASSWORD}")
    lines.append("  update-source Loopback0")
    lines.append("  !")
    lines.append("  address-family ipv4 labeled-unicast")
    lines.append("   route-reflector-client")
    lines.append("   next-hop-self")
    lines.append("  !")
    lines.append("  address-family vpnv4 unicast")
    lines.append("   multipath")
    lines.append("   route-reflector-client")
    lines.append("  !")
    lines.append("  address-family vpnv6 unicast")
    lines.append("   multipath")
    lines.append("   route-reflector-client")
    lines.append("  !")
    lines.append(" !")

    # neighbors
    for nb in neighbors:
        lines.append(f" neighbor {nb['peer']}")
        # use neighbor-group naming style from example:
        if nb["group"] == "RR-Mesh":
            lines.append("  use neighbor-group RR-Mesh")
        else:
            lines.append("  use neighbor-group RR-Client")
        lines.append(" !")

    # end router bgp stanza
    lines.append("!")  # close top-level config
    return lines

# ---------------- Main flow ----------------

def main():
    # welcome
    print("""
===========================================
      Welcome to the BGP Wizard (XRd)
===========================================
""")

    # run containerlab inspect
    try:
        data = run_containerlab_inspect()
    except Exception as e:
        print(f"❌ Failed to run containerlab inspect: {e}")
        sys.exit(1)

    lab_name = list(data.keys())[0]
    nodes_raw = data[lab_name]

    # collect Cisco XRd nodes
    nodes_info = []
    for node in nodes_raw:
        if node.get("kind") != "cisco_xrd":
            continue
        rawname = node.get("name")
        short = short_name_from_node(rawname, lab_name)
        host = node.get("ipv4_address", "").split("/")[0]
        prefix, region = parse_name_parts(short)
        role = classify_router(prefix)
        nodes_info.append({
            "rawname": rawname,
            "name": short,
            "host": host,
            "prefix": prefix,
            "region": region,
            "role": role,
            "loopback": None  # to be filled
        })

    if not nodes_info:
        print("⚠️ No Cisco XRd nodes found. Exiting.")
        sys.exit(0)

    # ask for AS or default
    asn_input = input(f"Enter AS number to use for iBGP (default {DEFAULT_AS}): ").strip()
    asn = int(asn_input) if asn_input.isdigit() else DEFAULT_AS

    # spinner during loopback gathering & classification
    spinner = Spinner(" Collecting Loopback0 and analyzing topology")
    spinner.start()

    # get loopback0 for each node; abort if missing
    for n in nodes_info:
        lb = get_loopback0_ip(n["host"])
        n["loopback"] = lb
    spinner.stop()

    missing = [n for n in nodes_info if not n["loopback"]]
    if missing:
        print("\n❌ The following routers are missing Loopback0 IP; aborting:")
        for m in missing:
            print(f" - {m['name']} ({m['host']})")
        sys.exit(1)

    # build plan
    plan = build_bgp_plan(nodes_info, asn)

    # print planned summary
    print_plan_summary(plan)

    proceed = input("\nProceed to push these configs to devices? (y/N): ").strip().lower() == "y"
    if not proceed:
        print("❌ Aborted by user.")
        sys.exit(0)

    # push per-router config
    for name, entry in plan.items():
        print(f"\n📡 Configuring {name} ({entry['node']['host']}) ...")
        cfg_lines = generate_config_lines(entry)
        # push to device
        push_config(entry['node']['host'], cfg_lines)
        print(f"✅ Pushed {len(cfg_lines)} config lines to {name}")

    print("\n✅ Done. BGP configuration applied to all routers in plan.")

if __name__ == "__main__":
    main()

