# clab_destroy.py

clab_destroy.py
===============

Wrapper script for containerlab workflows. Automates running get_clab_config.py before executing containerlab destroy.

Overview
--------
This script automates configuration backup for network labs created with
[Containerlab](https://containerlab.dev/). It performs the following steps:

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

