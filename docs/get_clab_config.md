# get_clab_config.py

get_clab_config.py
===================

Retrieve and save running configurations from Containerlab network devices
(Huawei VRP and Cisco XRd), and update the lab topology file with startup-config
entries.

Overview
--------
This script automates configuration backup for network labs created with
[Containerlab](https://containerlab.dev/). It performs the following steps:

1. Runs `containerlab inspect` in the current lab directory.
2. Parses the device list, identifying Huawei VRP and Cisco XRd nodes.
3. Connects via SSH to each device using [Scrapli](https://carlmontanari.github.io/scrapli/),
   executes the appropriate command to retrieve the running configuration, and saves
   it in the correct `config/` directory for each node.
4. Creates a backup of the lab's `.clab.yml` topology file.
5. Inserts a `startup-config` entry for each device, pointing to the saved configuration file.

Supported Device Types
----------------------
- **Huawei VRP**
  - Username: `admin`
  - Password: `admin`
  - Command: `display current-configuration`
- **Cisco XRd**
  - Username: `clab`
  - Password: `clab@123`
  - Command: `show running-config`

Requirements
------------
- Python 3.8+
- [Scrapli](https://pypi.org/project/scrapli/)
- [PyYAML](https://pypi.org/project/PyYAML/)
- Containerlab installed and working
- SSH connectivity to all lab devices (container host must be able to resolve container names)

Usage
-----
From inside your Containerlab lab directory:

    python3 get_clab_config.py

#### Example


    cd /home/user/containerlabs/hui-xrd-test-1
    python3 get_clab_config.py

After running, configurations will be saved to:

    clab-<labname>/<node>/config/<node>.cfg

And your `<labname>.clab.yml` will be updated with `startup-config` entries.
A backup of the original `.clab.yml` will be created with the `.bak` extension.

Author
------
Stephan Baenisch <stephan@baenisch.de>

