#!/bin/bash
# test-fmc-minimal.sh — Minimal FMC PCD hash table test
# Tests ExternalHashTableSet path with smallest valid masks (0xf = 16 buckets)
# Run on device after TFTP boot
set -e

echo "=== FMC Minimal Hash Table Test ==="
echo "Date: $(date)"
echo ""

# Step 1: Check kernel for ExternalHashTableSet redirect
echo "--- Step 1: Verify ExternalHashTableSet redirect in kernel ---"
if dmesg | grep -qi "ExternalHashTable\|ehash\|en ext hash"; then
    echo "FOUND: ExternalHashTableSet kernel messages (from boot)"
    dmesg | grep -i "ExternalHashTable\|ehash\|en ext hash" | head -10
else
    echo "OK: No ExternalHashTableSet messages yet (expected — no hash tables created yet)"
fi
echo ""

# Step 2: Check FM PCD device
echo "--- Step 2: Verify FM PCD device ---"
if [ -c /dev/fm0-pcd ]; then
    echo "OK: /dev/fm0-pcd exists"
else
    echo "ERROR: /dev/fm0-pcd not found — FMD shim or SDK driver not loaded"
    exit 1
fi
echo ""

# Step 3: Check fmc binary
echo "--- Step 3: Verify fmc binary ---"
if command -v fmc &>/dev/null; then
    echo "OK: fmc found at $(which fmc)"
    fmc -v 2>&1 || true
else
    echo "ERROR: fmc not found"
    exit 1
fi
echo ""

# Step 4: Deploy minimal-mask cdx_pcd.xml
echo "--- Step 4: Deploy minimal-mask cdx_pcd.xml ---"
PCDFILE="/etc/fmc/config/cdx_pcd_minimal.xml"
cat > "$PCDFILE" << 'XMLEOF'
<!-- Minimal test PCD: all masks=0xf (16 buckets) for ExternalHashTableSet validation -->
<netpcd>
<classification name="cdx_udp4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="14" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tcp4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="14" aging="yes"/>
  </key>
</classification>

<classification name="cdx_udp6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="38" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tcp6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="38" aging="yes"/>
  </key>
</classification>

<classification name="cdx_esp4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="10" aging="yes"/>
  </key>
</classification>

<classification name="cdx_esp6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="22" aging="yes"/>
  </key>
</classification>

<classification name="cdx_multicast4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="10" aging="no"/>
  </key>
</classification>

<classification name="cdx_multicast6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="34" aging="no"/>
  </key>
</classification>

<classification name="cdx_ethernet_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="15" aging="yes"/>
  </key>
</classification>

<classification name="cdx_pppoe_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="11" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tuple3udp4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="8" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tuple3tcp4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="8" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tuple3udp6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="20" aging="yes"/>
  </key>
</classification>

<classification name="cdx_tuple3tcp6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="20" aging="yes"/>
  </key>
</classification>

<classification name="cdx_frag4_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="12" aging="no"/>
  </key>
</classification>

<classification name="cdx_frag6_cc" max="16" masks="yes" shared="true" statistics="byteframe">
  <key>
     <hashtable external="yes" mask="0xf" hashshift="0" keysize="38" aging="no"/>
  </key>
</classification>

<distribution name="cdx_udp4_dist" shared="true">
  <protocols><protocolref name="udp"/></protocols>
  <key>
    <fieldref name="ipv4.src" header_index="last"/>
    <fieldref name="ipv4.dst" header_index="last"/>
    <fieldref name="ipv4.nextp" header_index="last"/>
    <fieldref name="udp.sport"/>
    <fieldref name="udp.dport"/>
  </key>
  <queue count="1" base="0x1000"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_udp4_cc"/>
</distribution>

<distribution name="cdx_tcp4_dist" shared="true">
  <protocols><protocolref name="tcp"/></protocols>
  <key>
    <fieldref name="ipv4.src" header_index="last"/>
    <fieldref name="ipv4.dst" header_index="last"/>
    <fieldref name="ipv4.nextp" header_index="last"/>
    <fieldref name="tcp.sport"/>
    <fieldref name="tcp.dport"/>
  </key>
  <queue count="1" base="0x1010"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tcp4_cc"/>
</distribution>

<distribution name="cdx_udp6_dist" shared="true">
  <protocols><protocolref name="udp"/></protocols>
  <key>
    <fieldref name="ipv6.src" header_index="last"/>
    <fieldref name="ipv6.dst" header_index="last"/>
    <fieldref name="ipv6.nexthdr" header_index="last"/>
    <fieldref name="udp.sport"/>
    <fieldref name="udp.dport"/>
  </key>
  <queue count="1" base="0x1020"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_udp6_cc"/>
</distribution>

<distribution name="cdx_tcp6_dist" shared="true">
  <protocols><protocolref name="tcp"/></protocols>
  <key>
    <fieldref name="ipv6.src" header_index="last"/>
    <fieldref name="ipv6.dst" header_index="last"/>
    <fieldref name="ipv6.nexthdr" header_index="last"/>
    <fieldref name="tcp.sport"/>
    <fieldref name="tcp.dport"/>
  </key>
  <queue count="1" base="0x1030"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tcp6_cc"/>
</distribution>

<distribution name="cdx_ipv4multicast_dist" shared="true">
  <protocols><protocolref name="ipv4"/></protocols>
  <key>
    <fieldref name="ipv4.src"/>
    <fieldref name="ipv4.dst"/>
    <fieldref name="ipv4.nextp"/>
  </key>
  <queue count="1" base="0x1040"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_multicast4_cc"/>
</distribution>

<distribution name="cdx_ipv6multicast_dist" shared="true">
  <protocols><protocolref name="ipv6"/></protocols>
  <key>
    <fieldref name="ipv6.src"/>
    <fieldref name="ipv6.dst"/>
    <fieldref name="ipv6.nexthdr"/>
  </key>
  <queue count="1" base="0x1050"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_multicast6_cc"/>
</distribution>

<distribution name="cdx_pppoe_dist" shared="true">
  <protocols><protocolref name="pppoe"/></protocols>
  <key>
    <fieldref name="ethernet.src"/>
    <fieldref name="ethernet.type"/>
    <fieldref name="pppoe.session_ID"/>
  </key>
  <action type="classification" name="cdx_pppoe_cc"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <queue count="1" base="0x1080"/>
</distribution>

<distribution name="cdx_ethernet_dist" shared="true">
  <protocols><protocolref name="ethernet"/></protocols>
  <key>
    <fieldref name="ethernet.dst"/>
    <fieldref name="ethernet.src"/>
    <fieldref name="ethernet.type"/>
  </key>
  <action type="classification" name="cdx_ethernet_cc"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <queue count="128" base="0x10000"/>
</distribution>

<distribution name="cdx_esp4_dist" shared="true">
  <protocols><protocolref name="ipv4"/><protocolref name="ipsec_esp"/></protocols>
  <key>
    <fieldref name="ipv4.dst"/>
    <fieldref name="ipv4.nextp"/>
    <fieldref name="ipsec_esp.spi"/>
  </key>
  <queue count="1" base="0x1060"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_esp4_cc"/>
</distribution>

<distribution name="cdx_esp6_dist" shared="true">
  <protocols><protocolref name="ipv6"/><protocolref name="ipsec_esp"/></protocols>
  <key>
    <fieldref name="ipv6.dst"/>
    <fieldref name="ipv6.nexthdr"/>
    <fieldref name="ipsec_esp.spi"/>
  </key>
  <queue count="1" base="0x1070"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_esp6_cc"/>
</distribution>

<distribution name="cdx_tup3udp4_dist" shared="true">
  <protocols><protocolref name="udp"/></protocols>
  <key>
    <fieldref name="ipv4.dst" header_index="last"/>
    <fieldref name="ipv4.nextp" header_index="last"/>
    <fieldref name="udp.dport"/>
  </key>
  <queue count="1" base="0x1090"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tuple3udp4_cc"/>
</distribution>

<distribution name="cdx_tup3tcp4_dist" shared="true">
  <protocols><protocolref name="tcp"/></protocols>
  <key>
    <fieldref name="ipv4.dst" header_index="last"/>
    <fieldref name="ipv4.nextp" header_index="last"/>
    <fieldref name="tcp.dport"/>
  </key>
  <queue count="1" base="0x10a0"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tuple3tcp4_cc"/>
</distribution>

<distribution name="cdx_tup3udp6_dist" shared="true">
  <protocols><protocolref name="udp"/></protocols>
  <key>
    <fieldref name="ipv6.dst" header_index="last"/>
    <fieldref name="ipv6.nexthdr" header_index="last"/>
    <fieldref name="udp.dport"/>
  </key>
  <queue count="1" base="0x10b0"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tuple3udp6_cc"/>
</distribution>

<distribution name="cdx_tup3tcp6_dist" shared="true">
  <protocols><protocolref name="tcp"/></protocols>
  <key>
    <fieldref name="ipv6.dst" header_index="last"/>
    <fieldref name="ipv6.nexthdr" header_index="last"/>
    <fieldref name="tcp.dport"/>
  </key>
  <queue count="1" base="0x10c0"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_tuple3tcp6_cc"/>
</distribution>

<distribution name="cdx_ipv4frag_dist" shared="true">
  <protocols><protocolref name="ipv4"/></protocols>
  <key>
    <fieldref name="ipv4.src"/>
    <fieldref name="ipv4.dst"/>
    <fieldref name="ipv4.nextp"/>
  </key>
  <queue count="1" base="0x10d0"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_frag4_cc"/>
</distribution>

<distribution name="cdx_ipv6frag_dist" shared="true">
  <protocols><protocolref name="ipv6"/></protocols>
  <key>
    <fieldref name="ipv6.src"/>
    <fieldref name="ipv6.dst"/>
    <fieldref name="ipv6.nexthdr"/>
  </key>
  <queue count="1" base="0x10e0"/>
  <combine portid="true" offset="16" mask="0xF"/>
  <action type="classification" name="cdx_frag6_cc"/>
</distribution>

<policy name="cdx_ethport_0_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_1_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
 </dist_order>
</policy>

<policy name="cdx_ethport_2_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_3_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_4_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_5_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_6_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_ethport_7_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_port_of2_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>

<policy name="cdx_port_of3_policy">
  <dist_order>
    <distributionref name="cdx_esp4_dist"/>
    <distributionref name="cdx_esp6_dist"/>
    <distributionref name="cdx_udp4_dist"/>
    <distributionref name="cdx_tcp4_dist"/>
    <distributionref name="cdx_udp6_dist"/>
    <distributionref name="cdx_tcp6_dist"/>
    <distributionref name="cdx_ipv4multicast_dist"/>
    <distributionref name="cdx_ipv6multicast_dist"/>
    <distributionref name="cdx_tup3udp4_dist"/>
    <distributionref name="cdx_tup3udp6_dist"/>
    <distributionref name="cdx_pppoe_dist"/>
    <distributionref name="cdx_ethernet_dist"/>
  </dist_order>
</policy>
</netpcd>
XMLEOF

echo "OK: Written $PCDFILE"
echo ""

# Step 5: Backup original and test with minimal PCD
echo "--- Step 5: Run fmc with minimal masks ---"
echo "Command: fmc -c /etc/fmc/config/cdx_cfg.xml -p $PCDFILE -d /etc/fmc/config/hxs_pdl_v3.xml -a"
echo ""
echo "Running fmc..."

# Capture return code
set +e
fmc -c /etc/fmc/config/cdx_cfg.xml -p "$PCDFILE" -d /etc/fmc/config/hxs_pdl_v3.xml -a 2>&1
FMC_RC=$?
set -e

echo ""
echo "fmc exit code: $FMC_RC"
echo ""

# Step 6: Check kernel messages
echo "--- Step 6: Check kernel messages ---"
dmesg | grep -i -E "ehash|hash.*table|FM_PCD|ExternalHash|MAJOR|ASSERT|Invalid|REPORT_ERROR|E_INVALID" | tail -30
echo ""

if [ $FMC_RC -eq 0 ]; then
    echo "SUCCESS: fmc completed without errors!"
    echo ""
    echo "Next steps:"
    echo "  1. Try with original masks: fmc -c /etc/fmc/config/cdx_cfg.xml -p /etc/fmc/config/cdx_pcd.xml -d /etc/fmc/config/hxs_pdl_v3.xml -a"
    echo "  2. Run dpa_app for full ASK PCD programming"
elif [ $FMC_RC -eq 134 ]; then
    echo "FAILED: fmc crashed with SIGABRT (heap corruption)"
    echo "This means the fmc binary itself has a bug — NOT mask-size related"
    echo ""
    echo "Checking dmesg for kernel errors that may have caused corruption..."
    dmesg | tail -20
else
    echo "FAILED: fmc exited with code $FMC_RC"
    dmesg | tail -20
fi