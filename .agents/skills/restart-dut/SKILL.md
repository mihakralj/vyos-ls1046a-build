---
name: restart-dut
description: Hard power-cycle the VyOS DUT (Mono Gateway LS1046A board) via the smart-plug HTTP API, then watch the serial console until the board boots and reaches an active login/prompt. Triggers on "restart dut", "power cycle dut", "reboot the board (hard)", "power cycle the board", "cold boot dut", or any request to physically power-cycle the device under test.
---

# restart-dut

Performs a **hard power-cycle** of the VyOS DUT by toggling its smart plug
(Hubitat Maker API) OFF → wait → ON, then **monitors the serial console** over
the TCP-to-serial relay until the board finishes booting and a login/prompt is
reached.

Use this when the board is hung, unresponsive over SSH and serial, or needs a
clean cold boot. This is a **power-rail cut**, not a software `reboot` — it is
the equivalent of pulling and re-inserting the plug.

## Targets

| Property | Value |
|---|---|
| **Smart-plug host** | `192.168.1.187` (Hubitat Maker API) |
| **App ID** | `15` |
| **Device ID** | `10` (DUT power outlet) |
| **Access token** | `77840586-be2b-4665-b184-3d82304b6804` |
| **OFF URL** | `http://192.168.1.187/apps/api/15/devices/10/off?access_token=77840586-be2b-4665-b184-3d82304b6804` |
| **ON URL** | `http://192.168.1.187/apps/api/15/devices/10/on?access_token=77840586-be2b-4665-b184-3d82304b6804` |
| **Serial relay** | `192.168.1.16:5555` (raw TCP, 115200 8N1) — via TcpSocketMCP |
| **DUT op-mode prompt** | `vyos@vyos:~$ ` |
| **DUT login prompt** | `vyos login:` |

## Prerequisites

- Network reachability to the Hubitat hub at `192.168.1.187`.
- The **TcpSocketMCP** MCP server loaded (`tcp_connect` / `tcp_send` /
  `tcp_read_buffer` / `tcp_clear_buffer` / `tcp_disconnect` tools). If those
  tools are unavailable, the MCP server has not loaded — restart the Kilo
  session. See the companion **dut-console** skill for full serial protocol
  details.

## Procedure

### Step 1 — Power OFF

Send the OFF request with `curl`:

```bash
curl -fsS --max-time 10 "http://192.168.1.187/apps/api/15/devices/10/off?access_token=77840586-be2b-4665-b184-3d82304b6804"
```

A Hubitat success response is typically `{}` or a small JSON body with HTTP
200. A non-200 or curl error means the toggle did **not** happen — stop and
report; do not proceed to ON (leaving the board cut).

### Step 2 — Wait 3 seconds

```bash
sleep 3
```

This drains residual power so the cold boot is clean. Do not shorten this.

### Step 3 — Power ON

```bash
curl -fsS --max-time 10 "http://192.168.1.187/apps/api/15/devices/10/on?access_token=77840586-be2b-4665-b184-3d82304b6804"
```

Confirm HTTP 200. The board's power rail is now live and U-Boot will start.

### Step 4 — Monitor serial until the board is active

Connect to the serial relay and watch the boot log until a login or shell
prompt appears.

1. **Connect** (do this right after Step 3 so no boot output is missed):
   ```
   tcp_connect(host="192.168.1.16", port=5555)
   ```
   Save the returned `connection_id`.

2. **Clear** any stale buffer:
   ```
   tcp_clear_buffer(connection_id)
   ```

3. **Poll the boot log.** Read the buffer repeatedly, sleeping between reads.
   The LS1046A takes roughly **60–130 s** from power-on to login prompt
   (U-Boot → TFTP/eMMC kernel → live-config → boot commit). Poll in a loop:
   ```
   bash: sleep 10
   tcp_read_buffer(connection_id)
   ```
   Repeat (up to ~15 iterations / ~150 s). Expected progression in the buffer:
   - U-Boot banner / `Starting kernel ...`
   - kernel boot messages, `[ OK ] Started …` systemd lines
   - `Waiting for VyOS boot configuration to complete...` → `done.`
   - finally `vyos login:` or, if auto-login, `vyos@vyos:~$ `

4. **Confirm active.** Once `vyos login:` or `vyos@vyos:~$ ` is seen, the board
   is up. Optionally validate the prompt by sending an empty CR:
   ```
   tcp_clear_buffer(connection_id)
   tcp_send(connection_id, data="", terminator="0D")
   bash: sleep 2
   tcp_read_buffer(connection_id)
   ```
   - `vyos@vyos:~$ ` → already logged in, **active**.
   - `vyos login:` → at login prompt, **active** (board reached userspace).
     Log in only if the caller needs to run commands:
     ```
     tcp_send(connection_id, data="vyos", terminator="0D")
     bash: sleep 1
     tcp_send(connection_id, data="vyos", terminator="0D")   # password
     bash: sleep 2
     tcp_read_buffer(connection_id)
     ```

5. **Disconnect** when done (the relay is shared):
   ```
   tcp_disconnect(connection_id)
   ```

## One-shot helper (power toggle only)

The two HTTP calls plus the wait can be issued in a single bash invocation;
the serial monitoring still uses the MCP tools afterward:

```bash
TOKEN="77840586-be2b-4665-b184-3d82304b6804"
BASE="http://192.168.1.187/apps/api/15/devices/10"
curl -fsS --max-time 10 "$BASE/off?access_token=$TOKEN" && echo "OFF ok"
sleep 3
curl -fsS --max-time 10 "$BASE/on?access_token=$TOKEN" && echo "ON ok"
```

## Reporting back

After the cycle, report to the user:
- Result of the OFF call (HTTP status).
- Result of the ON call (HTTP status).
- Time to reach login/prompt on serial, and the final prompt line seen
  (`vyos login:` or `vyos@vyos:~$ `), confirming the board is active.

## Error Handling

| Symptom | Cause | Action |
|---|---|---|
| `curl: (28)` timeout on OFF/ON | Hubitat hub unreachable | Verify `192.168.1.187` reachable; do not leave board OFF |
| OFF ok, ON fails | Hub flaked mid-cycle | Retry the ON call immediately — board must not stay cut |
| Non-200 JSON error | Wrong app/device id or token | Verify the URL components above |
| Serial buffer empty after 150 s | Board not booting / no power | Re-check ON succeeded; inspect U-Boot output; escalate to physical check |
| Boot log stops mid-kernel | Hang / bad image | Capture the last buffer for diagnosis; a second power-cycle may be warranted |
| `tcp_connect` refused | Relay down | Check serial relay at `192.168.1.16:5555` |

## Guardrails

- **Never leave the board powered OFF.** If the ON call fails after a
  successful OFF, retry ON until it succeeds or escalate immediately.
- **This is a hard cut** — prefer a software `reboot` (see the **dut-console**
  skill) when the board is still responsive. Use `restart-dut` only for hangs
  or when a true cold boot is required.
- **Confirm with the user before power-cycling** unless they explicitly asked
  to restart/power-cycle the DUT.
- **Always disconnect** the serial relay when finished — it is shared.
- The access token is an operator credential — keep it within this skill; do
  not echo it into logs the user did not ask for.
