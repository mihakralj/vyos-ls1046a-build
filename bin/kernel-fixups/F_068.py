import sys, re
p = 'drivers/net/ethernet/freescale/fman/fman_pcd.c'
with open(p) as f: s = f.read()
changed = 0

# 1. Revert next_engine 2 -> 3
if 'slot->next_engine    = 2;' in s:
    s = s.replace('slot->next_engine    = 2;', 'slot->next_engine    = 3;', 1)
    changed += 1
    print('### F-068-REVERT: next_engine 2->3')

# 2. Revert CC group table block to simple cc_base_offset = 0
pat = re.compile(r'/\* F-068: CC group table .*?\n\s*\}\n', re.DOTALL)
if pat.search(s):
    s = pat.sub('slot->cc_base_offset = 0;', s, count=1)
    changed += 1
    print('### F-068-REVERT: CC group table -> cc_base_offset=0')

# 3. Restore RCCB code
stub = '/* F-068: CCBS mode'
orig = 'rxport = fman_port_lookup_rx(fman, hw_port_id);'
if stub in s and orig not in s:
    old = '/* F-068: CCBS mode \xe2\x80\x94 no RCCB */\n\treturn 0; // F-068 stub'
    new = 'rxport = fman_port_lookup_rx(fman, hw_port_id);\n\tif (!rxport)\n\t\treturn -ENODEV;\n\terr = fman_port_set_cc_base(rxport, fe_enter_off);'
    if old in s:
        s = s.replace(old, new, 1)
        changed += 1
        print('### F-068-REVERT: RCCB code restored')
    else:
        print('### F-068-REVERT: RCCB stub found but exact match failed (unicode?)')
elif orig in s:
    print('### F-068-REVERT: RCCB already restored')
else:
    print('### F-068-REVERT: RCCB stub not found')

if changed > 0:
    with open(p, 'w') as f: f.write(s)
    print(f'### F-068-REVERT: AC_CC restored ({changed} changes)')
else:
    print('### F-068-REVERT: already at AC_CC baseline')
