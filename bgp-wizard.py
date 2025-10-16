#!/usr/bin/env python3
"""
bgp-wizard.py
==============

Automated BGP Configuration Generator and Deployer for Cisco IOS XR (Containerlab Environments)

Overview
--------
This script automates the generation and deployment of hierarchical iBGP configurations across
Cisco XRd routers running inside a Containerlab topology. It builds a complete BGP control-plane
based on node naming conventions and known topology roles (CRR, CCR, CHR, DHR, DSR, AHR, ASR).

The script establishes SSH connections to all routers via Scrapli, discovers their Loopback0 IP
addresses (used as BGP Router-IDs), builds the BGP relationship hierarchy, and optionally pushes
fully-rendered BGP configurations back to the routers.

Features
--------
- Automatic discovery of routers and roles from `containerlab inspect`
- Hierarchical iBGP structure with route-reflectors and clients:
  * Core (CRR, CCR, CHR, SAR)
  * Distribution (DHR, DSR)
  * Access (AHR, ASR)
- Automatic full-mesh between CRR routers
- Role-based BGP neighbor-group generation
- Descriptive neighbor entries including peer hostname and loopback
- Built-in IPv4, VPNv4, VPNv6, and BGP-LU configuration
- Per-router route-policy `RP_BGPLU_Lo0` to allocate labels for its own loopback /32
- Optional inclusion or exclusion of CE routers from analysis
- Dry-run export of planned configurations to `./bgp-wizard_configs`
- Optional live deployment (push to routers)

Configuration Template Highlights
---------------------------------
Each router receives a full BGP configuration block that includes:
- `router bgp <ASN>`
- Graceful-restart and NSR configuration
- Address-families: IPv4, VPNv4, VPNv6
- `allocate-label route-policy RP_BGPLU_Lo0` for labeled unicast (BGP-LU)
- Neighbor-groups dynamically created based on router role
- Per-peer description lines: “To <hostname> with Loopback0 <IP>”
- Per-router route-policy restricting label allocation to its own /32

Example Usage
-------------
1. Ensure all XRd routers are running and reachable via SSH:
   $ containerlab inspect

2. Run the wizard:
   $ ./bgp-wizard.py

3. During execution, you will be prompted to:
   - Decide whether CE routers should be analyzed.
   - Review the planned BGP role and peering summary.
   - Export configs (dry-run) or push them directly to devices.

4. Generated configurations are stored under:
   ./bgp-wizard_configs/<hostname>.cfg

Dependencies
------------
- Python 3.10+
- Scrapli (network automation SSH library)
- Containerlab CLI (`containerlab inspect --format json`)

Author
------
Developed by Stephan B. and GPT Network Automation Assistant
Version: 1.0
Date: 2025-10-16
"""

from __future__ import annotations
import json
import re
import sys
import time
import threading
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from scrapli import Scrapli

DEFAULT_AS = 65000
BGP_PASSWORD = "hurz123"

# ---------------- Pretty bits ----------------
def banner():
    print(r"""
🧙‍♂️  Welcome to the BGP Wizard
--------------------------------
This tool builds hierarchical iBGP for Cisco XRd in Containerlab.
""")

class Spinner:
    def __init__(self, msg="Analyzing topology"):
        self.msg = msg
        self._stop = threading.Event()
        self._t = None

    def start(self):
        def run():
            sys.stdout.write(self.msg + " ")
            sys.stdout.flush()
            while not self._stop.is_set():
                for ch in "|/-\\":
                    sys.stdout.write("\b" + ch)
                    sys.stdout.flush()
                    time.sleep(0.1)
                    if self._stop.is_set():
                        break
            sys.stdout.write("\b Done!\n")
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join()

# ---------------- Containerlab ----------------
def run_containerlab_inspect() -> dict:
    proc = subprocess.run(
        ["containerlab", "inspect", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)

def short_name(raw_name: str, lab_name: str) -> str:
    p = f"clab-{lab_name}-"
    return raw_name[len(p):] if raw_name.startswith(p) else raw_name

# ---------------- Roles ----------------
def classify_router(name: str) -> str:
    p = name.lower()
    if p.startswith("ce"):
        return "ce"
    if p.startswith("crr"):
        return "crr"
    if p.startswith("ccr"):
        return "ccr"
    if p.startswith("chr"):
        return "chr"
    if p.startswith("sar"):
        return "sar"
    if p.startswith("dhr"):
        return "dhr"
    if p.startswith("dsr"):
        return "dsr"
    if p.startswith("ahr"):
        return "ahr"
    if p.startswith("asr"):
        return "asr"
    # generic fallbacks (rare)
    if p.startswith("c") or p.startswith("s"):
        return "core"
    if p.startswith("d") or p.startswith("a"):
        return "distribution"
    return "other"

# ---------------- Device ops ----------------
def get_loopback0_ip(host: str, username="clab", password="clab@123") -> Optional[str]:
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
        timeout_socket=45,
        timeout_transport=45,
        timeout_ops=90,
    )
    conn.open()
    resp = conn.send_command("show running-config interface Loopback0", strip_prompt=False)
    conn.close()
    txt = resp.result or ""
    for line in txt.splitlines():
        m = re.search(r"ipv4 address (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            return m.group(1)
    return None


def push_config(host: str, lines: list[str]):
    """
    Push BGP config to Cisco XR router using the same reliable workflow as ospf-wizard.py.
    - Opens connection
    - Sends all configuration lines
    - Commits configuration (in config mode)
    - Closes connection cleanly
    """

    print(f"📡 Pushing to {host} ...")

    conn = Scrapli(
        host=host,
        auth_username="clab",
        auth_password="clab@123",
        platform="cisco_iosxr",
        auth_strict_key=False,
    )

    try:
        conn.open()

        # --- Send configuration ---
        conn.send_configs(lines)

        # --- Commit while still in config mode ---
        commit_result = conn.send_config("commit")
        if "Uncommitted" in commit_result.result or "error" in commit_result.result.lower():
            print(f"⚠️ Commit warning on {host}:\n{commit_result.result.strip()}")
        else:
            print(f"✅ Commit complete on {host}")

        # Optional cleanup: exit to exec mode (not strictly needed)
        conn.send_config("end")

    except Exception as e:
        print(f"❌ Failed on {host}: {e}")

    finally:
        # --- Ensure clean close ---
        try:
            conn.close()
        except Exception:
            try:
                conn.transport.close()
            except Exception:
                pass


def push_config_bak2(host: str, lines: list[str]):
    """Push BGP config to XR router safely (same pattern as ospf-wizard)."""
    print(f"📡 Pushing to {host} ...")

    conn = Scrapli(
        host=host,
        auth_username="clab",
        auth_password="clab@123",
        platform="cisco_iosxr",
        auth_strict_key=False,
    )

    try:
        conn.open()
        # Send the configuration lines
        conn.send_configs(lines)
        # Commit using send_config (inside config mode!)
        conn.send_config("commit")
        print(f"✅ Commit complete on {host}")
    except Exception as e:
        print(f"❌ Failed on {host}: {e}")
    finally:
        conn.close()


def push_config_bak(host: str, lines: List[str], username="clab", password="clab@123"):
    conn = Scrapli(
        host=host,
        auth_username=username,
        auth_password=password,
        platform="cisco_iosxr",
        auth_strict_key=False,
        timeout_socket=60,
        timeout_ops=300,
    )
    conn.open()
    conn.send_configs(lines)
    conn.send_config("commit")
    conn.close()

# ---------------- Plan builder ----------------
def build_bgp_plan(nodes_info: List[dict], asn: int) -> dict:
    """
    Build plan strictly per hierarchy (not link-limited):
      crr <-> crr (mesh)
      crr -> ccr/chr/sar (RR-to-Client)
      chr -> dhr/dsr/ahr (RR-to-Client)
      ahr -> asr (RR-to-Client)
    """
    plan: Dict[str, dict] = {}
    role_map: Dict[str, List[dict]] = {}
    for n in nodes_info:
        role_map.setdefault(n["role"], []).append(n)

    # Initialize plan skeleton
    for n in nodes_info:
        plan[n["name"]] = {
            "node": n,
            "asn": asn,
            "router_id": n["loopback"],
            "neighbors": [],
        }

    def add_pair(a: dict, b: dict, group_a_to_b: str, group_b_to_a: str):
        """Add bidirectional neighbor relationship."""
        if a["name"] != b["name"]:
            plan[a["name"]]["neighbors"].append({
                "peer": b["loopback"],
                "peer_name": b["name"],
                "peer_role": b["role"],
                "group": group_a_to_b,
            })
            plan[b["name"]]["neighbors"].append({
                "peer": a["loopback"],
                "peer_name": a["name"],
                "peer_role": a["role"],
                "group": group_b_to_a,
            })

    # 1️⃣ CRR full-mesh
    for a in role_map.get("crr", []):
        for b in role_map.get("crr", []):
            if a["name"] != b["name"]:
                plan[a["name"]]["neighbors"].append({
                    "peer": b["loopback"],
                    "peer_name": b["name"],
                    "peer_role": "crr",
                    "group": "RR-Mesh",
                })

    # 2️⃣ CRR → CCR/CHR/SAR
    for rr in role_map.get("crr", []):
        for cl in role_map.get("ccr", []) + role_map.get("chr", []) + role_map.get("sar", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 3️⃣ CHR → DHR/DSR/AHR
    for rr in role_map.get("chr", []):
        for cl in role_map.get("dhr", []) + role_map.get("dsr", []) + role_map.get("ahr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 4️⃣ AHR → ASR
    for rr in role_map.get("ahr", []):
        for cl in role_map.get("asr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # ❌ CCR and CHR must *not* peer directly
    # ❌ CCR and SAR must *not* peer directly
    # So we skip any such relationships completely

    # Deduplicate neighbors
    for p in plan.values():
        seen = set()
        uniq = []
        for nb in p["neighbors"]:
            key = (nb["peer"], nb["group"])
            if key not in seen:
                seen.add(key)
                uniq.append(nb)
        p["neighbors"] = uniq

    # Validation warnings (expected upstream)
    expectations = {
        "dhr": "chr",
        "dsr": "chr",
        "ahr": "chr",
        "asr": "ahr",
        "chr": "crr",
        "ccr": "crr",
        "sar": "crr",
    }
    for n in nodes_info:
        role = n["role"]
        if role in expectations:
            expect = expectations[role]
            nbs = plan[n["name"]]["neighbors"]
            has_up = any(nb["peer_role"] == expect and nb["group"] == "Client-to-RR" for nb in nbs)
            if not has_up:
                print(f"⚠️  Warning: {n['name']} ({role}) has no upstream {expect.upper()} peer in plan!")

    return plan



def build_bgp_plan_bak2(nodes_info: List[dict], asn: int) -> dict:
    """
    Build plan strictly per hierarchy (not link-limited):
      crr <-> crr (mesh)
      crr -> ccr/chr/sar (RR-to-Client)
      ccr -> chr (RR-to-Client)
      chr -> dhr/dsr/ahr (RR-to-Client), and chr -> crr (Client-to-RR)
      ahr -> asr (RR-to-Client), and ahr -> chr (Client-to-RR)
      dhr/dsr -> chr (Client-to-RR)
      asr -> ahr (Client-to-RR)
    """
    plan: Dict[str, dict] = {}
    role_map: Dict[str, List[dict]] = {}
    for n in nodes_info:
        role_map.setdefault(n["role"], []).append(n)

    # init skeleton
    for n in nodes_info:
        plan[n["name"]] = {
            "node": n,
            "asn": asn,
            "router_id": n["loopback"],
            "neighbors": [],
        }

    def add_pair(a: dict, b: dict, group_a_to_b: str, group_b_to_a: str):
        """Bidirectional neighbor relationship."""
        if a["name"] != b["name"]:
            plan[a["name"]]["neighbors"].append({
                "peer": b["loopback"],
                "peer_name": b["name"],
                "peer_role": b["role"],
                "group": group_a_to_b,
            })
            plan[b["name"]]["neighbors"].append({
                "peer": a["loopback"],
                "peer_name": a["name"],
                "peer_role": a["role"],
                "group": group_b_to_a,
            })

    # 1️⃣ CRR full mesh
    for a in role_map.get("crr", []):
        for b in role_map.get("crr", []):
            if a["name"] != b["name"]:
                plan[a["name"]]["neighbors"].append({
                    "peer": b["loopback"],
                    "peer_name": b["name"],
                    "peer_role": "crr",
                    "group": "RR-Mesh",
                })

    # 2️⃣ CRR → CCR/CHR/SAR
    for rr in role_map.get("crr", []):
        for cl in role_map.get("ccr", []) + role_map.get("chr", []) + role_map.get("sar", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 3️⃣ CHR → DHR/DSR/AHR
    for rr in role_map.get("chr", []):
        for cl in role_map.get("dhr", []) + role_map.get("dsr", []) + role_map.get("ahr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 4️⃣ AHR → ASR
    for rr in role_map.get("ahr", []):
        for cl in role_map.get("asr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 5️⃣ CHR are clients of CCR
    for up in role_map.get("ccr", []):
        for cl in role_map.get("chr", []):
            add_pair(up, cl, "RR-to-Client", "Client-to-RR")

    # ❌ DO NOT peer CCR ↔ SAR
    # 6️⃣ SAR are clients of CCR only if explicitly allowed (disabled per your correction)
    # Previously:
    # for up in role_map.get("ccr", []):
    #     for cl in role_map.get("sar", []):
    #         add_pair(up, cl, "RR-to-Client", "Client-to-RR")

    # Deduplicate
    for p in plan.values():
        seen = set()
        uniq = []
        for nb in p["neighbors"]:
            key = (nb["peer"], nb["group"])
            if key not in seen:
                seen.add(key)
                uniq.append(nb)
        p["neighbors"] = uniq

    # Validation warnings
    expectations = {
        "dhr": "chr",
        "dsr": "chr",
        "ahr": "chr",
        "asr": "ahr",
        "chr": "ccr",
        "sar": "ccr",
        "ccr": "crr",
    }
    for n in nodes_info:
        role = n["role"]
        if role in expectations:
            expect = expectations[role]
            nbs = plan[n["name"]]["neighbors"]
            has_up = any(nb["peer_role"] == expect and nb["group"] == "Client-to-RR" for nb in nbs)
            if not has_up:
                print(f"⚠️  Warning: {n['name']} ({role}) has no upstream {expect.upper()} peer in plan!")

    return plan




def build_bgp_plan_bak(nodes_info: List[dict], asn: int) -> dict:
    """
    Build plan strictly per hierarchy (not link-limited):
      crr <-> crr (mesh)
      crr -> ccr/chr/sar (RR-to-Client)
      ccr -> chr/sar (Client-to-RR)
      chr -> dhr/dsr/ahr (RR-to-Client), and chr -> crr (Client-to-RR)
      ahr -> asr (RR-to-Client), and ahr -> chr (Client-to-RR)
      dhr/dsr -> chr (Client-to-RR)
      asr -> ahr (Client-to-RR)
    """
    plan: Dict[str, dict] = {}
    role_map: Dict[str, List[dict]] = {}
    for n in nodes_info:
        role_map.setdefault(n["role"], []).append(n)

    # init skeleton
    for n in nodes_info:
        plan[n["name"]] = {
            "node": n,
            "asn": asn,
            "router_id": n["loopback"],
            "neighbors": [],  # list of {peer, peer_name, peer_role, group}
        }

    def add_pair(a: dict, b: dict, group_a_to_b: str, group_b_to_a: str):
        """Bidirectional neighbor relationship."""
        if a["name"] != b["name"]:
            plan[a["name"]]["neighbors"].append({
                "peer": b["loopback"], "peer_name": b["name"], "peer_role": b["role"], "group": group_a_to_b
            })
            plan[b["name"]]["neighbors"].append({
                "peer": a["loopback"], "peer_name": a["name"], "peer_role": a["role"], "group": group_b_to_a
            })

    # 1) CRR full-mesh
    for a in role_map.get("crr", []):
        for b in role_map.get("crr", []):
            if a["name"] != b["name"]:
                plan[a["name"]]["neighbors"].append({
                    "peer": b["loopback"], "peer_name": b["name"], "peer_role": "crr", "group": "RR-Mesh"
                })

    # 2) CRR as RR for CCR/CHR/SAR
    for rr in role_map.get("crr", []):
        for cl in role_map.get("ccr", []) + role_map.get("chr", []) + role_map.get("sar", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 3) CHR as RR for DHR/DSR/AHR
    for rr in role_map.get("chr", []):
        for cl in role_map.get("dhr", []) + role_map.get("dsr", []) + role_map.get("ahr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 4) AHR as RR for ASR
    for rr in role_map.get("ahr", []):
        for cl in role_map.get("asr", []):
            add_pair(rr, cl, "RR-to-Client", "Client-to-RR")

    # 5) CHR are *clients* of CCR (already partially handled by #2; keep explicit)
    for up in role_map.get("ccr", []):
        for cl in role_map.get("chr", []):
            add_pair(up, cl, "RR-to-Client", "Client-to-RR")

    # 6) SAR are clients of CCR (explicit)
    for up in role_map.get("ccr", []):
        for cl in role_map.get("sar", []):
            add_pair(up, cl, "RR-to-Client", "Client-to-RR")

    # Deduplicate (peer, group)
    for p in plan.values():
        seen = set()
        uniq = []
        for nb in p["neighbors"]:
            key = (nb["peer"], nb["group"])
            if key not in seen:
                seen.add(key)
                uniq.append(nb)
        p["neighbors"] = uniq

    # Validation warnings for missing expected RR
    expectations = {
        "dhr": "chr",
        "dsr": "chr",
        "ahr": "chr",
        "asr": "ahr",
        "chr": "ccr",
        "sar": "ccr",
        "ccr": "crr",
    }
    for n in nodes_info:
        role = n["role"]
        if role in expectations:
            expect = expectations[role]
            nbs = plan[n["name"]]["neighbors"]
            has_up = any(nb["peer_role"] == expect and nb["group"] == "Client-to-RR" for nb in nbs)
            if not has_up:
                print(f"⚠️  Warning: {n['name']} ({role}) has no upstream {expect.upper()} peer in plan!")

    return plan

# ---------------- Config generation ----------------
def generate_config_lines(entry: dict) -> List[str]:
    node = entry["node"]
    role = node["role"]
    rid = entry["router_id"]
    asn = entry["asn"]
    nbs = entry["neighbors"]

    # --- Base BGP config identical on all routers ---
    lines = [
        f"router bgp {asn}",
        " nsr",
        " timers bgp 30 90",
        f" bgp router-id {rid}",
        " bgp graceful-restart restart-time 120",
        " bgp graceful-restart graceful-reset",
        " bgp graceful-restart stalepath-time 360",
        " bgp graceful-restart",
        " bgp log neighbor changes detail",
        " ibgp policy out enforce-modifications",
        " !",
        " address-family ipv4 unicast",
        f"  network {rid}/32",
        "  allocate-label route-policy RP_BGPLU_Lo0",
        " !",
        " address-family vpnv4 unicast",
        "  nexthop trigger-delay critical 0",
        " !",
        " address-family vpnv6 unicast",
        "  nexthop trigger-delay critical 0",
        " !",
        "!"
    ]

    # --- Neighbor-group generator helper ---
    def add_group(name, desc, rrclient=False, nhself=False):
        nonlocal lines
        lines.extend([
            f" neighbor-group {name}",
            f"  remote-as {asn}",
            f"  password clear {BGP_PASSWORD}",
            "  update-source Loopback0",
            f"  description {desc}",
            "  !",
            "  address-family ipv4 labeled-unicast",
        ])
        if rrclient:
            lines.append("   route-reflector-client")
        if nhself:
            lines.append("   next-hop-self")
        lines += ["  !", "  address-family vpnv4 unicast"]
        if rrclient:
            lines += ["   multipath", "   route-reflector-client"]
        if nhself:
            lines.append("   next-hop-self")
        lines += ["  !", "  address-family vpnv6 unicast"]
        if rrclient:
            lines += ["   multipath", "   route-reflector-client"]
        if nhself:
            lines.append("   next-hop-self")
        lines += ["  !", " !"]

    # --- Neighbor-group sets per role ---
    if role == "crr":
        add_group("CRR-to-CRR", "Group for full mesh peering between all CRR routers")
        add_group(
            "CRR-to-C-Clients",
            "Group for downstream peering to all RR Clients in Core",
            rrclient=True,
            nhself=True,
        )
    elif role in ("ccr", "sar"):
        add_group(
            "CCR_SAR-to-CRR",
            "Group for upstream peering from CCR and SAR to all CRR Route-Reflectors",
            nhself=True,
        )
    elif role == "chr":
        add_group(
            "CHR-to-D-Clients",
            "Group for downstream peering to all RR Clients in Distribution",
            rrclient=True,
            nhself=True,
        )
        add_group(
            "CHR-to-CRR",
            "Group for upstream peering from CHR to all CRR Route-Reflectors",
            nhself=True,
        )
    elif role in ("dhr", "dsr"):
        add_group(
            "DHR_DSR-to-CHR",
            "Group for upstream peering from DHR and DSR to all CHR Route-Reflectors",
            nhself=True,
        )
    elif role == "ahr":
        add_group(
            "AHR-to-A-Clients",
            "Group for downstream peering to all RR Clients in Access",
            rrclient=True,
            nhself=True,
        )
        add_group(
            "AHR-to-CHR",
            "Group for upstream peering from AHR to all CHR Route-Reflectors",
            nhself=True,
        )
    elif role == "asr":
        add_group(
            "ASR-to-AHR",
            "Group for upstream peering from ASR to all AHR Route-Reflectors",
            nhself=True,
        )

    # --- Neighbor assignments ---
    def group_for(local: str, peer_role: str, rel_group: str) -> str:
        if local == "crr":
            return "CRR-to-CRR" if peer_role == "crr" else "CRR-to-C-Clients"
        if local in ("ccr", "sar"):
            return "CCR_SAR-to-CRR"
        if local == "chr":
            return "CHR-to-D-Clients" if rel_group == "RR-to-Client" else "CHR-to-CRR"
        if local in ("dhr", "dsr"):
            return "DHR_DSR-to-CHR"
        if local == "ahr":
            return "AHR-to-A-Clients" if peer_role == "asr" else "AHR-to-CHR"
        if local == "asr":
            return "ASR-to-AHR"
        return "UNKNOWN"

    for nb in nbs:
        nb_ip = nb["peer"]
        nb_name = nb["peer_name"]
        nb_role = nb["peer_role"]
        rel = nb["group"]
        grp = group_for(role, nb_role, rel)
        lines += [
            f" neighbor {nb_ip}",
            f"  use neighbor-group {grp}",
            f"  description To {nb_name} with Loopback0 {nb_ip}",
            " !",
        ]

    # --- Route-policy for this router’s own Loopback0 ---
    lines.extend([
        "!",
        "route-policy RP_BGPLU_Lo0",
        f"  if destination in ({rid}/32) then",
        "    pass",
        "  else",
        "    drop",
        "  endif",
        "end-policy",
        "!"
    ])

    return lines

# ---------------- Main ----------------
def main():
    banner()

    data = run_containerlab_inspect()
    lab = list(data.keys())[0]

    include_ce = input("Include CE routers in BGP plan? (y/N): ").strip().lower() == "y"
    as_str = input(f"Enter BGP AS [default {DEFAULT_AS}]: ").strip()
    asn = int(as_str) if as_str.isdigit() else DEFAULT_AS

    # Collect XRd nodes
    nodes_raw = [n for n in data[lab] if n.get("kind") == "cisco_xrd"]

    # Build base node info (name/host/role), then fetch loopbacks
    nodes_info = []
    for n in nodes_raw:
        nm = short_name(n["name"], lab)
        role = classify_router(nm)
        if role == "ce" and not include_ce:
            continue
        host = n["ipv4_address"].split("/")[0]
        nodes_info.append({"name": nm, "role": role, "host": host, "loopback": None})

    if not nodes_info:
        print("⚠️ No eligible XRd routers found.")
        return

    # Pull Loopback0s
    spin = Spinner("Collecting Loopback0 from routers")
    spin.start()
    for node in nodes_info:
        node["loopback"] = get_loopback0_ip(node["host"])
    spin.stop()

    missing = [n for n in nodes_info if not n["loopback"]]
    if missing:
        print("\n❌ Missing Loopback0 on routers:")
        for m in missing:
            print(f" - {m['name']} ({m['host']})")
        sys.exit(1)

    # Build plan
    plan = build_bgp_plan(nodes_info, asn)

    # Summary
    print("\n🔎 Planned BGP allocations:")
    for name, entry in plan.items():
        node = entry["node"]
        print(f"- {name} ({node['host']}) role={node['role']} RID={entry['router_id']} AS={asn}")
        if not entry["neighbors"]:
            print("   (no BGP peers planned)")
        else:
            for nb in entry["neighbors"]:
                print(f"   {nb['peer']} ({nb['peer_name']}) → group {nb['group']}")

    # Dry-run export
    if input("\nExport planned configs to ./bgp-wizard_configs? (y/N): ").strip().lower() == "y":
        out = Path("bgp-wizard_configs")
        out.mkdir(exist_ok=True)
        for name, entry in plan.items():
            cfg = "\n".join(generate_config_lines(entry))
            (out / f"{name}.cfg").write_text(cfg)
        print(f"✅ Exported to {out.resolve()}")

    # Optional push
    if input("\nPush configs to devices now? (y/N): ").strip().lower() == "y":
        for name, entry in plan.items():
            print(f"\n📡 Pushing to {name} ({entry['node']['host']}) ...")
            lines = generate_config_lines(entry)
            push_config(entry["node"]["host"], lines)
            print(f"✅ Applied {len(lines)} lines to {name}")

    print("\n✅ Done.")

if __name__ == "__main__":
    main()