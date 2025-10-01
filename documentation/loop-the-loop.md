loop-the-loop.py
================

Automates Loopback0 interface provisioning for XRd routers in a Containerlab lab.

- Runs `containerlab inspect -f json`
- Parses the device list (name + kind)
- Logs into each XRd router via SSH using Scrapli
- Creates Loopback0 and configures IPv4/IPv6 addresses:
  - IPv4: from 1.1.1.0/24
  - IPv6: from fd00::/8, encoding the last IPv4 octet into the IPv6 suffix
- Address allocation scheme:
  - Core routers (name starts with "c"):      start at 1.1.1.1
  - Distribution routers (name starts with "d"): start at 1.1.1.51
  - Access routers (name starts with "a"):    start at 1.1.1.101
  - Service routers (name starts with "s"):   start at 1.1.1.151
  - Customer routers (name starts with "CE"): start at 1.1.1.201

Requirements:
- Python 3.8+
- scrapli
- containerlab in $PATH