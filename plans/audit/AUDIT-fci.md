# AUDIT-fci

**Module:** `ASK/fci/` — FCI (Fast Control Interface) kernel OOT module
**Origin:** Mindspeed Technologies (2007), Freescale (2014–2016), NXP (2017, 2021)
**License:** GPL-2.0+
**Audit lens:** D1..D18 / O1..O10 (per `plans/ASK-USERSPACE-AUDIT-PLAN.md`)
**Date:** refresh of `ASK-CODE-REVIEW.md §4` and `ASK-CODE-QUALITY.md §1–2`

---

## Findings summary (P0/P1/P2 table)

| ID | Severity | Class | File:Line | Title |
|---|---|---|---|---|
| F-01 | **P0** | D4 / D12 | `ASK/fci/fci.c:480, 533–548` | `fci_msg->length` (user-supplied u16) is forwarded to `comcerto_fpp_send_command()` without bounding to actual netlink payload size. |
| F-02 | **P0** | D12 | `ASK/fci/fci.c:455–510` | No `capable(CAP_NET_ADMIN)` / `netlink_capable(skb, CAP_NET_ADMIN)` check on inbound messages. |
| F-03 | **P0** | D6 / D4 | `ASK/fci/fci.c:457, 466` | `nlh = (struct nlmsghdr *)skb->data` and `fci_msg = nlmsg_data(nlh)` are dereferenced with no `nlmsg_ok()` / `nlmsg_len()` size check. |
| F-04 | **P1** | D6 | `ASK/fci/fci.c:475–477` | `nlmsg_put(nskb, ...)` return value not checked before `nlmsg_data(rep)` is called — NULL deref on tail-room exhaustion. |
| F-05 | **P1** | D17 | `ASK/fci/fci.c:655, 673` | `proc_create("fci", ...)` return value ignored; `remove_proc_entry` called unconditionally. |
| F-06 | **P1** | D5 / D6 | `ASK/fci/fci.c:432–447` | `nlmsg_put()` return not checked in `fci_outbound_fe_data()`; on NULL the skb leaks and `nlmsg_data(NULL)` is dereferenced. |
| F-07 | **P1** | D11 / D2 | `ASK/fci/fci.c:189, 391` | `in_interrupt()` gating for GFP — fragile to context migration of FE callback. |
| F-08 | **P2** | D14 | `ASK/fci/fci.c:70, 207, 326, 333, 367, 396, 575, 648, 670` | Legacy `printk(KERN_ERR …)` instead of `pr_err()` + `pr_fmt`. |
| F-09 | **P2** | O7 | `ASK/fci/fci.c:54` | `static FCI *this_fci;` lacks `__read_mostly`. |
| F-10 | **P2** | O1 | `ASK/fci/fci.c:292–308` | `fci_type_to_nl_type()` is a switch over a single-element enum. |
| F-11 | **P2** | D16 | `ASK/fci/fci.h:44–53` | Macro hygiene: `#define FCI_NL_FF0` etc. — no whitespace between identifier and value; `FCI_MSG_SIZE` looks function-like but is value-style. |
| F-12 | **P2** | D15 | `ASK/fci/fci.c:472–490` | `fci_rep->payload` tail not zeroed before FE call — uninit kernel memory may escape to userspace. |
| F-13 | **P2** | D18 | `ASK/fci/fci.h:59–65` | No `_Static_assert(sizeof(FCI_MSG) == 516, ...)` — implicit on-wire ABI. |

---

## Module inventory

| File | LoC | Role |
|---|---:|---|
| `ASK/fci/fci.c` | 681 | netlink kernel handler, init/exit, /proc/fci |
| `ASK/fci/fci.h` | 100 | private header (FCI struct, debug macros, FE entrypoints) |
| `ASK/fci/lib/include/libfci.h` | (~) | userspace API (out-of-scope for kernel-side audit) |
| `ASK/fci/lib/src/libfci.c` | (~) | userspace netlink wrapper |

**Kernel-side ABI surface:**
- Netlink protocol: `NETLINK_FF` (raw protocol number, **not** generic netlink).
- Multicast group: `NL_FF_GROUP` (= 1).
- /proc node: `/proc/fci` (read-only stats).
- External symbols consumed: `comcerto_fpp_send_command()`, `comcerto_fpp_register_event_cb()` (defined in cdx kernel module).

No `module_param`, no sysctl, no chardev, no ioctl. Single attack vector is the netlink socket.

---

## Re-verification of prior findings

### Prior CRIT #1 — "FCI netlink — no payload length validation" (`ASK-CODE-QUALITY.md §1`)

**Prior cite:** `ASK/fci/src/fci_msg.c:72-91` with a `nla_policy fci_genl_policy[FCI_ATTR_MAX + 1]` snippet.

**Status:** **REFRAMED.** The file `ASK/fci/src/fci_msg.c` does **not exist** in the current tree (`ls ASK/fci/` shows only `fci.c`, `fci.h`, `lib/`, build artefacts). There is no generic-netlink (`genl`) handler, no `nla_policy`, and no `FCI_ATTR_*` enum anywhere in `ASK/fci/`. The actual handler is plain (raw protocol) netlink implemented in `ASK/fci/fci.c:455` (`__fci_fe_inbound_data`) and parsed at `ASK/fci/fci.c:533` (`fci_fe_inbound_parser`). The audit's quoted `nla_policy` snippet is fabricated (or copied from an unrelated module).

The **underlying defect** (no payload-length validation) is **still open**, now re-cited as **F-03** (`fci.c:457, 466` — no `nlmsg_ok()`) and **F-01** (`fci.c:480, 533–548` — `fci_msg->length` from user is not bounded against actual skb payload size).

### Prior CRIT #2 — "FCI netlink — unchecked cast of payload to command structs" (`ASK-CODE-QUALITY.md §2`)

**Prior cite:** `ASK/fci/src/fci_msg.c:166-200`. **Status:** **REFRAMED** — same fabricated path. The actual unchecked dispatch is at `fci.c:540`:

```c
rc = comcerto_fpp_send_command(fci_msg->fcode, fci_msg->length,
                               fci_msg->payload,
                               &fci_rep->length, fci_rep->payload);
```

`fci_msg->length` comes verbatim from userspace; never validated against `nlmsg_len(nlh) - NLMSG_HDRLEN - FCI_MSG_HDR_SIZE`. **Still open** as F-01.

### Prior HIGH C21 — "Netlink message size not validated in `fci.c`" (`ASK-CODE-REVIEW.md §4`)

**Status:** **STILL OPEN.** Same root cause as F-03. Re-cited at `fci.c:457` (raw cast `(struct nlmsghdr *)skb->data`) and `fci.c:466` (`nlmsg_data(nlh)`).

### Prior HIGH C22 — "libfci.c: No timeout on netlink recv"

**Status:** Out of scope of this kernel-side report (lives in `ASK/fci/lib/src/libfci.c`).

### Prior MED M15 — "Uses deprecated NETLINK_FF (protocol 30)"

**Status:** **STILL OPEN, P2.** Confirmed at `fci.c:123, 125, 128, 299`. Migration to genl is a long-tail rewrite — not this cycle.

### Prior MED M16 — "libfci.c: global fci_fd not thread-safe"

**Status:** Out of scope (userspace lib).

---

## P0 findings

### F-01 — User-controlled `length` not bounded against netlink skb

**File:** `ASK/fci/fci.c:480` (call site), `ASK/fci/fci.c:533–548` (parser).

```c
466:    fci_msg = nlmsg_data(nlh);
...
480:    rc = fci_fe_inbound_parser(fci_msg, fci_rep);
...
533: static int fci_fe_inbound_parser(FCI_MSG *fci_msg, FCI_MSG *fci_rep)
540:     rc = comcerto_fpp_send_command(fci_msg->fcode, fci_msg->length,
541:                                    fci_msg->payload, &fci_rep->length, fci_rep->payload);
```

`fci_msg->length` is a `u16` read directly from the userspace-supplied netlink payload. It is forwarded as-is into the FPP command dispatch. If `length > nlmsg_len(nlh) - FCI_MSG_HDR_SIZE`, the FPP layer reads past the end of the netlink buffer (kernel infoleak / oops if past the page).

**Fix:** add at start of `__fci_fe_inbound_data()`:

```c
if (!nlmsg_ok(nlh, skb->len) ||
    nlmsg_len(nlh) < FCI_MSG_HDR_SIZE)
    return;
fci_msg = nlmsg_data(nlh);
if (fci_msg->length > nlmsg_len(nlh) - FCI_MSG_HDR_SIZE ||
    fci_msg->length > FCI_MSG_MAX_PAYLOAD)
    return;
```

**Routing:** kernel-side patch in `ASK/fci/fci.c`.

### F-02 — Missing capability check on netlink ingress

**File:** `ASK/fci/fci.c:455–510` (`__fci_fe_inbound_data`).

There is no `netlink_capable(skb, CAP_NET_ADMIN)` (or `CAP_SYS_ADMIN`) check on inbound messages. An unprivileged user can `socket(AF_NETLINK, SOCK_RAW, NETLINK_FF)` and send commands directly to the FPP forward engine. Without the check, **F-01** becomes an unprivileged-LPE rather than a root→kernel hardening issue.

**Fix:** insert `if (!netlink_capable(skb, CAP_NET_ADMIN)) return;` at top of `__fci_fe_inbound_data`.

### F-03 — `skb->data` cast to `struct nlmsghdr *` without `nlmsg_ok()`

**File:** `ASK/fci/fci.c:457`.

```c
457:    struct nlmsghdr *nlh = (struct nlmsghdr *)skb->data;
466:    fci_msg = nlmsg_data(nlh);
```

For a malformed short skb (`skb->len < NLMSG_HDRLEN`), `nlh->nlmsg_len`, `nlh->nlmsg_type`, etc. are read from past the buffer. `nlmsg_data(nlh)` then returns a pointer past the header, and `fci_msg->fcode`/`->length` reads land in arbitrary kernel memory.

The kernel idiom is `netlink_rcv_skb(skb, handler)` — which `auto_bridge.c:568` uses correctly. FCI does not.

**Fix:** restructure inbound to use `netlink_rcv_skb(skb, fci_inbound_one_msg)` or guard with `nlmsg_ok(nlh, skb->len)`.

---

## P1 findings

### F-04 — `nlmsg_put()` return value ignored

**File:** `ASK/fci/fci.c:475`.

```c
475:    rep = nlmsg_put(nskb, NETLINK_CB(skb).portid, nlh->nlmsg_seq, 0, 0, 0);
476:
477:    fci_rep = nlmsg_data(rep);
```

`nlmsg_put()` returns NULL on tail-room exhaustion. `nlmsg_data(NULL)` returns `((char *)NULL) + NLMSG_HDRLEN` = `0x10` — a NULL-page deref oops on the next write to `fci_rep->length`.

**Fix:** check `if (!rep) { kfree_skb(nskb); return; }`.

### F-05 — `proc_create` failure not handled

**File:** `ASK/fci/fci.c:655` (init) / `fci.c:673` (exit).

```c
655:    proc_create("fci", 0, NULL, &fci_proc_fops);
...
673:    remove_proc_entry("fci", NULL);
```

If `proc_create()` returns NULL the module continues but the entry is missing. `remove_proc_entry()` on a non-existent entry is a `WARN_ON` in modern kernels.

**Fix:** capture return value, fail init if NULL, only call `remove_proc_entry` in exit if registration succeeded.

### F-06 — skb leak / NULL deref on `nlmsg_put` failure in `fci_outbound_fe_data`

**File:** `ASK/fci/fci.c:432`.

```c
432:    nlh = nlmsg_put(skb, 0, 0, 0, len + FCI_MSG_HDR_SIZE, 0);
434:    fci_msg = nlmsg_data(nlh);
```

Same NULL-check omission as F-04, with the additional fact that the skb is already allocated by `fci_alloc_msg()` and would leak (in practice the NULL-deref oopses first).

**Fix:** `if (!nlh) { kfree_skb(skb); rc = -EMSGSIZE; goto err; }`.

### F-07 — `in_interrupt()` GFP gating idiom is brittle

**File:** `ASK/fci/fci.c:189, 391`. `in_interrupt()` covers softirq+hardirq but not e.g. tasklet workqueue migrations. If the FE callback path ever moves to a kthread, the gating becomes wrong. Switch to `gfp_t` plumbed from caller, or `preemptible() ? GFP_KERNEL : GFP_ATOMIC`.

---

## P2 findings

### F-08 — `printk(KERN_ERR …)` not modernized

Mass-rewrite candidates: `fci.c:70, 207, 326, 333, 367, 396, 575, 611–629, 648, 670`. Add `#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt` at top of file and switch to `pr_err`/`pr_warn`.

### F-09 — `this_fci` not `__read_mostly`

**File:** `ASK/fci/fci.c:54`. Pointer set once in `fci_init()`, never re-pointed. Mark `__read_mostly` to keep its cache line clean under SMP.

### F-10 — `fci_type_to_nl_type` switch is a 1-entry table

**File:** `ASK/fci/fci.c:292–308`. Replace with `static const int fci_to_nl[FCI_MAX_PROTO] = { [FCI_NL_FF] = NETLINK_FF };`.

### F-11 — Macro hygiene / source whitespace damage

**File:** `ASK/fci/fci.h:44–53` and **whole-file** `ASK/fci/fci.c` (every line is flush-left, no tab indentation, suggesting de-tabbed paste).

```c
#define FCI_NL_FF0
#define FCI_MAX_PROTO1
#define NL_FF_GROUP1
#define FCI_MSG_MAX_PAYLOAD512
#define FCI_MSG_HDR_SIZE 4 /* fcode + length */
#define FCI_MSG_SIZE(FCI_MSG_MAX_PAYLOAD + FCI_MSG_HDR_SIZE)
```

`FCI_MSG_SIZE(...)` is parameterless-looking but parses as a value because the parens are part of the replacement. Class **D16** macro hazard: it *looks* function-like and grep-matches as such.

**Fix:** restore canonical formatting (whitespace + tabs).

### F-12 — Uninitialized `fci_rep->payload` tail

**File:** `ASK/fci/fci.c:472–490`. Skb tail bytes are not zeroed; `comcerto_fpp_send_command()` writes `fci_rep->length` and *some* bytes into payload; `skb_put()` then exposes exactly `length` bytes. Padding holes / FE-internal struct gaps may carry uninit kernel memory to userspace. Zero `fci_rep` before the FE call.

### F-13 — Implicit on-wire ABI

**File:** `ASK/fci/fci.h:59–65`. Add `_Static_assert(sizeof(FCI_MSG) == 516, "FCI_MSG ABI changed");` to lock the struct layout (16 + 4 + 512 = 516 bytes nominal; padding could shift).

---

## Optimization candidates

| # | Class | File:Line | Notes |
|---|---|---|---|
| O-fci-1 | O7 | `fci.c:54` | `static FCI *this_fci __read_mostly;` (also F-09). |
| O-fci-2 | O1 | `fci.c:292–308` | Replace switch with const lookup table (F-10). |
| O-fci-3 | O2 | `fci.c:388–407` | Per-CPU skb pool for inbound `fci_alloc_msg()` (low priority — control plane). |

---

## Recommendations / fix routing

| Finding | Routing | Patch target |
|---|---|---|
| F-01, F-02, F-03 | **Kernel-side** (`ASK/fci/fci.c`) | Single hardening patch covering capability check, `nlmsg_ok` validation, and `length` bound check — block before any FE dispatch. |
| F-04, F-06 | Kernel-side | Add `nlmsg_put` NULL checks + skb cleanup. |
| F-05 | Kernel-side | Capture `proc_create` return; condition `remove_proc_entry`. |
| F-12 | Kernel-side | `memset(fci_rep, 0, ...)` before FE call. |
| F-07 | Defer | Audit FE callback context first. |
| F-08, F-09, F-10, F-11, F-13 | Style/hardening cleanup | Single janitorial patch. |

**Suggested merge order:**
1. F-01 + F-02 + F-03 (security hardening, single patch).
2. F-04 + F-06 (NULL deref prevention).
3. F-12 (infoleak).
4. F-05 (init/exit symmetry).
5. F-08..F-13 janitorial.
