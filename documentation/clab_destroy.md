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