noshutter.py
========================

Enable all GigabitEthernet interfaces on Cisco XRd routers in a Containerlab lab.
Enable LLDP globally on Cisco XRd routers in a Containerlab lab.

Steps
-----
1. Run `containerlab inspect -f json`
2. Parse node names and kinds
3. For each XRd router:
   - Log in via SSH (scrapli)
   - Run `show ip int brief`
   - Find all `GigabitEthernet` interfaces
   - Configure `no shutdown` on each
   - enable lldp
   - Commit and exit

Requirements
------------
- Python 3.8+
- Scrapli (`pip install scrapli`)
- Containerlab installed and working