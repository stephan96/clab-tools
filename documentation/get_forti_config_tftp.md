get_fortigate_config.py
=======================

Retrieve and save running configuration from FortiGate devices in a Containerlab
environment using local TFTP backup.

Steps:
1. Verify TFTP server (tftpd-hpa) is running locally.
2. Parse its configuration for IP and directory.
3. Use Scrapli to connect to each FortiGate.
4. Execute `execute backup config tftp <hostname>.cfg <tftp_ip>`.
5. Move resulting file from the TFTP directory to lab node's config directory.
6. Optionally update the topology file with startup-config entries.

Author: Stephan Baenisch