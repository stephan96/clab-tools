#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
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
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import paramiko
import yaml
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException

DEFAULT_LICENSE_DIR = "/var/images/fortigate/Fortigate-VM_Lizenzen/"

FG_USERNAME = "admin"
FG_PASSWORD = "admin"


# ---------------------------------------------------------------------
# Containerlab & Node Helpers
# ---------------------------------------------------------------------


def run_containerlab_inspect() -> Dict:
    """Run `containerlab inspect --format json` and return parsed data."""
    result = subprocess.run(
        ["containerlab", "inspect", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    # JSON is valid YAML; we can reuse yaml.safe_load
    data = yaml.safe_load(result.stdout)
    return data


def get_fortigate_nodes(data: Dict) -> Tuple[List[Dict], str]:
    """
    Extract FortiGate nodes from containerlab inspect output.

    Detection rules:
    - Primary: node["name"] starts with "fg-".
    - Fallback: "fortigate" in node["kind"].lower().

    Returns a tuple of (list_of_node_dicts, lab_name).
    """
    nodes: List[Dict] = []

    if not data:
        return [], ""

    # Same structure as your get_fortigate_config_tftp.py:
    # top-level key is lab name, value is list of node dicts
    lab_name = list(data.keys())[0]
    for node in data[lab_name]:
        name = node.get("name", "")
        kind = node.get("kind", "")

        is_fg_name = isinstance(name, str) and name.startswith("fg-")
        is_fg_kind = isinstance(kind, str) and "fortigate" in kind.lower()

        if is_fg_name or is_fg_kind:
            nodes.append(node)

    return nodes, lab_name


def pretty_print_fortis(nodes: List[Dict]) -> None:
    """Pretty-print a list of FortiGate nodes with index."""
    print("\nDetected FortiGate nodes:")
    print("  Idx | Hostname        | Mgmt IP")
    print("  ----+-----------------+-----------------")
    for idx, node in enumerate(nodes, start=1):
        name = node.get("name", "unknown")
        ip_raw = node.get("ipv4_address", "0.0.0.0/0")
        ip = ip_raw.split("/")[0]
        print(f"  {idx:>3} | {name:<15} | {ip:<15}")


# ---------------------------------------------------------------------
# TFTP Server Handling
# ---------------------------------------------------------------------


def check_tftp_server() -> Tuple[Optional[str], Optional[Path]]:
    """
    Check if tftpd-hpa is running and parse IP + directory from
    /etc/default/tftpd-hpa.

    Returns (tftp_ip, tftp_dir) or (None, None) on failure.

    tftp_ip is taken from TFTP_ADDRESS, e.g. "0.0.0.0:69" -> "0.0.0.0".
    tftp_dir is the Path for TFTP_DIRECTORY.
    """
    try:
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "tftpd-hpa"], check=True
        )
    except subprocess.CalledProcessError:
        print("❌ TFTP server (tftpd-hpa) not active or missing.")
        print(
            """
To install and configure a minimal TFTP server (Debian/Ubuntu example):

  sudo apt install -y tftpd-hpa
  sudo vi /etc/default/tftpd-hpa

  TFTP_USERNAME="tftp"
  TFTP_DIRECTORY="/srv/tftp"
  TFTP_ADDRESS="0.0.0.0:69"
  TFTP_OPTIONS="--secure --create"

  sudo mkdir -p /srv/tftp
  sudo chown -R tftp:tftp /srv/tftp
  sudo chmod -R 755 /srv/tftp
  sudo systemctl restart tftpd-hpa
  sudo systemctl enable tftpd-hpa
"""
        )
        return None, None

    tftp_ip = None
    tftp_dir = None

    try:
        with open("/etc/default/tftpd-hpa") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TFTP_ADDRESS="):
                    val = line.split("=", 1)[1].strip().strip('"')
                    tftp_ip = val.split(":", 1)[0]
                elif line.startswith("TFTP_DIRECTORY="):
                    val = line.split("=", 1)[1].strip().strip('"')
                    tftp_dir = val
    except FileNotFoundError:
        print("⚠️ /etc/default/tftpd-hpa not found, cannot parse TFTP settings.")
        return None, None

    if not tftp_ip or not tftp_dir:
        print("⚠️ Could not determine TFTP IP or directory from /etc/default/tftpd-hpa.")
        return None, None

    tftp_dir_path = Path(tftp_dir)
    print(f"✅ Detected TFTP server IP {tftp_ip}, directory {tftp_dir_path}")
    return tftp_ip, tftp_dir_path


# ---------------------------------------------------------------------
# License File Handling
# ---------------------------------------------------------------------


def prompt_license_dir() -> Path:
    """
    Prompt the user for the license directory with a default path.

    Default is DEFAULT_LICENSE_DIR, but the user can override.
    """
    print(
        f"\nLicense directory (press ENTER for default):\n"
        f"  Default: {DEFAULT_LICENSE_DIR}"
    )
    user_dir = input("License directory: ").strip()
    if not user_dir:
        user_dir = DEFAULT_LICENSE_DIR

    license_dir = Path(user_dir).expanduser().absolute()

    if not license_dir.is_dir():
        print(f"❌ License directory does not exist: {license_dir}")
        sys.exit(1)

    print(f"📁 Using license directory: {license_dir}")
    return license_dir


def find_unused_license_files(license_dir: Path) -> List[Path]:
    """
    Find "unused" license files in the given directory.

    Rules:
    - Consider all *.lic files.
    - Treat files that already contain "_fg-" in the filename as "used" and
      skip them (e.g. FGVMSLTM11111111_fg-gfk-1.lic).
    """
    pattern = str(license_dir / "*.lic")
    files = [Path(p) for p in glob.glob(pattern)]

    unused: List[Path] = []
    used_suffix_re = re.compile(r"_fg-[^/]+\.lic$", re.IGNORECASE)

    for f in files:
        if used_suffix_re.search(f.name):
            # Already tagged with hostname, consider "used".
            continue
        unused.append(f)

    unused.sort()
    return unused


def rename_used_license_file(license_path: Path, hostname: str) -> Path:
    """
    Rename the used license file to include the FortiGate hostname.

    Example:
      FGVMSLTM11111111.lic -> FGVMSLTM11111111_fg-gfk-1.lic

    Returns the new Path.
    """
    stem = license_path.stem  # filename without ".lic"
    new_name = f"{stem}_{hostname}.lic"
    new_path = license_path.with_name(new_name)
    license_path.rename(new_path)
    return new_path


# ---------------------------------------------------------------------
# Selection Helpers
# ---------------------------------------------------------------------


def prompt_selection(
    fortis: List[Dict],
    enough_licenses_for_all: bool,
) -> List[Dict]:
    """
    Ask the user which FortiGates should receive licenses.

    If enough_licenses_for_all is True, offer:
      1) all FortiGates
      2) select specific
      3) exit

    Otherwise:
      1) select specific
      2) exit
    """
    if not fortis:
        return []

    while True:
        print("\nLicense installation options:")
        if enough_licenses_for_all:
            print("  1) Install licenses on ALL detected FortiGates")
            print("  2) Install licenses ONLY on specific FortiGates")
            print("  3) Exit / do nothing")
            choice = input("Choose [1-3]: ").strip()
            if choice == "1":
                return fortis
            elif choice == "2":
                break
            elif choice == "3":
                return []
        else:
            print("  1) Install licenses ONLY on specific FortiGates")
            print("  2) Exit / do nothing")
            choice = input("Choose [1-2]: ").strip()
            if choice == "1":
                break
            elif choice == "2":
                return []

    pretty_print_fortis(fortis)
    print(
        "\nEnter the index numbers of the FortiGates to license,\n"
        "as a comma-separated list (e.g. '1,3,4')."
    )

    while True:
        raw = input("Selection: ").strip()
        if not raw:
            print("No selection given. Please enter at least one index.")
            continue

        try:
            indices = [int(x) for x in raw.split(",")]
        except ValueError:
            print("Invalid input. Please enter numbers separated by commas.")
            continue

        chosen: List[Dict] = []
        for idx in indices:
            if 1 <= idx <= len(fortis):
                chosen.append(fortis[idx - 1])
            else:
                print(f"Index {idx} is out of range, ignored.")

        # Deduplicate by node name
        seen = set()
        unique: List[Dict] = []
        for node in chosen:
            name = node.get("name")
            if name not in seen:
                unique.append(node)
                seen.add(name)

        if not unique:
            print("No valid indices selected. Try again.")
            continue

        return unique


# ---------------------------------------------------------------------
# Scrapli & System Status Parsing
# ---------------------------------------------------------------------

def scrapli_connect(host: str) -> Scrapli:
    """
    Establish a Scrapli connection to a FortiGate with static credentials
    admin/admin.
    """
    conn = Scrapli(
        host=host,
        auth_username=FG_USERNAME,
        auth_password=FG_PASSWORD,
        auth_strict_key=False,
        platform="fortinet_fortios",
        transport="paramiko",
    )
    conn.open()
    return conn


def scrapli_get_system_status(conn: Scrapli) -> str:
    """Run 'get system status' and return the raw output."""
    response = conn.send_command("get system status")
    return response.result


def parse_license_status(system_status_output: str) -> Optional[str]:
    """
    Parse "get system status" output and return the license status:

    - "Valid"
    - "Invalid"
    - Any other string if a different status is found (e.g., "Trial")
    - None if no 'License Status:' line is present.
    """
    for line in system_status_output.splitlines():
        line = line.strip()
        if line.startswith("License Status:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                status = parts[1].strip()
                return status or None
    return None


def parse_serial_number(system_status_output: str) -> Optional[str]:
    """Parse 'Serial-Number:' from get system status output."""
    for line in system_status_output.splitlines():
        line = line.strip()
        if line.startswith("Serial-Number:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                serial = parts[1].strip()
                return serial or None
    return None


def parse_license_expiration(system_status_output: str) -> Optional[str]:
    """
    Best-effort parse of license expiration information from get system status.

    Looks for lines containing 'Expiration', 'Expiry', or 'Expires'.
    Returns the text after ':' if present, else the whole line.
    """
    for line in system_status_output.splitlines():
        if re.search(r"(Expiration|Expiry|Expires)", line, re.IGNORECASE):
            line = line.strip()
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip() or line
            return line
    return None


# ---------------------------------------------------------------------
# Paramiko VM License Restore (debug-friendly)
# ---------------------------------------------------------------------


def restore_vmlicense_paramiko(
    host: str,
    hostname: str,
    license_filename: str,
    tftp_ip: str,
    timeout: int = 120,
) -> Tuple[bool, str]:
    """
    Use a raw Paramiko shell to execute:
      execute restore vmlicense tftp <file> <tftp_ip>

    - Wait for 'Do you want to continue? (y/n)' and send 'y'.
    - Read EVERYTHING until the device closes the connection or timeout.
    - Print detailed debug output of all received lines.
    - Treat the operation as success **only if** the output contains BOTH:
        'Get VM license from tftp server OK.'
        'VM license install succeeded. Rebooting firewall.'

    Returns:
        (success: bool, full_output: str)
    """
    print(f"[{hostname}] [DBG] Starting Paramiko vmlicense restore on {host}...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=FG_USERNAME,
        password=FG_PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=20,
    )

    chan = client.get_transport().open_session()
    chan.get_pty()
    chan.invoke_shell()

    cmd = f"execute restore vmlicense tftp {license_filename} {tftp_ip}\n"
    print(f"[{hostname}] [DBG] Sending command: {cmd.strip()}")
    chan.send(cmd)

    buff = ""
    answered = False
    start = time.time()

    while True:
        if chan.recv_ready():
            data = chan.recv(4096).decode(errors="ignore")
            if data:
                buff += data
                for line in data.splitlines():
                    print(f"[{hostname}] [DBG] RECV: {line}")

                if ("Do you want to continue? (y/n)" in buff) and not answered:
                    print(f"[{hostname}] [DBG] Sending 'y' to confirmation prompt")
                    chan.send("y\n")
                    answered = True

        if chan.exit_status_ready():
            if chan.recv_ready():
                continue
            break

        if time.time() - start > timeout:
            print(f"[{hostname}] [DBG] TIMEOUT waiting for vmlicense restore output.")
            break

        time.sleep(0.5)

    chan.close()
    client.close()

    print(f"[{hostname}] --- vmlicense raw output start ---")
    print(buff)
    print(f"[{hostname}] --- vmlicense raw output end ---")

    success_mark_1 = "Get VM license from tftp server OK." in buff
    success_mark_2 = "VM license install succeeded. Rebooting firewall." in buff

    if success_mark_1 and success_mark_2:
        print(f"[{hostname}] ✅ VM license install reported SUCCESS (Paramiko).")
        return True, buff

    print(
        f"[{hostname}] ❌ Expected success strings not found in output; "
        f"treating as failure."
    )
    return False, buff


# ---------------------------------------------------------------------
# License Check-Only Mode
# ---------------------------------------------------------------------


def run_license_check_only() -> int:
    """
    License check mode:
    - Discover FortiGates via containerlab
    - Connect to each (Scrapli) and run 'get system status'
    - Print a summary table: Hostname, Mgmt IP, Serial, Expiration, Status
    """
    print("=== FortiGate VM License Check (check-only mode) ===")

    print("\n[STEP 1] Discovering FortiGate nodes via `containerlab inspect`...")
    try:
        lab_data = run_containerlab_inspect()
    except subprocess.CalledProcessError as exc:
        print(f"❌ Failed to run containerlab inspect: {exc}")
        return 1

    nodes, labname = get_fortigate_nodes(lab_data)
    if not nodes:
        print("⚠️ No FortiGate nodes (fg-*) found in containerlab topology.")
        return 0

    pretty_print_fortis(nodes)

    print("\n[STEP 2] Checking license status on all FortiGates...\n")

    results = []  # list of (hostname, mgmt_ip, serial, expiry, status, note)

    for node in nodes:
        hostname = node.get("name", "unknown")
        ip_raw = node.get("ipv4_address", "0.0.0.0/0")
        mgmt_ip = ip_raw.split("/")[0]

        print(f"--- {hostname} ({mgmt_ip}) ---")
        try:
            conn = scrapli_connect(mgmt_ip)
        except ScrapliException as exc:
            print(f"  ❌ Scrapli connection failed: {exc}")
            results.append((hostname, mgmt_ip, "N/A", "N/A", "UNREACHABLE", str(exc)))
            continue

        try:
            output = scrapli_get_system_status(conn)
            license_status = parse_license_status(output) or "UNKNOWN"
            serial = parse_serial_number(output) or "N/A"
            expiry = parse_license_expiration(output) or "N/A"

            print(f"  Serial-Number      : {serial}")
            print(f"  License Status     : {license_status}")
            print(f"  License Expiration : {expiry}")

            results.append((hostname, mgmt_ip, serial, expiry, license_status, ""))

        except ScrapliException as exc:
            print(f"  ❌ Error while running get system status: {exc}")
            results.append((hostname, mgmt_ip, "N/A", "N/A", "ERROR", str(exc)))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Print summary table
    print("\n=== LICENSE SUMMARY ===")
    print(
        f"{'Hostname':15} {'Mgmt IP':15} {'Serial-Number':20} "
        f"{'Expiration':25} {'Status':12}"
    )
    print("-" * 90)
    for hostname, mgmt_ip, serial, expiry, status, note in results:
        print(
            f"{hostname:15} {mgmt_ip:15} {serial:20} "
            f"{expiry:25} {status:12}"
        )
        if note:
            print(f"    Note: {note}")

    print("\nDone (check-only mode).")
    return 0


# ---------------------------------------------------------------------
# Install Mode (original behavior, improved)
# ---------------------------------------------------------------------


def run_install_mode(dry_run: bool = False) -> int:
    print("=== FortiGate VM License Installer (fortilic.py) ===")
    if dry_run:
        print("*** DRY-RUN MODE ENABLED: No SSH, no changes, no file renames. ***")

    # 1) Check TFTP server
    print("\n[STEP 1] Checking local TFTP server (tftpd-hpa)...")
    tftp_ip, tftp_dir = check_tftp_server()
    if not tftp_ip or not tftp_dir:
        print("Aborting due to TFTP server problem.")
        return 1

    # 2) Discover FortiGates via containerlab
    print("\n[STEP 2] Discovering FortiGate nodes via `containerlab inspect`...")
    try:
        lab_data = run_containerlab_inspect()
    except subprocess.CalledProcessError as exc:
        print(f"❌ Failed to run containerlab inspect: {exc}")
        return 1

    nodes, labname = get_fortigate_nodes(lab_data)
    if not nodes:
        print("⚠️ No FortiGate nodes (fg-*) found in containerlab topology.")
        return 0

    pretty_print_fortis(nodes)

    # 3) License directory & files
    print("\n[STEP 3] Selecting license files...")
    license_dir = prompt_license_dir()
    available_licenses = find_unused_license_files(license_dir)

    num_fortis = len(nodes)
    num_licenses = len(available_licenses)

    print(
        f"\nFound {num_licenses} unused .lic files in {license_dir}\n"
        f"Detected {num_fortis} FortiGate(s) in the topology."
    )

    # Hard stop if no unused licenses
    if num_licenses == 0:
        print(
            "❌ No unused license files available.\n"
            "   All .lic files in the directory are already tagged "
            "with a hostname (_fg-*) or there are none at all.\n"
            "   Nothing to do."
        )
        return 0

    enough_for_all = num_licenses >= num_fortis
    if enough_for_all:
        print("✅ There are enough license files to cover ALL FortiGates.")
    else:
        print(
            "⚠️ There are NOT enough license files for all FortiGates.\n"
            "   You will need to choose a subset of devices to license."
        )

    # 4) Node selection
    chosen_nodes = prompt_selection(nodes, enough_for_all)
    if not chosen_nodes:
        print("No FortiGates selected. Nothing to do.")
        return 0

    if len(available_licenses) < len(chosen_nodes):
        print(
            "\n⚠️ Fewer license files than selected FortiGates.\n"
            "   Only the first devices will be processed in this run."
        )

    print(f"\n[INFO] Using TFTP server IP {tftp_ip} from tftpd-hpa configuration.")
    print(f"[INFO] TFTP directory is {tftp_dir}")
    print(
        "       Make sure your license files are reachable by the TFTP server "
        "(this script copies them into the TFTP directory for you).\n"
    )

    summary = []

    # 5) Process each selected FortiGate
    for node in chosen_nodes:
        hostname = node.get("name", "unknown")
        ip_raw = node.get("ipv4_address", "0.0.0.0/0")
        mgmt_ip = ip_raw.split("/")[0]

        print(f"\n=== Processing {hostname} ({mgmt_ip}) ===")

        if not available_licenses:
            msg = "No license files remaining."
            print("  " + msg)
            summary.append((hostname, "SKIPPED", msg))
            continue

        # Assign the first available license to this node
        license_path = available_licenses.pop(0)
        license_filename = license_path.name

        print(f"  Assigned license file: {license_filename}")

        if dry_run:
            msg = (
                f"[DRY-RUN] Would copy {license_path} to {tftp_dir / license_filename}, "
                f"connect to {mgmt_ip} as {FG_USERNAME}/{FG_PASSWORD}, "
                "run 'get system status', decide based on License Status, "
                f"then execute 'execute restore vmlicense tftp {license_filename} {tftp_ip}', "
                "rename license file with hostname and clean up TFTP copy."
            )
            print("  " + msg)
            summary.append((hostname, "DRY-RUN", msg))
            continue

        # Scrapli connection for get system status / license decision
        try:
            conn = scrapli_connect(mgmt_ip)
        except ScrapliException as exc:
            msg = f"Scrapli connection failed: {exc}"
            print("  " + msg)
            summary.append((hostname, "FAILED", msg))
            # Put license back so it can be used later
            available_licenses.insert(0, license_path)
            continue

        try:
            print("  Running 'get system status'...")
            status_output = scrapli_get_system_status(conn)
            status = parse_license_status(status_output)
            print("  License Status:", status if status else "UNKNOWN")

            if status == "Valid":
                ans = input(
                    f"  License is VALID on {hostname}. "
                    "Upload a NEW license anyway? [y/N]: "
                ).strip().lower()
                if ans not in ("y", "yes"):
                    msg = "License valid, user chose NOT to replace."
                    print("  " + msg)
                    summary.append((hostname, "SKIPPED", msg))
                    conn.close()
                    # Put license back so it stays unused
                    available_licenses.insert(0, license_path)
                    continue
            elif status is None:
                ans = input(
                    f"  Could not determine license status on {hostname}. "
                    "Upload license anyway? [y/N]: "
                ).strip().lower()
                if ans not in ("y", "yes"):
                    msg = "Unknown license status, user chose NOT to upload."
                    print("  " + msg)
                    summary.append((hostname, "SKIPPED", msg))
                    conn.close()
                    available_licenses.insert(0, license_path)
                    continue
            else:
                # "Invalid" or other status - proceed without extra confirmation
                print(f"  License status is '{status}', proceeding with upload.")

            conn.close()

        except ScrapliException as exc:
            msg = f"Error while processing {hostname} via Scrapli: {exc}"
            print("  " + msg)
            summary.append((hostname, "FAILED", msg))
            try:
                conn.close()
            except Exception:
                pass
            available_licenses.insert(0, license_path)
            continue

        # --- COPY LICENSE TO TFTP DIRECTORY ---
        tftp_license_path = tftp_dir / license_filename
        try:
            shutil.copy2(license_path, tftp_license_path)
            print(f"  Copied license file to TFTP directory: {tftp_license_path}")
        except Exception as exc:
            msg = f"Failed to copy license to TFTP directory: {exc}"
            print("  " + msg)
            summary.append((hostname, "FAILED", msg))
            # Put license back so it can be retried later
            available_licenses.insert(0, license_path)
            continue

        # --- RESTORE LICENSE USING PARAMIKO ---
        ok, restore_output = restore_vmlicense_paramiko(
            host=mgmt_ip,
            hostname=hostname,
            license_filename=license_filename,
            tftp_ip=tftp_ip,
        )

        if not ok:
            msg = (
                "License restore did not report success; "
                "leaving license file unchanged and keeping TFTP copy."
            )
            print("  " + msg)
            summary.append((hostname, "FAILED", msg))
            # do NOT rename license file
            continue

        # --- SUCCESS: RENAME ORIGINAL LICENSE FILE ---
        new_path = rename_used_license_file(license_path, hostname)
        print(f"  Renamed used license file to: {new_path.name}")

        # --- CLEANUP TFTP FILE ---
        if tftp_license_path.exists():
            try:
                tftp_license_path.unlink()
                print(f"  Cleaned up temporary TFTP file: {tftp_license_path}")
            except Exception as exc:
                print(f"  ⚠️ Failed to clean up TFTP file {tftp_license_path}: {exc}")
        else:
            print(f"  ⚠️ TFTP file not found for cleanup: {tftp_license_path}")

        summary.append(
            (
                hostname,
                "SUCCESS",
                f"License installed from {new_path.name} (TFTP {tftp_ip}). "
                f"Device rebooting.",
            )
        )

    # Summary
    print("\n=== SUMMARY ===")
    for host, result, msg in summary:
        print(f"{host:15} : {result:7} - {msg}")

    print("\nDone (install mode).")
    return 0


# ---------------------------------------------------------------------
# Main Entry
# ---------------------------------------------------------------------

def main(mode: str, dry_run: bool = False) -> int:
    if mode == "check":
        return run_license_check_only()

    if mode == "install":
        return run_install_mode(dry_run=False)

    if mode == "dry-run":
        return run_install_mode(dry_run=True)

    print(f"❌ Unknown mode '{mode}'")
    return 1


if __name__ == "__main__":
    DESCRIPTION = (
        "FortiGate VM license installer and license checker for "
        "Containerlab environments."
    )

    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        add_help=True,
        usage=(
            "\n\n  fortilic.py --install\n"
            "  fortilic.py --dry-run\n"
            "  fortilic.py --check\n\n"
            "No default action. You must specify an option."
        ),
    )

    mode_group = parser.add_mutually_exclusive_group()

    mode_group.add_argument(
        "--install",
        action="store_true",
        help="Install license(s) to FortiGate VMs."
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Run install logic but perform NO actions (no SSH, no TFTP, no renaming)."
    )
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Check license status on all FortiGates (no installation)."
    )

    args = parser.parse_args()

    # If user provided no options → show help, description, and exit
    if not (args.install or args.dry_run or args.check):
        #parser.print_usage()
        print(f"\n{DESCRIPTION}\n")
        parser.print_usage()
        print(
            "\n❌ No mode selected. "
            "Please choose --install, --dry-run, or --check.\n"
        )
        raise SystemExit(1)

    # Determine mode
    if args.check:
        mode = "check"
    elif args.dry_run:
        mode = "dry-run"
    elif args.install:
        mode = "install"

    raise SystemExit(main(mode=mode))





# def main(mode: str = "install", dry_run: bool = False) -> int:
#     if mode == "check":
#         if dry_run:
#             print("⚠️ Dry-run flag has no effect in check-only mode.")
#         return run_license_check_only()
#     else:
#         return run_install_mode(dry_run=dry_run)


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(
#         description="FortiGate-VM license installer / checker using TFTP and Scrapli."
#     )
#     parser.add_argument(
#         "-n",
#         "--dry-run",
#         action="store_true",
#         help="Dry-run mode (install mode only): do not connect or change anything, "
#              "just show what would be done.",
#     )
#     parser.add_argument(
#         "--check-only",
#         action="store_true",
#         help="Check-only mode: do not install licenses, only summarize license "
#              "status for all FortiGates.",
#     )
#     args = parser.parse_args()

#     mode = "check" if args.check_only else "install"
#     raise SystemExit(main(mode=mode, dry_run=args.dry_run))
