ospf-wizard.py
==============

Automates OSPF configuration for Cisco XRd routers in a Containerlab lab.

OVERVIEW
--------
- Reads Loopback0 IPv4 from device configuration (used as router-id).
- Configures OSPF processes per router role.
- Loopback0 is configured as passive in all OSPF processes.
- Enables OSPF on router-to-router links according to predefined rules.
- Commits configuration and exits.

ROUTER ROLE CLASSIFICATION
--------------------------
- ce : Customer Edge routers (name starts with "CE")
- ch : Core/HQ routers (name starts with "ch")
- cc : Core/Compute routers (name starts with "cc")
- cr : Core Route Reflector routers (name starts with "cr")
- sa : Service/Aggregation routers (name starts with "sa")
- dh : Distribution High routers (name starts with "dh")
- ds : Distribution Secondary routers (name starts with "ds")
- ah : Aggregation/Hybrid routers (name starts with "ah")
- as : Access routers (name starts with "as")
- core : Generic core routers (name starts with "c" or "s" not matched above)
- d : Generic distribution routers (name starts with "d")
- other : Routers with unknown role (any other name pattern)

OSPF PROCESS ALLOCATION
-----------------------
- cc, cr, sa, core : OSPF process 1
- ch : OSPF processes 1 and 10
- dh, ds : OSPF process 10
- ah : OSPF processes 10 and 100
- as : OSPF process 100
- ce : no OSPF process
- other : no OSPF process (unless overridden with force_all option)

LOOPBACK0 CONFIGURATION
-----------------------
- Loopback0 interface is always included in OSPF processes.
- Configured as passive.
- Router-ID is set to Loopback0 IPv4 address.

LINK-TO-OSPF RULES
------------------
All rules assume link endpoints are parsed as (local_node:local_iface, neighbor_node:neighbor_iface):

1. Core & Service Links
   - Links between all "c" and "s" routers: process 1, area 0.0.0.0
   - Links between ch routers:
       - If interface ends with 0/0/0/0: process 1, area 0.0.0.0
       - Otherwise: process 10, area 0.0.0.10
   - Links between c and d routers: process 10, area 0.0.0.10

2. Distribution & Aggregation Links
   - Between all d routers and between d and ah routers: process 10, area 0.0.0.10

3. Access & Aggregation Links
   - Between all a routers and between ah and a routers: process 100, area 0.0.0.100
   - Special case between ahrg and ahrb:
       - First link: process 10, area 0.0.0.10
       - Second link: process 100, area 0.0.0.100

4. CE Links
   - Links involving CE routers are ignored (no OSPF)

5. Fallback Option
   - If *all* routers are classified as "other" and user agrees, process 1 / area 0 is applied to all routers and links.

DEBUGGING
---------
- Set `DEBUG = True` at the top of the script to enable detailed link parsing output.
- LLDP neighbor parsing is used to detect router-to-router links dynamically.

USAGE
-----
1. Ensure containerlab lab is deployed.
2. Run script:
    $ ./ospf-wizard.py
3. Follow prompt if all routers are "other" to force OSPF on all devices.

AUTHOR
------
Stephan