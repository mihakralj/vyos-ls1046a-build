import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
try:
    with open(path) as f:
        src = f.read()
except FileNotFoundError:
    print("### dpaa_eth.c: IC key dump - file not found (may not exist on this kernel)")
    sys.exit(0)

# The IC copy size is derived from DPAA_HWA_SIZE = DPAA_PARSE_RESULTS_SIZE + 8 + 8
# We need to increase size to cover the key region at IC offset 0x48.
# Search for DPAA_HWA_SIZE or the buffer prefix configuration.

changes = 0

# Look for DPAA_HWA_SIZE definition and bump it
old_hwa = "#define DPAA_HWA_SIZE"
if old_hwa in src:
    # Find the actual definition line
    for line in src.split('\n'):
        if "DPAA_HWA_SIZE" in line and "define" in line:
            print(f"### dpaa_eth.c: found {line.strip()}")
            break
    
    # Strategy: add DPAA_HWA_KEY_SIZE that includes extra bytes for the KG key
    # The key at IC offset 0x48 is 13 bytes for EKFC=0x001c0006.
    # We want to copy from IC offset 0x40 (hash) through 0x55 (key end) = 22 bytes.
    # But the IC copy already covers hash at 0x40 via DPAA_HASH_RESULTS_SIZE=8.
    # We just need to bump the size to include +13 bytes for the key.
    
    old_hwa_line = "#define DPAA_HWA_SIZE              (DPAA_PARSE_RESULTS_SIZE + DPAA_TIME_STAMP_SIZE + DPAA_HASH_RESULTS_SIZE)"
    new_hwa_line = "#define DPAA_HWA_SIZE              (DPAA_PARSE_RESULTS_SIZE + DPAA_TIME_STAMP_SIZE + DPAA_HASH_RESULTS_SIZE + 32)\t/* +32B for KG key probe */"
    
    if old_hwa_line in src:
        src = src.replace(old_hwa_line, new_hwa_line)
        changes += 1
        print("### dpaa_eth.c: DPAA_HWA_SIZE extended +32B for key probe")
    else:
        print("### dpaa_eth.c: DPAA_HWA_SIZE line not found (tabs vs spaces?)")
else:
    print("### dpaa_eth.c: DPAA_HWA_SIZE not found (kernel version?)")

if changes > 0:
    with open(path, "w") as f:
        f.write(src)
    print(f"### dpaa_eth.c: IC key probe: {changes} change(s) applied")
else:
    print("### dpaa_eth.c: IC key probe: no changes")
