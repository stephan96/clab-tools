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