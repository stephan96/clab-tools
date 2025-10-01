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