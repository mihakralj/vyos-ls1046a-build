"""Kernel-log checks shared by test and profile fixture lifecycles."""

from contextlib import asynccontextmanager

from ask_orch.artifacts import artifact_dir, record


def verify_capture(result, nodeid, allowlist):
    from _dmesg_allowlist import filter_splats

    assert result.get("complete") is True, ("incomplete kernel log capture", result)
    assert "splats" in result, ("missing kernel log verdict", result)
    splats = filter_splats(result["splats"], nodeid, allowlist)
    assert not splats, f"kernel splats during {nodeid}: " + "; ".join(splats[:3])


@asynccontextmanager
async def capture_window(agent, session, nodeid, allowlist, *, name="kernel", failed_check=lambda: False):
    cap_id = await agent.capture_start(session)
    failed = False
    try:
        yield cap_id
    except BaseException:
        failed = True
        raise
    finally:
        result = await agent.capture_stop(session, cap_id)
        record(name, result, nodeid=nodeid)
        try:
            verify_capture(result, nodeid, allowlist)
        except BaseException:
            failed = True
            raise
        finally:
            artifact = result.get("artifact")
            if artifact:
                if failed or failed_check():
                    data = await agent.artifact(session, artifact["id"])
                    (artifact_dir(nodeid) / f"{name}-log.json").write_bytes(data)
                await agent.request(session, "artifact/release", {"id": artifact["id"]})
