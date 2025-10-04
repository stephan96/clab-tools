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