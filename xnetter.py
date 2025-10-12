#!/usr/bin/env python3
"""
xnetter.py

Assign point-to-point IPv4 /31 and IPv6 /127 addresses to links defined in the
current containerlab *.clab.yml and apply them to devices (Cisco XRd).

Behavior:
- Run `containerlab inspect -f json` to get node names and mgmt IPs
- Parse the *.clab.yml links
- For each link (pair of endpoints) allocate next /31 from 10.0.0.0/24
  and map IPv6 /127 from fc00::/7 by embedding IPv4 into low 32 bits
- Translate short if names like "Gi0-0-0-1" -> "GigabitEthernet0/0/0/1"
- Configure interfaces on Cisco XRd devices:
    interface <iface>
      ipv4 address 10.x.x.x 255.255.255.254
      ipv6 address <fc00:...>/127
      description To neighbor <peer_name> <peer_iface>
      no shutdown
    commit

Note: This script targets Cisco XRd nodes only. It can be extended for other
vendors.
"""

from pathlib import Path
import subprocess
import json
import yaml
import ipaddress
from collections import defaultdict
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException
import sys

# ---------- CONFIG ----------
IPV4_POOL = ipaddress.ip_network("10.10.10.0/24")
IPV4_PREFIXLEN = 31
IPV6_BASE = ipaddress.IPv6Address("fc00::")
IPV6_PREFIXLEN = 127

XRD_USERNAME = "clab"
XRD_PASSWORD = "clab@123"
# ----------------------------

def run_containerlab_inspect():
    """Run containerlab inspect -f json and return parsed JSON."""
    try:
        r = subprocess.run(
            ["containerlab", "inspect", "-f", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("❌ containerlab inspect failed:", e.stderr or e)
        sys.exit(2)
    try:
        data = json.loads(r.stdout)
    except Exception as e:
        print("❌ Failed to parse containerlab inspect JSON:", e)
        sys.exit(2)
    return data

def find_clab_yaml():
    """Find a single *.clab.yml file in cwd and return Path."""
    files = list(Path.cwd().glob("*.clab.yml"))
    if not files:
        print("❌ No .clab.yml file found in current directory.")
        sys.exit(1)
    if len(files) > 1:
        print("❌ Multiple .clab.yml files found; please run inside a single lab folder.")
        for f in files:
            print(" -", f)
        sys.exit(1)
    return files[0]

def load_links_from_yaml(yml_path: Path):
    """Load links list from clab YAML. Support several plausible structures."""
    raw = yaml.safe_load(yml_path.read_text())
    # Prefer top-level "links"
    if isinstance(raw, dict):
        if "links" in raw:
            return raw["links"]
        # some clab topologies have the topology under "topology" key
        if "topology" in raw and isinstance(raw["topology"], dict) and "links" in raw["topology"]:
            return raw["topology"]["links"]
    # fallback: try to find any 'links' key recursively
    def find_links(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "links":
                    return v
                res = find_links(v)
                if res is not None:
                    return res
        if isinstance(obj, list):
            for item in obj:
                res = find_links(item)
                if res is not None:
                    return res
        return None
    links = find_links(raw)
    if links is None:
        print("❌ No links section found in YAML.")
        sys.exit(1)
    return links

def parse_endpoint(ep):
    """
    Parse an endpoint entry. Accepts forms:
      - mapping: { hostname: "Gi0-0-0-1" }  (PyYAML will give a dict)
      - string: "hostname:Gi0-0-0-1"
    Returns tuple (hostname, short_iface)
    """
    if isinstance(ep, dict):
        # first key/value
        for k, v in ep.items():
            return str(k), str(v)
    if isinstance(ep, str):
        if ":" in ep:
            host, iface = ep.split(":", 1)
            return host.strip(), iface.strip()
    raise ValueError(f"Unsupported endpoint format: {ep!r}")

def short_if_to_real_if(short_if: str) -> str:
    """
    Translate Gi0-0-0-1 -> GigabitEthernet0/0/0/1
    Also accept Gi0/0/0/1 and return normalized GigabitEthernet...
    """
    s = short_if.strip()
    # if already looks like GigabitEthernet keep it
    if s.lower().startswith("gigabit"):
        return s
    # common shorthand starting with Gi or gi
    if s.startswith("Gi") or s.startswith("gi"):
        body = s[2:]
        # allow either '-' or '/' separators
        body = body.replace("-", "/")
        return "GigabitEthernet" + body
    # fallback return as-is
    return s

def allocate_ipv4_ipv6_pairs(links_count):
    """
    Generate a list of (ipv4_network, [a,b]) pairs for each link using /31 subnets
    within IPV4_POOL. Also compute corresponding IPv6 addresses by embedding
    IPv4 integer inside IPV6_BASE low 32 bits. Returns generator of tuples:
    (ipv4_net (IPv4Network), ipv4_addr_a (IPv4Address), ipv4_addr_b, ipv6_addr_a (IPv6Address), ipv6_addr_b)
    """
    subnets = IPV4_POOL.subnets(new_prefix=IPV4_PREFIXLEN)
    for net in subnets:
        # net is IPv4Network with 2 addresses for /31
        # get both addresses
        addrs = list(net.hosts()) if list(net.hosts()) else list(net)
        # sometimes hosts() for /31 yields both addresses or empty; ensure we take the two addresses
        if len(addrs) < 2:
            addrs = list(net)
        a = addrs[0]
        b = addrs[1]
        # Map IPv6: embed the IPv4 address integer into low 32 bits of base
        a_v6 = ipaddress.IPv6Address(int(IPV6_BASE) | int(a))
        b_v6 = ipaddress.IPv6Address(int(IPV6_BASE) | int(b))
        yield net, a, b, a_v6, b_v6

def build_node_map(clab_json):
    """
    Build mapping of node name -> info dict containing mgmt ip (IPv4)
    clab_json structure: top-level keys are labs, values are lists of node dicts
    """
    nodes = {}
    for labname, node_list in clab_json.items():
        for n in node_list:
            name = n.get("name")
            ipv4 = n.get("ipv4_address")
            ipaddr = None
            if ipv4:
                ipaddr = ipv4.split("/")[0]
            nodes[name] = {
                "name": name,
                "kind": n.get("kind"),
                "ip": ipaddr,
                "raw": n
            }
    return nodes

def prepare_link_endpoint_mapping(links, nodes_map):
    """
    Process YAML links into a list of tuples:
      ( (hostA, short_ifA), (hostB, short_ifB) )
    Validate hosts exist in containerlab inspect nodes_map.
    """
    pairs = []
    for link in links:
        eps = link.get("endpoints") if isinstance(link, dict) else None
        if eps is None and isinstance(link, list) and len(link) == 2:
            eps = link
        if eps is None:
            # YAML variant where link itself is dict with endpoints key,
            # or link is mapping. Try to find endpoints.
            if isinstance(link, dict) and "endpoints" in link:
                eps = link["endpoints"]
            else:
                print("⚠️ Unable to parse link entry, skipping:", link)
                continue
        if not isinstance(eps, list) or len(eps) != 2:
            print("⚠️ Endpoint pair not found or invalid, skipping:", link)
            continue
        try:
            h1, if1 = parse_endpoint(eps[0])
            h2, if2 = parse_endpoint(eps[1])
        except Exception as e:
            print("⚠️ Failed to parse endpoints:", e, "entry:", link)
            continue
        # validate hosts exist (exact match)
        if h1 not in nodes_map:
            print(f"❌ Host {h1} not found in containerlab inspect output; skipping link.")
            continue
        if h2 not in nodes_map:
            print(f"❌ Host {h2} not found in containerlab inspect output; skipping link.")
            continue
        pairs.append(((h1, if1), (h2, if2)))
    return pairs

def build_device_config_batches(pairs, nodes_map):
    """
    For each endpoint pair assign next /31 from pool and prepare per-device config commands.
    Returns: device_configs: { nodename: [cmd1, cmd2, ...] }
    and allocation_map: list of dicts describing assignment for reporting
    """
    device_configs = defaultdict(list)
    alloc = []
    gen = allocate_ipv4_ipv6_pairs(len(pairs))
    for (a_ep, b_ep), (net, a4, b4, a6, b6) in zip(pairs, gen):
        (a_host, a_short_if) = a_ep
        (b_host, b_short_if) = b_ep
        a_real_if = short_if_to_real_if(a_short_if)
        b_real_if = short_if_to_real_if(b_short_if)

        # Build XRd config lines for each side
        # For XR, we'll use interface context commands (send_configs will enter config mode)
        # Example commands per interface:
        # interface GigabitEthernet0/0/0/1
        #  description To neighbor <peername> <peer_iface>
        #  ipv4 address 10.x.x.x 255.255.255.254
        #  ipv6 address <addr>/<prefixlen>
        #  no shutdown

        a_cmds = [
            f"interface {a_real_if}",
            f" description To neighbor {b_host} {b_real_if}",
            f" ipv4 address {a4} 255.255.255.254",
            f" ipv6 address {a6}/{IPV6_PREFIXLEN}",
            f" no shutdown",
        ]
        b_cmds = [
            f"interface {b_real_if}",
            f" description To neighbor {a_host} {a_real_if}",
            f" ipv4 address {b4} 255.255.255.254",
            f" ipv6 address {b6}/{IPV6_PREFIXLEN}",
            f" no shutdown",
        ]
        # Append to batches
        device_configs[a_host].extend(a_cmds)
        device_configs[b_host].extend(b_cmds)

        alloc.append({
            "link_net": str(net),
            "a": {"host": a_host, "interface": a_real_if, "ipv4": str(a4), "ipv6": str(a6) + f"/{IPV6_PREFIXLEN}"},
            "b": {"host": b_host, "interface": b_real_if, "ipv4": str(b4), "ipv6": str(b6) + f"/{IPV6_PREFIXLEN}"},
        })

    return device_configs, alloc

def push_configs_to_devices(device_configs, nodes_map):
    """
    Connect to each device and push configs (for XRd). Commits once per device.
    """
    for node, cmds in device_configs.items():
        info = nodes_map.get(node)
        if not info:
            print(f"⚠️ Node {node} missing in nodes_map, skipping")
            continue
        if info.get("kind") != "cisco_xrd":
            print(f"ℹ️ Skipping node {node} of kind {info.get('kind')}")
            continue
        host = info.get("ip") or info.get("name")
        print(f"📡 Configuring {node} ({host}) ...")
        try:
            conn = Scrapli(
                host=host,
                auth_username=XRD_USERNAME,
                auth_password=XRD_PASSWORD,
                platform="cisco_iosxr",
                auth_strict_key=False,
                transport="paramiko",
            )
            conn.open()
            # send configs as a batch (Scrapli will enter config mode)
            conn.send_configs(cmds)
            # commit once
            try:
                conn.send_config("commit")
            except Exception:
                # some XRd images expect "commit" inside configuration session; ensuring it's sent
                conn.send_command("commit")
            print(f"✅ Pushed {len(cmds)} config lines to {node}")
        except ScrapliException as e:
            print(f"❌ Scrapli error for {node}: {e}")
        except Exception as e:
            print(f"❌ Unexpected error for {node}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

def main():
    # 1) containerlab inspect
    clab_json = run_containerlab_inspect()
    nodes_map = build_node_map(clab_json)

    # 2) find YAML and parse links
    yml_path = find_clab_yaml()
    print("ℹ️ Found topology file:", yml_path.name)
    links = load_links_from_yaml(yml_path)

    # 3) parse link endpoint pairs and validate hosts present
    pairs = prepare_link_endpoint_mapping(links, nodes_map)
    if not pairs:
        print("ℹ️ No valid link pairs found; exiting.")
        return

    # 4) build device config batches and allocation table
    device_configs, alloc = build_device_config_batches(pairs, nodes_map)

    # 5) show allocation summary
    print("\n🔢 Allocations:")
    for entry in alloc:
        print(f" - link {entry['link_net']}:")
        print(f"    {entry['a']['host']} {entry['a']['interface']} -> {entry['a']['ipv4']} {entry['a']['ipv6']}")
        print(f"    {entry['b']['host']} {entry['b']['interface']} -> {entry['b']['ipv4']} {entry['b']['ipv6']}")

    # 6) push configs to devices
    confirm = input("\nProceed to push these configs to devices? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted by user.")
        return

    push_configs_to_devices(device_configs, nodes_map)
    print("\n✅ Done.")

if __name__ == "__main__":
    main()

