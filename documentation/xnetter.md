xnetter.py

Assign point-to-point IPv4 /31 and IPv6 /127 addresses to links defined in the
current containerlab *.clab.yml and apply them to devices (Cisco XRd).

Behavior:
- Run `containerlab inspect -f json` to get node names and mgmt IPs
- Parse the *.clab.yml links
- For each link (pair of endpoints) allocate next /31 from 10.0.0.0/24
  and map IPv6 /127 from fc00::/7 by embedding IPv4 into low 32 bits
- Translate short if names like "Gi0-0-0-1" -> "GigabitEthernet0/0/0/1"
- Configure interfaces on Cisco XRd devices:
    interface <iface>
      ipv4 address 10.x.x.x 255.255.255.254
      ipv6 address <fc00:...>/127
      description To neighbor <peer_name> <peer_iface>
      no shutdown
    commit

Note: This script targets Cisco XRd nodes only. It can be extended for other
vendors.