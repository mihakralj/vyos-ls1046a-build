"""Focused IPv4 TCP acceptance using the two-port flowtable rig."""
from __future__ import annotations

from _flowtable_tcp import tcp_retransmit_withdraw_rst, tcp_transfer_expiry_fin


import pytest


pytestmark = pytest.mark.parametrize("rig", ["tcp"], indirect=True)


@pytest.mark.rfc("793")
async def test_transfer_expiry_fin(rig):
    await tcp_transfer_expiry_fin(rig)


async def test_retransmit_withdraw_rst(rig):
    await tcp_retransmit_withdraw_rst(rig)
