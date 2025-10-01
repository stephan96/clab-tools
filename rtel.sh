#!/usr/bin/expect

 # set timeout 20
 set ip [lindex $argv 1]
 set vendor [lindex $argv 0]
 # set port "23"

 # no logging on console
 log_user 0

 if { $ip == "" || $vendor == "" } {
 puts "\nUsage: rtel.sh <vendor: Huawei Containerlab -> hui   Cisco XRd Containerlab -> xrd> <router ip address>"
 puts "Example: rtel.sh hui 1.1.1.1\n"
 puts "Remember to store your username and password in the script and ensure to set 'chmod 700'!\n"
 exit 1
 }

# Huawei Containerlab
 set user_hui "admin"
 set pw_hui "admin"

# Cisco XRd Containerlab
 set user_xrd "clab"
 set pw_xrd "clab@123"



 # Huawei Containerlab
 if {$vendor eq "hui"} {
 spawn ssh -o StrictHostKeyChecking=no $user_hui@$ip
 expect "Enter password:"
 send "$pw_hui\r"
 interact
 }

 # Cisco XRd Containerlab
 if {$vendor eq "xrd"} {
 spawn ssh -o StrictHostKeyChecking=no $user_xrd@$ip
 expect "Password:";
 send "$pw_xrd\r";
 interact
 }


 # Example with specific SSH Algorithms
 if {$vendor eq "c"} {
 spawn ssh -o KexAlgorithms=diffie-hellman-group1-sha1 -o HostKeyAlgorithms=+ssh-dss $user@$ip
 expect "password:";
 send "$pw_red\r";
 interact
 }



 #else {
 # nothing yet
 #puts "Else Schleife\n"
 #}

 #interact

