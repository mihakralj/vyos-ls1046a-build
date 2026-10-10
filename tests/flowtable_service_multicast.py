"""Native multicast recovery with live unicast and multicast controls."""
from __future__ import annotations

from _flowtable_service_multicast import (acceleration_stopped, recover)


import pytest



@pytest.mark.parametrize('fault', ['withdrawal', 'install-failslab', 'add-event-failslab', 'delete-event-failslab', 'group-failslab'])
async def test_recovery(multicast_service, fault):
    await recover(multicast_service, fault)


# Complement the bridge cases: each stop method covers both families and learners.
@pytest.mark.parametrize('multicast_service,how', [
    (4, 'stop'), (4, 'disabled'),
    (6, 'service-stop'), (6, 'disabled-config'),
], indirect=['multicast_service'])
async def test_stops_with_acceleration(multicast_service, how):
    await acceleration_stopped(multicast_service, how)
