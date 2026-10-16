# ASK2 Suricata integration plan

**Updated 2026-10-16 · dpaa1 · Status: design only. No code exists: there is no Suricata, NFQUEUE or conntrack-mark reference in `ask.ko` or in the patch stack.**

This document is intended to stand on its own. It explains the current scope,
what is already known, and what remains to be implemented without assuming any
prior discussion or archived working note.

## 1. Executive summary

The ASK2 question is not: "how do we replace Suricata?"

The real question is: "how do we keep Suricata as the inspection authority while
ASK2 handles the repeated bulk traffic that has already been accepted by the
inspection path?"

This is the correct architecture for the LS1046A flow-offload model. Suricata is
still the rule engine, logging layer and policy authority. ASK2 is the
hardware fast-path used after the first inspection decision has been made.

This leaves the router aligned with the normal VyOS service model instead of
inventing a second custom Suricata control plane.

## 2. Current fact base: the real VyOS Suricata service model

VyOS exposes Suricata as a normal service under the `service suricata` tree.
The command model is intentionally simple and documented in the upstream VyOS
service docs.

Required building blocks:

- monitored interfaces: `set service suricata interface <interface>`
- address groups: `set service suricata address-group <name> address <address>`
- port groups: `set service suricata port-group <name> port <port>`
- EVE log settings: `set service suricata log eve ...`
- rules update: `update suricata`
- service reload/restart: `restart suricata`

At least one interface, one address group, and one port group are required for a
valid service configuration.

Example:

```bash
set service suricata interface eth1
set service suricata address-group home-net address 192.0.2.0/24
set service suricata address-group home-net address 2001:db8::/32
set service suricata port-group http-ports port 80
set service suricata port-group http-ports port 443
set service suricata log eve filename eve.json
set service suricata log eve filetype regular
set service suricata log eve type alert
set service suricata log eve type flow
set service suricata log eve type http
commit
save
update suricata
restart suricata
```

## 3. What Suricata does in practice

Suricata is a packet-inspection engine. It accepts live traffic on a router
interface, evaluates a rule set, emits EVE logs, and reloads rules with
`update suricata`.

### 3.1 The native VyOS service is IDS-only (verified 2026-10-16)

Upstream vyos-1x (`src/conf_mode/service_suricata.py`,
`data/templates/ids/suricata.j2`) renders `af-packet:` and `pcap:` capture
blocks, one per `service suricata interface`. It sets `cluster_flow` and
`defrag: yes`, but no `copy-mode: ips`/`tap`, no `nfq:` section and no
nftables `queue` rule. Suricata therefore receives a copy of each packet and
cannot hold or drop anything: the native service is a passive IDS, not an
inline approval gate. Its `drop`/`reject` rule actions have no forwarding
effect in this mode.

Consequences for ASK2:

- When the native service runs, the kernel forwards (and, under the factory
  default, hardware-learns) a flow regardless of what Suricata concludes.
- With ASK2 offload engaged, Suricata's AF_PACKET socket stops seeing a flow
  once that flow is in hardware. It sees only the slow-path prefix: the first
  packets plus the `hardware-offload-delay` window.
- Being in conntrack state `established`, having raised no alert, or having
  outlived `hardware-offload-delay` are **not** approvals. None of them means
  Suricata has inspected and accepted the flow.

### 3.2 Interaction with the ISO factory default

Since commit `c84499e5` (2026-10-16) the factory `config.boot.default` enables
`offload ipv4 ipv6` on eth1–eth4 and a hardware flowtable with
`hardware-offload-delay 500`. A fresh install that adds `service suricata`
therefore runs IDS on traffic that ASK2 offloads about 1 s after flow start.
Until an approval gate exists, the documented guidance is:

- For full-flow visibility, delete `offload` from the monitored interfaces, or
  remove the flowtable rule for the monitored traffic.
- Otherwise, accept that Suricata sees only the start of each offloaded flow.
  This is enough for protocol/TLS/HTTP metadata and most signature matches,
  but not for full-stream inspection.

`CLI-AND-TOOLS.md` should carry the same note when Suricata guidance is added
there.

That decision point is exactly where ASK2 can fit. ASK2 already behaves as a
slow-path learning engine: a new flow enters the slow path, a decision is made,
and the flow may be installed into the fast hardware path.

## 4. ASK2-compatible design

The right design is not a custom Suricata API or a separate packet-processing
stack. The correct design is:

1. Suricata runs on the configured interface using the native VyOS service model.
2. The first packet(s) of a flow go through the Suricata inspection gate.
3. If the flow is accepted as bulk or safe for the hardware path, ASK2 installs a
   fast-path flow record.
4. Subsequent packets use the FE-VM ehash path instead of staying on the CPU
   inspection path.
5. Suricata keeps rule-based visibility and logging for the flows that remain in
   the software path or for the early prefix before handoff.

This keeps one source of truth for the service configuration and avoids duplicate
state machines.

Step 2 requires an inline (NFQUEUE/IPS) capture mode. The native service is
IDS-only (§3.1), so this design is a target rather than a description of the
current system. The approval signal in step 3 is defined in §9.

## 5. Why this is a good fit for the LS1046A hardware model

The design is not: "make Suricata itself hardware offloaded".

The design is: "separate the expensive repeated traffic from the inspection path
after a decision has been made".

This matches the LS1046A asymmetry well:

- first-flow cost: software inspection and classification
- established-flow cost: hardware fast path
- workload shape: elephant-heavy traffic with repeat flows and a manageable number
  of decision events

This is the correct use of a hardware flow engine for IDS/IPS work. It is not a
promise that every packet is always visible to Suricata.

## 6. Deployment model

### Hybrid inspection-driven local action model (primary target)

The most important distinction is that the practical ASK2/Suricata design is not
best described as pure inline or pure passive. It is a hybrid model:

- inspection can happen on the normal routed path, or through a mirrored/monitored
  path such as SPAN, TAP or a local bridge-assisted observation path
- the inspection result is fed back into the router's local control plane
- the router then changes local forwarding/offload state for the relevant flow or
  flow class via ASK2
- the result is a local action on the Mono GW rather than passive logging alone

This is the key to making it IPS-shaped rather than merely IDS-like.

The router still does normal local policy decisions, but traffic that is observed
as suspicious, interesting, or approved for bulk handling can cause a local
flow-state change on the gateway. In other words, inspection is not merely a
monitor; it becomes the trigger for local enforcement or fast-path installation.

### Inline routed mode

Inline routed mode is one end of the spectrum. It is the cleanest case when the
router is directly in the forwarding path and can make a local decision on the
traffic it sees.

### Passive / SPAN / tap mode

Passive monitoring is the other extreme. This is still useful for visibility,
forensics, and rule tuning, but by itself it does not change local forwarding
state. It becomes part of the hybrid model only when the observed inspection
result is translated into a local policy action on the router.

The real first target is therefore the hybrid model: inspection data from either
local inline traffic or mirrored traffic influences a local ASK2 flow decision on
this gateway, without requiring a fully custom or fully passive-only deployment.

## 7. Interaction points and boundaries

The ASK2/Suricata boundary should be intentionally small, but the important
point is that the boundary is not just a passive log feed. It is a control-plane
interaction:

- Suricata config remains native VyOS config under `service suricata`
- monitored or mirrored traffic feeds the inspection decision
- the inspection result is translated into a local gateway action
- flow handoff is controlled by the ASK2 flow install path
- flow teardown happens on timeout, close, or policy invalidation
- EVE logging remains the same Suricata logging model
- ASK2 only acts on the flow subset the policy and inspection path explicitly
  approves for hardware handling

This is the hybrid IPS-shaped behavior: local observation turns into local action,
not passive-only monitoring.

## 8. Engineering constraints that matter

The following are real constraints for a safe implementation:

- flow lifecycle must be clean and reversible
- NAT/VLAN/PPPoE must be preserved correctly in the fast path
- MTU and fragmentation limits must be respected
- the fast path must never unconditionally bypass all traffic
- logging and packet loss must remain observable and understandable
- the design must remain fail-safe when the offload path is not available

The design is deliberately narrow: it is a controlled handoff after inspection,
not a blanket replacement of the Suricata service model.

## 9. Approval contract (required before any code)

An ASK2 handoff gated by Suricata needs a written contract covering every
item below. None of it exists today.

1. **Explicit authorization signal.** Hardware install is allowed only after a
   positive per-flow verdict, never because something is absent (no alert,
   timer expired). Candidate mechanisms:
   - An inline NFQUEUE deployment where Suricata's verdict sets a conntrack
     mark, and the nftables flowtable rule matches that mark
     (`ct mark & APPROVED`) before `flow add @FT`. This reuses the existing
     ASK2 flowtable learning path and needs no `ask.ko` change.
   - Suricata's own `bypass` keyword / NFQ `bypass-mark`, which is designed
     to say "stop inspecting this flow". It is the natural integration point:
     map the bypass mark to the approval mark.
   - An EVE-driven local agent is a fallback only. It adds a second control
     plane and races the data path.
2. **Tuple and NAT identity.** The approval must name the same flow that ASK2
   installs: original and reply tuples after DNAT/SNAT, plus the ingress
   port. Conntrack marks get this for free, which is a reason to prefer them.
3. **Revocation.** Any of the following must remove the hardware record and
   return the flow to the slow path (`nf_flow_offload` teardown, then ASK2
   delete): a rule reload (`update suricata` / `restart suricata`), a later
   alert on the flow, or an operator flush. Define whether a reload revokes
   every approved flow or only those whose rules changed. The first is
   simpler and is the default.
4. **Fail-closed.** If Suricata stops or crashes, or its queue overflows,
   unapproved flows must not be hardware-learned. Approved flows may continue
   until a revocation event. Spell out the NFQUEUE `fail-open` setting
   explicitly: fail-open keeps traffic forwarding in software, but must not
   imply approval.
5. **Default-config interaction.** Decide whether configuring
   `service suricata` on an interface with `offload` engaged:
   - is a commit error,
   - automatically adds the approval-mark match to the flowtable rule, or
   - only warns.
   It must not silently keep learning unapproved flows if the operator asked
   for IPS.
6. **Observability.** Counters for approved, revoked and hardware-installed
   flows, so that `ask-check` or an equivalent can show that only approved
   flows reach hardware.

## 10. Recommended implementation sequence

1. Configure a valid Suricata service using the native VyOS syntax and
   validate `update suricata` / `restart suricata` on the routed path, with
   `offload` deleted on the monitored ports. This gives the IDS baseline.
2. Repeat with `offload` engaged and quantify what IDS still sees: alerts on
   a known-bad pattern sent early versus late in a long flow.
3. Prototype the inline path outside the VyOS CLI: NFQUEUE in `copy-mode ips`,
   Suricata `bypass` → conntrack mark → flowtable rule matching the mark.
   Prove that no unmarked flow reaches hardware (`fe_ehash_stats`,
   `ask-check`).
4. Prove revocation (rule reload, alert, Suricata stop) and fail-closed
   behaviour on the board.
5. Only then design the CLI and vyos-1x integration, keeping interface and
   rule configuration in the native `service suricata` tree.
6. Measure mixed elephant/mouse traffic with both the inspection and hardware
   paths enabled.

## 11. Bottom line

This is a practical IDS/IPS-shaped design for the LS1046A, but the important
nuance is that it is a hybrid model rather than a pure inline or pure passive
deployment.

Keep Suricata as the inspection and logging authority, and let it influence the
local gateway's forwarding/offload state through the router's control plane. ASK2
then handles the established traffic that has already passed the relevant policy
and inspection gate.

That keeps the implementation compatible with the documented VyOS service model,
uses the existing ASK2 flow-offload architecture, and preserves the real
performance advantage: local inspection results can drive local fast-path action
without inventing a bespoke second control plane.
