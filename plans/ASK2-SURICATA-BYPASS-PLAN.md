# ASK2 Suricata integration plan

**2026-10-16 · dpaa1 · Status: design only.** This plan is based on the current
VyOS Suricata service model described in the upstream documentation at
https://docs.vyos.io/en/rolling/configuration/service/suricata.html. The key
point is that the router does not invent a custom Suricata control plane; it
configures the built-in service under `service suricata`, then runs
`update suricata` and `restart suricata` to load the ruleset.

The ASK2 question is not "how do we replace Suricata". It is: "how do we keep
Suricata's inspection path working while the ASK2 hardware flow path handles the
large bulk of traffic the IDS does not need to see again after the initial
inspection prefix?"

## 1. Baseline: the actual VyOS Suricata service

The current VyOS Suricata config model is intentionally simple:

- monitored interfaces: `set service suricata interface <interface>`
- address groups: `set service suricata address-group <name> address <address>`
- port groups: `set service suricata port-group <name> port <port>`
- EVE log: `set service suricata log eve filename ...`, `filetype`, and `type`
- rules update: `update suricata`
- restart: `restart suricata`

The underlying docs also require at least one interface, one address group, and
one port group before a commit succeeds. That matters because the hardware-
offload design should attach to an already valid Suricata service, not replace it
with a separate ad hoc definition.

Example config from the upstream service model:

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

## 2. What Suricata is doing in practice

Suricata is a packet-inspection engine. It accepts a live network interface, runs
rules against traffic, emits JSON EVE logs, and loads fresh rules with
`update suricata`. In a routed inline deployment, the packet stream is still
visible to the kernel stack and the service, but the first useful state is the
inspection decision.

This is a very good fit for the ASK2 flow model. ASK2 already operates from a
slow-path learning pattern: a new flow enters the slow path, the system decides
whether it remains software-visible or becomes a hardware-offloaded fast path.
The design goal is to let Suricata decide that decision without inventing a
separate packet-processing stack.

## 3. ASK2-compatible design: Suricata as the decision point, ASK2 as the fast path

The right integration point is not a custom Suricata YAML/daemon API. It is the
existing ASK2 flowtable/offload contract:

- Suricata monitors traffic on the configured interface(s)
- the flow reaches the inspection gate
- once the flow has passed the initial inspection prefix, the router installs a
  hardware flow record
- the bulk of subsequent packets travel through the FE-VM ehash path instead of
  the normal CPU inspection path
- the Suricata service keeps its rule-based visibility for the flow until the
  policy says it can be handed off

This is the same pattern the current ASK2 design already uses for fast flow
insertion: a flow starts as a slow-path event; once the system decides it is
safe for the silicon fast path, the record is published and the subsequent
packets stop taking the inspection path.

This makes the integration compatible with the shipped VyOS service model.
Suricata remains the thing that decides what is interesting; ASK2 handles the
repeat traffic once the decision is made.

## 4. Why this is a good match for the LS1046A

The advantage is not that Suricata "becomes hardware offloaded by itself". The
advantage is that the high-rate bulk of the traffic can leave the inspection path
once the flow has already been classified, while Suricata continues to run in the
normal way for the ruleset and logging.

That is exactly the class of problem ASK2 is good at:

- new-flow cost: software logic and inspection
- established-flow cost: hardware path / direct FQ dispatch
- CPU bound by flow churn, not by ongoing byte-forwarding bandwidth

This fits elephant-heavy traffic mixes better than trying to force all packets
through Suricata's userspace pipeline. The result is a real inline IDS/IPS
pattern, not a fake "all traffic is Suricata traffic" model.

## 5. Deployment model

### Inline routed mode

This is the target model for the first design pass.

- Suricata is configured as the router's IDS/IPS service on the relevant
  interfaces using the normal VyOS syntax above.
- The router continues to do normal routed forwarding.
- The ASK2 flow path handles the bulk of traffic after the decision point.
- The service remains the authoritative inspection and logging layer for the
  flows that are still in software.

This is the cleanest way to match the actual VyOS Suricata model.

### Passive or SPAN mode

Passive tap/SPAN deployments are not the first target. They are useful for
network visibility, but they do not align as well with a hardware fast path that
must own forwarding for the actual throughput path. The inline routed mode is the
real target because it exercises the same packet path the router already forwards
on.

## 6. Suricata and ASK2 interaction points

The integration points should be intentionally small:

1. Suricata configuration
   - `service suricata interface ...`
   - `address-group` and `port-group`
   - `log eve ...`
   - `update suricata`
   - `restart suricata`

2. Flow decision and handoff
   - Suricata identifies the flow it wants to treat as bulk/known-safe
   - the router records the flow in the ASK2 flow table for the normal fast path
   - the flow switches from software inspection to hardware forwarding

3. Safe teardown
   - flow timeout / close / NAT teardown removes the record and returns the flow
     to the normal software path when required

4. Observability
   - Suricata still emits EVE logs for whatever it chooses to inspect
   - ASK2 provides the hardware-fastpath state and flow counters for the
     offloaded subset

This keeps one source of truth for the service config and avoids inventing a
second special-purpose control plane.

## 7. What must be handled carefully

The following are the real engineering gates, not purely design concerns:

- Flow lifecycle: established flows must be removed cleanly on timeout or close
- NAT/VLAN/PPPoE handling: the flow record is only useful if the hardware path
  can safely carry the same tuple transformations the router already supports
- MTU and fragmentation: the hardware offload path must stay inside the valid
  packet envelope
- Rule policy: the router must not promise that all traffic is bypassed; only the
  flows that are verified safe may leave the inspection path
- Logging and packet loss: Suricata must continue to emit useful EVE output for
  the traffic that remains in software or for the early prefix before handoff

## 8. Recommended first implementation sequence

1. Establish a valid Suricata service on the router using the upstream VyOS
   syntax and command model.
2. Validate normal `update suricata` and `restart suricata` behavior.
3. Build a minimal flow-offload handoff based on known safe flows only.
4. Keep the interface and rule configuration under the native `service suricata`
   tree; avoid custom CLI or a separate rule parser.
5. Measure a mixed elephant/mouse workload with Suricata running and the ASK2
   path enabled.
6. Expand only after the software/hardware handoff is stable under real ruleset
   traffic.

## 9. Bottom line

The native VyOS Suricata configuration is already the right service abstraction:
monitored interfaces, groups, EVE logs, and rule updates. The ASK2 contribution
is not to replace this service, but to let the system use the hardware fast path
for flows that have already been approved by the inspection process.

That keeps the design compatible with the documented VyOS model, aligns with the
shipping ASK2 flow-offload architecture, and gives a realistic path toward a
usable IDS/IPS deployment on DPAA1 hardware without rewriting the service layer.
