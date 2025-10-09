get_fortigate_config.py
=======================

Retrieve and save running configuration from FortiGate devices in a Containerlab
environment, and update the lab topology file with startup-config entries (optional).

Overview
--------
This script:
1. Runs `containerlab inspect` in the current lab directory.
2. Identifies FortiGate nodes from the device list.
3. Connects via SSH (paramiko) using Scrapli.
4. Executes `show` to retrieve the running config.
5. Saves the configuration to `clab-<labname>/<node>/config/<node>.cfg`.
6. Updates the `<labname>.clab.yml` with `startup-config` entries (startup-config not yet implemented for Fortigate in Containerlab)
7. Creates a backup of the original `.clab.yml`.

Requirements
------------
- Python 3.8+
- [Scrapli](https://pypi.org/project/scrapli/)
- [PyYAML](https://pypi.org/project/PyYAML/)
- Containerlab installed and working
- SSH connectivity to FortiGate nodes

Usage
-----
From inside your Containerlab lab directory:

    get_fortigate_config.py

Author
------
Stephan Baenisch