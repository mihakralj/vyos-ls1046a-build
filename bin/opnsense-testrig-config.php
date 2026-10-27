<?php
// Put an OPNsense NXP-ASK DUT (Mono Gateway LS1046A) into the bin/testrig-* data-path layout.
// Run on the DUT as root:  php /tmp/opnsense-testrig-config.php /path/to/authorized_key.pub  &&  reboot
// Edits /conf/config.xml directly (backup: /conf/config.xml.pre-testrig). Idempotent.
//
// Result: dtsec1 (eth0, left RJ45) = WAN, DHCP v4/v6 = management (untouched);
//         dtsec3 10.99.1.185/24 fd99:1::185/64, dtsec3.10 10.99.10.185/24 fd99:10::185/64,
//         dtsec4 10.99.2.185/24 fd99:2::185/64, dtsec4.20 10.99.20.185/24 fd99:20::185/64
//         (same data-plane addresses as the VyOS and OpenWrt DUTs, so a cable move swaps DUTs),
//         pf ENABLED (CMM offloads pf states) with inbound pass rules on the four test interfaces,
//         the 'testrig' nat anchor plugin, cmm_enable, VLAN devices named vlan01/vlan02, outbound NAT disabled,
//         sshd enabled with root login + the given public key (password auth stays on).
$cfgfile = '/conf/config.xml';
$key = trim(file_get_contents($argv[1] ?? ''));
if ($key === '') { fwrite(STDERR, "usage: php $argv[0] <pubkey-file>\n"); exit(1); }
if (!file_exists("$cfgfile.pre-testrig")) copy($cfgfile, "$cfgfile.pre-testrig");

$x = simplexml_load_file($cfgfile);
function sub($p, $n) { return isset($p->$n) ? $p->$n : $p->addChild($n); }
function setv($p, $n, $v) { $c = sub($p, $n); $c[0] = $v; return $c; }

$sys = $x->system;
unset($sys->disablefilter);
setv($sys, 'disablereplyto', '1'); // else replies to same-subnet mgmt hosts are sent to the gateway
$ssh = sub($sys, 'ssh');
setv($ssh, 'enabled', 'enabled');
setv($ssh, 'permitrootlogin', '1');
setv($ssh, 'passwordauth', '1');
foreach ($sys->user as $u) {
	if ((string)$u->name === 'root') {
		setv($u, 'authorizedkeys', base64_encode($key . "\n"));
		setv($u, 'shell', '/bin/sh'); // the harness sends POSIX sh one-liners over ssh; csh breaks on them
	}
}

// outbound NAT off
$nat = sub($x, 'nat');
setv(sub($nat, 'outbound'), 'mode', 'disabled');

// DNS / DHCP services off (dnsmasq DNS+DHCP, Unbound)
if (isset($x->dnsmasq)) setv($x->dnsmasq, 'enable', '0');
if (isset($x->OPNsense->unboundplus->general)) setv($x->OPNsense->unboundplus->general, 'enabled', '0');

// VLAN devices
$vlans = sub($x, 'vlans');
unset($vlans->vlan);
$vname = ['dtsec3' => 'vlan01', 'dtsec4' => 'vlan02'];
foreach ([['dtsec3', 10], ['dtsec4', 20]] as [$p, $t]) {
	$v = $vlans->addChild('vlan');
	$v->addAttribute('uuid', sprintf('%08x-0000-4000-8000-%012x', crc32("$p.$t"), $t));
	$v->addChild('if', $p);
	$v->addChild('tag', (string)$t);
	$v->addChild('pcp', '0');
	$v->addChild('proto');
	$v->addChild('descr', "testrig $p.$t");
	$v->addChild('vlanif', $vname[$p]);
}

// data-plane interfaces
$ifs = $x->interfaces;
foreach (['lan', 't3', 't3v10', 't4', 't4v20'] as $n) unset($ifs->$n);
// The factory LAN (dtsec0, 192.168.1.1/24 + DHCP server) collides with the real router at 192.168.1.1.
// rc.bootup re-creates it whenever no interface named 'lan' exists, so an unused placeholder 'lan' (dtsec0, left unconnected, 10.254.0.1/30) keeps it from doing so.
foreach (['dhcpd', 'dhcpdv6'] as $n) if (isset($x->$n)) unset($x->$n->lan);
$wan = $ifs->wan;
setv($wan, 'descr', 'mgmt');
unset($wan->blockpriv, $wan->blockbogons); // the mgmt LAN is RFC1918
$plan = [
	'lan'   => ['dtsec0',    '10.254.0.1',   'fd99:254::1'],
	't3'    => ['dtsec3',    '10.99.1.185',  'fd99:1::185'],
	't3v10' => ['vlan01', '10.99.10.185', 'fd99:10::185'],
	't4'    => ['dtsec4',    '10.99.2.185',  'fd99:2::185'],
	't4v20' => ['vlan02', '10.99.20.185', 'fd99:20::185'],
];
foreach ($plan as $n => [$dev, $v4, $v6]) {
	$i = $ifs->addChild($n);
	$i->addChild('enable', '1');
	$i->addChild('if', $dev);
	$i->addChild('descr', $n);
	$i->addChild('ipaddr', $v4);
	$i->addChild('subnet', $n === 'lan' ? '30' : '24');
	$i->addChild('ipaddrv6', $v6);
	$i->addChild('subnetv6', $n === 'lan' ? '126' : '64');
}

// inbound pass rules on the test interfaces (v4+v6)
$filter = sub($x, 'filter');
unset($filter->rule);
foreach (['wan', 't3', 't3v10', 't4', 't4v20'] as $n) {
	foreach (['inet', 'inet6'] as $fam) {
		$r = $filter->addChild('rule');
		$r->addAttribute('uuid', sprintf('%08x-0000-4000-8000-%012x', crc32("$n$fam"), 1));
		$r->addChild('type', 'pass');
		$r->addChild('interface', $n);
		$r->addChild('ipprotocol', $fam);
		$r->addChild('direction', 'in');
		$r->addChild('quick', '1');
		$r->addChild('statetype', 'keep state');
		$r->addChild('descr', "testrig pass $n $fam");
		$s = $r->addChild('source'); $s->addChild('any', '1');
		$d = $r->addChild('destination'); $d->addChild('any', '1');
	}
}

// pf nat anchor for runtime NAT cells + CMM
@mkdir('/usr/local/etc/inc/plugins.inc.d', 0755, true);
file_put_contents('/usr/local/etc/inc/plugins.inc.d/testrig.inc', "<?php\nfunction testrig_firewall(\\OPNsense\\Firewall\\Plugin \$fw)\n{\n    \$fw->registerAnchor(\"testrig\", \"nat\", 0, \"head\");\n}\n");
exec('sysrc -f /etc/rc.conf.local cmm_enable=YES');

$dom = dom_import_simplexml($x)->ownerDocument;
$dom->formatOutput = true;
$dom->save($cfgfile);
echo "config.xml updated; reboot to apply\n";
