#!/usr/bin/env bash
# ghidra-mcp-server.sh — bring up the Ghidra HTTP server (port 8080) that the
# GhidraMCP bridge (and thus the Kilo `ghidra` MCP tools) talks to.
#
# Ghidra 11.x has NO on-disk default-tool file to pre-seed the plugin, and the
# GhidraMCP HTTP server lives inside a per-tool GUI plugin, so a running
# CodeBrowser is required. On this headless box we run the GUI under Xvfb.
#
# ONE-TIME per project: after the GUI is up, open a program in the CodeBrowser
# (File > Open, or double-click the imported program), then
# File > Configure > Miscellaneous > check "GhidraMCPPlugin" > OK.
# The plugin starts the HTTP server on 127.0.0.1:8080 and the setting persists.
# After that, `curl -s http://127.0.0.1:8080/methods` lists the program's
# functions, and the Kilo `ghidra` MCP tools work (restart Kilo once to load
# the MCP server from .kilo/kilo.json).
#
# Usage: decomp/tools/ghidra-mcp-server.sh [project.gpr]
set -euo pipefail
export JAVA_HOME=/opt/jdk-21.0.12+8
export GHIDRA_HOME=/opt/ghidra_11.3.2_PUBLIC
export PATH="$JAVA_HOME/bin:$PATH"
PROJ="${1:-/tmp/kilo/ghidra-proj/decomp.gpr}"
DISP="${GHIDRA_DISPLAY:-:99}"

if ! (command -v xdpyinfo >/dev/null 2>&1 && xdpyinfo -display "$DISP" >/dev/null 2>&1); then
  echo "starting Xvfb on $DISP"
  Xvfb "$DISP" -screen 0 1600x1000x24 >/tmp/ghidra-xvfb.log 2>&1 &
  sleep 2
fi
export DISPLAY="$DISP"
echo "DISPLAY=$DISPLAY ; launching Ghidra GUI on project $PROJ"
echo "(then enable GhidraMCPPlugin on an open program — see header)"
exec "$GHIDRA_HOME/ghidraRun" "$PROJ"
