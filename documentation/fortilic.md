fortilic.py
===========

FortiGate-VM License Installer & License Checker for Containerlab Topologies

Modes
-----
1) Install Mode (default):
   - Verify local TFTP server (tftpd-hpa), parse TFTP_ADDRESS and TFTP_DIRECTORY.
   - Run `containerlab inspect --format json` and detect FortiGate nodes:
       * Hostnames starting with "fg-"
       * Fallback: nodes whose "kind" contains "fortigate"
   - Prompt for license directory (default: /var/images/fortigate/Fortigate-VM_Lizenzen/).
   - Find "unused" .lic files (without "_fg-<hostname>" suffix).
   - If there are no unused licenses, exit.
   - Compare number of FortiGates vs number of licenses and allow selection of
     ALL or a subset of devices.
   - Use static credentials admin/admin to:
       * Run "get system status" (Scrapli).
       * Decide based on "License Status: Valid/Invalid".
       * For Valid: ask user if license should be replaced.
       * For Invalid/Unknown: proceed with upload (with confirmation for unknown).
   - For each selected device:
       * Copy selected .lic file to TFTP_DIRECTORY.
       * Use Paramiko interactive shell to run:
            execute restore vmlicense tftp <file> <tftp_ip>
         answer "y" to confirmation and capture full output.
       * Check output for:
            "Get VM license from tftp server OK."
            "VM license install succeeded. Rebooting firewall."
         If both present:
           - Rename original license file to append "_<hostname>".
           - Delete the temporary copy from TFTP_DIRECTORY.
         Else:
           - Treat as FAILED and leave files unchanged.

2) Check-only Mode (--check-only):
   - Run `containerlab inspect --format json` and detect FortiGate nodes.
   - Connect to each with Scrapli (admin/admin) and run "get system status".
   - Parse and print for each device:
       * Hostname
       * Mgmt IP
       * Serial-Number
       * License Expiration Date (best-effort)
       * License Status
   - No TFTP or license files are used/required.

Dry-run
-------
- `--dry-run` only applies to Install Mode.
- It performs discovery and selection, prints what *would* be done, but:
    * Does not copy files
    * Does not connect to devices
    * Does not rename or delete files

Assumptions
-----------
- Python 3.8+.
- "containerlab" is installed and available in PATH.
- tftpd-hpa is used as the local TFTP server.
- Scrapli & scrapli-community are installed: `pip install scrapli scrapli-community`.
- Paramiko is installed: `pip install paramiko`.
- FortiGate management IPs are available in `containerlab inspect` as `ipv4_address`.
- License directory is typically NOT the TFTP root; the script copies files to TFTP root.

Author: Stephan Baenisch