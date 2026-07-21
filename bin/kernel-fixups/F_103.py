"""F_103: SUPERSEDED — BPID reprogram re-enabled.

F_102 (NULL fq guard in __poll_portal_fast) provides sufficient protection
against the QMan context_b corruption crash that F_103 was guarding against.
The BPID reprogram is required for true-ZC RX — without it, FMan DMA writes
to kernel page-pool, not XSK UMEM, and xsk_zc_rx_redirect stays at 0.

The BPID reprogram was proven working on this board in June 2026 (0102b
confirmed FMBM_EBMPI showed correct XSK BPID after reprogram).

Superseded 2026-07-21.
"""

print("### F_103: SUPERSEDED — BPID reprogram re-enabled (F_102 guards the crash path)")