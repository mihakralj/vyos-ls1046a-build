# ASK2 Suricata integration plan

**2026-10-16 · dpaa1 · Status: design only.**

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

In a routed inline deployment, the packet stream is still visible to the kernel
stack and the service. The first useful state is the inspection decision.

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

## 9. Recommended implementation sequence

1. Configure a valid Suricata service using the native VyOS syntax.
2. Validate `update suricata` and `restart suricata` on a normal routed path.
3. Add a minimal safe-flow handoff from Suricata-approved flows into the ASK2
   fast path.
4. Keep all interface and rule configuration in the native `service suricata`
   tree.
5. Measure mixed elephant/mouse traffic with both the inspection and hardware
   paths enabled.
6. Expand only after the handoff is stable under real ruleset traffic.

## 10. Bottom line

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
