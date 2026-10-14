#!/usr/bin/env python3
"""Regression for the fused TinyCNN-8 core (tinycnn8_npu_fused).

Runs, with iverilog/vvp only:
  * tb_requant_lane: pipelined single lane vs requant_unit, 20k operands;
  * tb_tinycnn8_npu_top against the fused core on 4x8 and 4x4 arrays;
  * tb_tinycnn8_npu_mmio_wrapper with FUSED_CORE=1 and FUSED_CORE=0;
  * tb_tinycnn8_fused_golden on golden cases (random/impulse/extreme
    patterns, 1..8 classes), checking Pool1, Pool2 and logits bit-exactly
    over two back-to-back jobs, and recording the hardware profile.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
NPU = ROOT / "hardware/npu"
RTL = NPU / "rtl"
FUSED = [RTL / f"{name}.sv" for name in ("fused_ws_array", "requant_lane", "tinycnn8_npu_fused")]
LAYERED = [RTL / f"{name}.sv" for name in (
    "ws_pe", "ws_systolic_array", "matrix_unit", "vector_unit", "requant_unit",
    "conv_window_addr_gen", "conv2d_engine", "maxpool2x2_engine", "tinycnn8_npu_top")]
WRAPPER = ROOT / "hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv"
sys.path.insert(0, str(NPU / "tools"))
from check_fused_events import check_trace, read_hex  # noqa: E402
from test_fused_events import negative_checks  # noqa: E402
PROFILE = re.compile(r"PROFILE run=(\d+) classes=(\d+) total=(\d+) C1=(\d+) C2=(\d+) FC=(\d+) "
                     r"weight_rows=(\d+) issues=(\d+) peak_inflight=(\d+)")


def run(argv, expect):
    result = subprocess.run([str(x) for x in argv], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode or expect not in result.stdout:
        raise SystemExit(f"FAIL: {' '.join(map(str, argv))}\n{result.stdout[-4000:]}")
    return result.stdout


def compile_tb(build, top, sources, params=(), defines=()):
    image = build / (top + "".join(f"_{k}{v}" for k, v in params) + "".join(f"_{d}" for d in defines) + ".vvp")
    argv = ["iverilog", "-g2012", "-s", top, "-o", image]
    argv += [f"-P{top}.{k}={v}" for k, v in params]
    argv += [f"-D{d}" for d in defines]
    run(argv + list(sources), "")
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=16, help="random golden cases (classes cycle 1..8)")
    parser.add_argument("--summary", type=Path, help="optional JSON summary path")
    options = parser.parse_args()
    for tool in ("iverilog", "vvp"):
        if not shutil.which(tool):
            raise SystemExit(f"required tool is missing: {tool}")
    spec = importlib.util.spec_from_file_location("tinycnn8_golden", NPU / "tools/tinycnn8_golden.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    summary = {"units": [], "cases": []}
    with tempfile.TemporaryDirectory(prefix="tinycnn8-fused-") as directory:
        build = Path(directory)
        image = compile_tb(build, "tb_requant_lane", [RTL / "requant_unit.sv", RTL / "requant_lane.sv",
                                                      NPU / "tb/tb_requant_lane.sv"])
        print(run(["vvp", image], "PASS requant_lane").strip().splitlines()[0])
        summary["units"].append("tb_requant_lane")
        for cols in (8, 4):
            image = compile_tb(build, "tb_tinycnn8_npu_top", FUSED + [NPU / "tb/tb_tinycnn8_npu_top.sv"],
                               (("ARRAY_COLS", cols),), ("TINYCNN8_CORE=tinycnn8_npu_fused",))
            print(run(["vvp", image], "ALL TINYCNN8 TOP TESTS PASSED").strip().splitlines()[0])
            summary["units"].append(f"tb_tinycnn8_npu_top fused 4x{cols}")
        for fused in (1, 0):
            image = compile_tb(build, "tb_tinycnn8_npu_mmio_wrapper",
                               LAYERED + FUSED + [WRAPPER, NPU / "tb/tb_tinycnn8_npu_mmio_wrapper.sv"],
                               (("FUSED_CORE", fused),))
            text = run(["vvp", image], "ALL TINYCNN8 MMIO WRAPPER TESTS PASSED")
            print(f"FUSED_CORE={fused}: " + text.strip().splitlines()[0])
            summary["units"].append(f"tb_tinycnn8_npu_mmio_wrapper FUSED_CORE={fused}")

        for a_bytes, b_bytes in ((640, 2560), (4096, 4096)):
            image = compile_tb(build, "tb_tinycnn8_capacity",
                               LAYERED + [NPU / "tb/tb_tinycnn8_capacity.sv"],
                               (("ACT_A_BANK_BYTES", a_bytes), ("ACT_BANK_BYTES", b_bytes)))
            print(run(["vvp", image], "ALL FLAT CAPACITY TESTS PASSED").strip())
            summary["units"].append(f"tb_tinycnn8_capacity A={a_bytes} B={b_bytes}")

        image = compile_tb(build, "tb_tinycnn8_fused_golden", FUSED + [NPU / "tb/tb_tinycnn8_fused_golden.sv"],
                           defines=("TINYCNN8_FUSED_EVENTS",))
        cases = [(seed, 1 + (seed - 1) % 8, "random") for seed in range(1, options.seeds + 1)]
        cases += [(11, 6, "impulse"), (12, 4, "impulse"), (5, 6, "extreme"), (6, 8, "extreme"), (7, 4, "random")]
        compared = 0
        for seed, classes, pattern in cases:
            data = build / f"{pattern}_s{seed}_c{classes}"
            golden.generate(data, seed=seed, classes=classes, pattern=pattern)
            trace = data / "events.csv"
            text = run(["vvp", image, f"+DATA_DIR={data}", f"+CLASSES={classes}",
                        f"+EVENT_TRACE={trace}"], "PASS fused golden")
            args = ([read_hex(data / "input.hex")], read_hex(data / "weights.hex"),
                    read_hex(data / "bias.hex"), read_hex(data / "multiplier.hex"),
                    read_hex(data / "shift.hex"), classes)
            events = check_trace(trace, *args)
            if len(events) != 2:
                raise SystemExit(f"FAIL: missing event jobs for {data.name}")
            if not summary["cases"]:
                summary["negative_event_checks"] = negative_checks(trace, *args)
            profiles = [dict(zip(("run", "classes", "total", "c1", "c2", "fc", "weight_rows",
                                  "issues", "peak_inflight"), map(int, m.groups())))
                        for m in PROFILE.finditer(text)]
            if len(profiles) != 2 or profiles[0]["total"] != profiles[1]["total"]:
                raise SystemExit(f"FAIL: unstable or missing profile for {data.name}")
            compared += 2 * (640 + 160 + 8)
            summary["cases"].append({"case": data.name, "profile": profiles[0], "event_jobs": events})
            p = profiles[0]
            print(f"PASS {data.name}: total={p['total']} C1={p['c1']} C2={p['c2']} FC={p['fc']} "
                  f"weight_rows={p['weight_rows']} issues={p['issues']}")
    summary["element_comparisons"] = compared
    if options.summary:
        options.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"ALL FUSED TINYCNN8 TESTS PASSED: {len(summary['units'])} unit runs, "
          f"{len(summary['cases'])} golden cases, {compared} element comparisons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
