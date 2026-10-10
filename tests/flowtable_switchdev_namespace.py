"""A foreign bridge's switchdev events leave the host's hardware flows alone."""

from _flowtable_rig import (command)


async def test_switchdev_foreign_namespace(rig):
    r = rig
    namespace = "ask-switchdev-test"

    async def run(*args):
        return await command(r.target, r.session, *args)

    await r.table()
    await r.exchange()
    before = await r.wait(lambda s: s["entries"] == 2)
    await run("modprobe", "dummy")
    await run("ip", "netns", "add", namespace)
    try:
        await run("ip", "-n", namespace, "link", "add", "br0", "type", "bridge")
        await run("ip", "-n", namespace, "link", "add", "v0", "type", "dummy")
        await run("ip", "-n", namespace, "link", "set", "v0", "master", "br0")
        for name in ("br0", "v0"):
            await run("ip", "-n", namespace, "link", "set", name, "up")
        for state in (4, 3, 0, 3):  # blocking, forwarding, disabled, forwarding
            await run("ip", "netns", "exec", namespace, "bridge", "link", "set",
                      "dev", "v0", "state", str(state))
        for enabled in (1, 0):
            await run("ip", "-n", namespace, "link", "set", "br0", "type", "bridge",
                      "vlan_filtering", str(enabled))
        for operation in ("add", "del"):
            await run("ip", "netns", "exec", namespace, "bridge", "vlan", operation,
                      "dev", "v0", "vid", "100")
            await run("ip", "netns", "exec", namespace, "bridge", "mdb", operation,
                      "dev", "br0", "port", "v0", "grp", "239.86.1.1", "permanent")
    finally:
        await run("ip", "netns", "del", namespace)

    after = await r.state()
    for field in ("entries", "installs", "deletes", "stp_invalidations", "errors"):
        assert before[field] == after[field], (field, before, after)
    # Both directions must still carry the same flow in hardware.
    await r.exchange()
    carried = await r.state()
    for field in ("entries", "installs", "deletes"):
        assert after[field] == carried[field], (field, after, carried)
    old = {row["in"]: row for row in after["flows"]}
    for row in carried["flows"]:
        assert row["cookie"] == old[row["in"]]["cookie"], (after, carried)
        assert int(row["packets"]) - int(old[row["in"]]["packets"]) == 64, (after, carried)
    r.record("switchdev-namespace", {"before": before, "after": after, "carried": carried})
