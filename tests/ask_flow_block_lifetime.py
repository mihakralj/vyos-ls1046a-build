"""Host regressions for ASK flowtable callback registration and release."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"


@pytest.fixture(scope="module")
def block_binary(tmp_path_factory):
    source = SOURCE.read_text()
    private = source[source.index("struct ask_flow_block_priv {"):
                     source.index("/* Match parsing")]
    private = private[:private.rfind("/* ----------------------------------------------------------------")]
    setup = source[source.index("static int ask_flow_offload_setup_tc_common("):
                   source.index("EXPORT_SYMBOL_GPL(ask_flow_offload_setup_tc);")]
    indirect = source[source.index("static int ask_flow_indr_setup_block_cb("):
                      source.index("/* Lifecycle")]
    indirect = indirect[:indirect.rfind("/* ----------------------------------------------------------------")]
    registration = source[source.index("static bool ask_flow_offload_registered;"):
                          source.index("int ask_flow_offload_init(void)")]
    exit_body = source[source.index("void ask_flow_offload_exit("):]
    unregister = exit_body[exit_body.index("\tif (ask_flow_offload_registered) {"):
                           exit_body.index("\task_fe_orphan_exit();")]
    main = (ROOT / "kernel/ask/oot-modules/ask/ask_main.c").read_text()
    main = main[main.index("static int __init ask_init("):
                main.index("module_init(ask_init);")]
    work = tmp_path_factory.mktemp("ask-flow-block")
    (work / "ask_flow_block_body.h").write_text(
        private + "\nstatic LIST_HEAD(ask_flow_block_cb_list);\n" +
        setup + indirect +
        "\n#define ask_flow_offload_register actual_provider_register\n" +
        registration + "\n#undef ask_flow_offload_register\n" +
        "\nstatic void unregister_provider(void)\n{\n" +
        unregister + "}\n"
    )
    (work / "ask_main_body.h").write_text(main)
    binary = work / "ask-flow-block"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")),
         "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(work), str(ROOT / "tests/dut/ask_flow_block_lifetime.c"),
         "-o", str(binary)],
        check=True,
    )
    return binary


@pytest.mark.parametrize("scenario", [
    "native-identity", "shared-device", "duplicate-bind", "allocation-failure",
    "indirect-unbind", "provider-cleanup", "registration-failure", "init-unwind",
])
def test_flow_block_lifetime(block_binary, scenario):
    subprocess.run([str(block_binary), scenario], check=True, timeout=30)
