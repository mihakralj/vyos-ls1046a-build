"""Run the TCP lifetime proofs with a forced native SNAT address and port."""


import pytest

from _flowtable_tcp import (tcp_transfer_expiry_fin as _fin, tcp_retransmit_withdraw_rst as _rst)

pytestmark = pytest.mark.parametrize("rig", ["tcp"], indirect=True)


async def test_expiry_fin(tcp_snat):
    await _fin(tcp_snat)


async def test_retransmit_withdraw_rst(tcp_snat):
    await _rst(tcp_snat)
