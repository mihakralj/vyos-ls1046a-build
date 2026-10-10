"""Static UDP SNAT: forced address/port rewrite, wire checks, retirement."""
from __future__ import annotations

from _flowtable_snat import udp_snat


import pytest



@pytest.mark.parametrize("zero_checksum", [False, True], ids=["checksum", "zero-checksum"])
@pytest.mark.rfc("3022")
async def test_flowtable_udp_snat(connections, zero_checksum, nat_kind='snat'):
    await udp_snat(connections, zero_checksum, nat_kind)
