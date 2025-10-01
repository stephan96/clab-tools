#!/usr/bin/env python3
"""
# clab_destroy.py

Wrapper script for containerlab workflows. Automates running get_clab_config.py before executing containerlab destroy.

## Overview

This script wrappes the "containerlab destroy" command and ensures that the device configurations have been backed up before destroy is executed.

## Requirements

- get_clab_config.py
- tbd

## Usage

From inside your Containerlab lab directory:

python3 clab_destroy.py

## Author
Stephan Baenisch <stephan@baenisch.de>
"""


import subprocess
import sys

def run_cmd(cmd: list) -> int:
    """
    Run a shell command and return its exit code.

    Args:
        cmd (list): Command and arguments to run.

    Returns:
        int: Exit code of the process (0 = success, nonzero = failure).
    """
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except FileNotFoundError:
        print(f"❌ Command not found: {cmd[0]}")
        return 127

def main():
    """
    Main execution logic:
      - Run get_clab_config.py
      - If successful, destroy the lab
      - If failed, prompt user before destroying
    """
    print("▶ Running get_clab_config.py...")
    ret = run_cmd(["python3", "/home/stephan/git/clab-tools/get_clab_config.py"])

    if ret == 0:
        print("✅ get_clab_config.py succeeded. Destroying lab...")
        run_cmd(["/usr/bin/containerlab", "destroy"])
    else:
        print("⚠️ get_clab_config.py failed.")
        choice = input("Do you still want to run containerlab destroy? (y/N): ").strip().lower()
        if choice == "y":
            print("🔥 Forcing containerlab destroy...")
            run_cmd(["/usr/bin/containerlab", "destroy"])
        else:
            print("🚫 Destroy skipped.")

if __name__ == "__main__":
    main()
