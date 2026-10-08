#!/usr/bin/env python3
"""Reproduce the standalone TinyCNN RTL correctness and scheduling experiment."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
NPU = ROOT / "hardware/npu"
LAYERS = ("conv1", "pool1", "conv2", "pool2", "gap", "fc")
SIZES = (2560, 640, 640, 160, 8)
RTL = (
    "ws_pe", "ws_systolic_array", "matrix_unit", "vector_unit",
    "requant_unit", "reduction_sum_unit", "conv_window_addr_gen",
    "conv2d_engine", "maxpool2x2_engine", "global_sum_pool_engine",
    "global_avg_pool_engine", "tinycnn8_npu_top",
)


def command(argv, log, timeout=180):
    start = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([str(x) for x in argv], cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        text = error.stdout or ""
        if isinstance(text, bytes): text = text.decode(errors="replace")
        log.write_text("$ " + " ".join(map(str, argv)) + "\n" + text + "\nTIMEOUT\n")
        raise
    log.write_text("$ " + " ".join(map(str, argv)) + "\n" + result.stdout)
    if result.returncode:
        raise RuntimeError(f"command exited {result.returncode}: {log}\n{result.stdout[-5000:]}")
    return result.stdout, round(time.monotonic() - start, 3)


def sources(rtl_dir=None):
    rtl_dir = rtl_dir or NPU / "rtl"
    return [rtl_dir / (name + ".sv") for name in RTL]


def compile_tb(top, build, logs, params=(), rtl_dir=None, tb_path=None):
    image = build / (top + "_" + "_".join(f"{k}{v}" for k, v in params) + ".vvp")
    argv = ["iverilog", "-g2012", "-Wall", "-s", top, "-o", image]
    argv.extend(f"-P{top}.{k}={v}" for k, v in params)
    argv.extend(sources(rtl_dir))
    if top == "tb_conv2d_tiled_lifecycle":
        argv.append(NPU / "tb/tb_conv2d_engine.sv")
    if "mmio" in top:
        argv.append(ROOT / "hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv")
    argv.append(tb_path or NPU / "tb" / (top + ".sv"))
    text, _ = command(argv, logs / (image.stem + "_compile.log"))
    if "parameter" in text.lower() and "not found" in text.lower():
        raise AssertionError(f"unsupported testbench parameter: {top}; {text}")
    return image


def check_outputs(data, run, classes):
    actual = [dict() for _ in LAYERS]
    with (run / "outputs.csv").open() as stream:
        for row in csv.DictReader(stream):
            layer, index, value = (int(row[key]) for key in ("layer", "index", "value"))
            if layer not in range(6) or index in actual[layer]:
                raise AssertionError(f"invalid/duplicate layer/index {layer}/{index}")
            actual[layer][index] = value
    checks = []
    for layer, name in enumerate(LAYERS):
        width = 32 if name == "fc" else 8
        expected = [int(x, 16) for x in (data / f"golden_{name}.hex").read_text().split()]
        expected = [x - (1 << width) if x & (1 << (width - 1)) else x for x in expected]
        size = classes if name == "fc" else SIZES[layer]
        if len(expected) != size or set(actual[layer]) != set(range(size)):
            raise AssertionError(f"{name}: wrong output indices/count {len(actual[layer])}, expected {size}")
        for index, value in enumerate(expected):
            if actual[layer][index] != value:
                raise AssertionError(f"{name}[{index}] expected {value}, actual {actual[layer][index]}")
        checks.append({"layer": name, "elements": size, "status": "PASS"})
    with (run / "perf.csv").open() as stream:
        perf = {row["layer"]: {key: int(value) for key, value in row.items() if key != "layer"}
                for row in csv.DictReader(stream)}
    if sum(perf[str(i)]["cycles"] for i in range(6)) + perf["CTRL"]["cycles"] != perf["TOTAL"]["cycles"]:
        raise AssertionError("layer cycle accounting does not close")
    for i in (0, 2, 5):
        if perf[str(i)]["matrix_inputs"] != perf[str(i)]["matrix_outputs"]:
            raise AssertionError("matrix transaction loss")
    if perf["0"]["useful_mac"] != 21344 or perf["2"]["useful_mac"] != 39424:
        raise AssertionError("padding/useful-MAC accounting mismatch")
    (run / "validation.json").write_text(json.dumps(checks, indent=2) + "\n")
    return perf, sum(row["elements"] for row in checks)


def unit_regressions(build, logs):
    tests = [
        ("tb_ws_pe", ()),
        ("tb_tinycnn8_npu_mmio_wrapper", ()),
        ("tb_matrix_unit", (("ARRAY_ROWS", 4), ("ARRAY_COLS", 8))),
        ("tb_matrix_unit", (("ARRAY_ROWS", 4), ("ARRAY_COLS", 4))),
        ("tb_conv_window_addr_gen", ()),
        ("tb_requant_unit", (("LANES", 8),)),
        ("tb_reduction_sum_unit", (("LANES", 8),)),
        ("tb_maxpool2x2_engine", (("LANES", 8),)),
        ("tb_global_avg_pool_engine", (("LANES", 8),)),
    ]
    for top in ("tb_conv2d_engine", "tb_tinycnn8_npu_top"):
        for opt, tiled in ((0, 0), (1, 0), (0, 1)):
            for cols in (8, 4):
                for layer in ((1, 2) if top == "tb_conv2d_engine" else (None,)):
                    params = (("ARRAY_ROWS", 4), ("ARRAY_COLS", cols), ("OPT_GATHER_LOAD", opt), ("OPT_SPATIAL_TILE", tiled), ("SPATIAL_TILE", 16))
                    if layer is not None:
                        params += (("TEST_LAYER", layer),)
                    tests.append((top, params))
    # Bounded lifecycle/fallback checks complement the full network matrix.
    for gather, tile in ((0, 0), (1, 0)):
        tests.append(("tb_conv2d_tiled_lifecycle", (("OPT_GATHER_LOAD", gather),
            ("OPT_SPATIAL_TILE", tile), ("SPATIAL_TILE", 16))))
    for extras in ((("FALLBACK_PAD", 0),), (("ARRAY_ROWS", 8),), (("FINAL_INT32", 1),)):
        tests.append(("tb_conv2d_engine", (("TEST_LAYER", 2), ("OPT_SPATIAL_TILE", 1),
            ("TEST_LIFECYCLE", 1), ("SPATIAL_TILE", 16)) + extras))
    result = []
    for top, params in tests:
        image = compile_tb(top, build, logs, params)
        text, elapsed = command(["vvp", image], logs / (image.stem + ".log"))
        if not ("PASS" in text or "PASSED" in text):
            raise AssertionError(f"no completion evidence: {top}")
        result.append({"test": top, "parameters": dict(params), "status": "PASS", "seconds": elapsed})
        print(f"PASS {top} {dict(params)}", flush=True)
    for tile in (0, 81):
        image = compile_tb("tb_tinycnn8_fileio", build, logs,
            (("OPT_SPATIAL_TILE", 1), ("SPATIAL_TILE", tile)))
        log = logs / (image.stem + "_expected_rejection.log")
        try:
            command(["vvp", image], log)
        except RuntimeError:
            if "SPATIAL_TILE must be in range 1..80" not in log.read_text():
                raise AssertionError(f"wrong tile guard failure: {log}")
        else:
            raise AssertionError(f"invalid tile {tile} was not rejected")
        result.append({"test": "tile_parameter_guard", "tile": tile,
                       "status": "PASS", "expected_rejection": True})
        print(f"PASS expected tile rejection {tile}", flush=True)
    return result


def independent_requant(golden, build, logs):
    rng = random.Random(20261007)
    cases = []
    boundary = (-2147483648, -2147483647, -65537, -129, -3, -1, 0, 1, 3, 127, 65537, 2147483647)
    for i, value in enumerate(boundary):
        for shift in (-32, -31, -4, -1, 0, 1, 15, 31):
            multiplier = (-2147483648, 1073741824, 1717986918, 2147483647)[i % 4]
            cases.append((value, 0, multiplier, shift, 0, -128, 127, 1))
    cases.extend([
        (2147483647, 1, 1073741824, 0, 0, -128, 127, 1),
        (-2147483648, -1, 1073741824, 0, 0, -128, 127, 1),
        (-2147483648, 0, -2147483648, 0, 0, -128, 127, 1),
        (2147483647, 0, 2147483647, 0, 2147483647, -128, 127, 1),
        (-2147483648, 0, 2147483647, 0, -2147483648, -128, 127, 1),
    ])
    for _ in range(300):
        low = rng.choice((-128, -64, 0))
        high = rng.choice((63, 100, 127))
        cases.append((rng.randint(-2**31, 2**31-1), rng.randint(-2**31, 2**31-1),
                      rng.randint(-2**31, 2**31-1), rng.randint(-32, 31),
                      rng.choice((0, -2147483648, 2147483647, 17)), low, high, rng.randrange(2)))
    words = []
    for acc, bias, mult, shift, offset, low, high, mask in cases:
        # Reserved encoding -32 is an RTL underflow-to-zero guard, not valid Q31.
        if not mask:
            expected = 0
        elif shift == -32:
            expected = min(high, max(low, golden.wrap32(offset)))
        else:
            expected = golden.requant(acc, bias, mult, shift, offset, low, high)
        word = 0
        for value, width in ((acc, 32), (bias, 32), (mult, 32), (offset, 32),
                             (low, 8), (high, 8), (expected, 8), (mask, 8), (shift, 8)):
            word = (word << width) | (value & ((1 << width) - 1))
        words.append(f"{word:042x}")
    vectors = logs / "requant_oracle.hex"
    vectors.write_text("\n".join(words) + "\n")
    image = compile_tb("tb_tinycnn8_requant_fileio", build, logs)
    text, elapsed = command(["vvp", image, f"+VECTORS={vectors}", f"+COUNT={len(cases)}"], logs / "requant_oracle.log")
    if "PASS independent" not in text:
        raise AssertionError("requant independent oracle did not complete")
    return {"test": "independent_requant", "vectors": len(cases), "status": "PASS", "seconds": elapsed}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=NPU / "sim/tinycnn8_tiled")
    parser.add_argument("--quick", action="store_true", help="one network case; unit regressions still run")
    parser.add_argument("--seeds", type=int, default=8, help="additional tile-only random seeds")
    parser.add_argument("--skip-units", action="store_true", help="development smoke only; reported explicitly")
    options = parser.parse_args()
    if not 0 <= options.seeds <= 64:
        parser.error("--seeds must be in 0..64")
    for tool in ("iverilog", "vvp", "git"):
        if not shutil.which(tool):
            raise SystemExit(f"required tool is missing: {tool}; no dependencies installed automatically")
    required_files = (NPU / "tools/tinycnn8_golden.py", NPU / "tools/test_tinycnn8_golden.py")
    if any(not path.is_file() for path in required_files):
        raise SystemExit("golden reference or its unit tests are missing")
    output = options.output.resolve()
    if output.exists() and any(output.iterdir()):
        output = output / (time.strftime("rerun_%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1000000:06d}")

    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    logs.mkdir()
    spec = importlib.util.spec_from_file_location("tinycnn8_golden", NPU / "tools/tinycnn8_golden.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    cases = [("random_s7_c6", 7, 6, "random")]
    if not options.quick:
        cases.extend([("random_s29_c4", 29, 4, "random"), ("impulse_s11_c6", 11, 6, "impulse"),
                      ("extreme_s5_c6", 5, 6, "extreme")])
    summary = {"scope": "standalone RTL; synthetic model/data; combinational behavioral memories",
               "baseline_ref": "current workspace, optimizations disabled", "host": platform.platform(), "python": sys.version,
               "cases": [], "regressions": []}
    (output / "summary.json").write_text(json.dumps({**summary, "status": "RUNNING"}, indent=2) + "\n")
    print(f"Results: {output}", flush=True)
    with tempfile.TemporaryDirectory(prefix="tinycnn8-build-") as directory:
        build = Path(directory)
        unittest_text, _ = command([sys.executable, "-m", "unittest", "discover", "-s", NPU / "tools", "-p", "test_tinycnn8_golden.py", "-v"], logs / "golden_unittest.log")
        if "Ran 0 tests" in unittest_text or "\nOK" not in unittest_text:
            raise AssertionError("golden unit tests did not execute successfully")
        summary["regressions"] = [] if options.skip_units else unit_regressions(build, logs)
        summary["unit_regressions_skipped"] = options.skip_units
        summary["regressions"].append(independent_requant(golden, build, logs))
        modes = {"baseline": (0, 0, 16), "overlap": (1, 0, 16)}
        modes.update({f"tile{tile}": (0, 1, tile) for tile in (8, 16, 32, 7)})
        parameters = {name: tuple(zip(("OPT_GATHER_LOAD", "OPT_SPATIAL_TILE", "SPATIAL_TILE"), values))
                      for name, values in modes.items()}
        images = {name: compile_tb("tb_tinycnn8_fileio", build, logs, params)
                  for name, params in parameters.items()}
        lifecycle = NPU / "tb/tb_conv2d_tiled_lifecycle.sv"
        if not options.skip_units:
            if not lifecycle.exists():
                raise RuntimeError("required tile lifecycle TB not yet available")
            for tile in (8, 16, 32, 7):
                image = compile_tb(lifecycle.stem, build, logs, (("SPATIAL_TILE", tile),))
                text, elapsed = command(["vvp", image], logs / (image.stem + ".log"))
                if "PASS" not in text: raise AssertionError("tile lifecycle missing PASS")
                summary["regressions"].append({"test": lifecycle.stem, "tile": tile, "status": "PASS", "seconds": elapsed})
        mmio_path = NPU / "tb/tb_tinycnn8_mmio_fileio.sv"
        mmio_images = {name: compile_tb(mmio_path.stem, build, logs, params)
                       for name, params in parameters.items()} if not options.skip_units else {}
        complete_cases = list(cases)
        if not options.quick:
            cases += [(f"random_s{seed}_c{classes}", seed, classes, "random")
                      for seed, classes in zip(range(101, 101 + options.seeds),
                                                (1, 8, 3, 7, 2, 5, 8, 6) * ((options.seeds + 7)//8))]
        compared = 0
        for case, seed, classes, pattern in cases:
            case_dir = output / "cases" / case
            data = case_dir / "data"
            golden.generate(data, seed=seed, classes=classes, pattern=pattern)
            entry = {"case": case, "seed": seed, "classes": classes, "pattern": pattern, "runs": {}}
            for name, image in images.items():
                if (case, seed, classes, pattern) not in complete_cases and name in ("baseline", "overlap"):
                    continue
                run = case_dir / name
                run.mkdir(parents=True)
                argv = ["vvp", image, f"+DATA_DIR={data}", f"+OUT_DIR={run}", f"+CLASSES={classes}"]
                text, elapsed = command(argv, run / "simulation.log")
                if "PASS fileio" not in text:
                    raise AssertionError("file-driven run did not complete")
                perf, elements = check_outputs(data, run, classes)
                compared += elements
                tile = modes[name][2]
                expected_rows = 4 * 18 * ((80 + tile - 1) // tile) if modes[name][1] else 5760
                if perf["2"]["weight_rows"] != expected_rows or perf["2"]["matrix_inputs"] != 1440:
                    raise AssertionError(f"C2 transaction count {name}: {perf['2']}")
                if perf["0"]["weight_rows"] != 3840 or perf["0"]["matrix_inputs"] != 960:
                    raise AssertionError("C1 schedule changed")
                if perf["5"]["useful_mac"] != classes * 8:
                    raise AssertionError("FC useful MAC mismatch")
                with (run / "hardware_profile.csv").open() as stream:
                    hardware = {k: int(v) for k, v in next(csv.DictReader(stream)).items()}
                entry["runs"][name] = {"hardware_profile": hardware, "parameters": dict(parameters[name]),
                    "logical_capacity": {"activation_pack_bytes": 8 if modes[name][1] else 4,
                        "partial_sum_bytes": tile * 8 * 4 if modes[name][1] else 32,
                        "tag_fifo_entries": 16 if modes[name][1] else 1}, "perf": perf, "elements": elements, "seconds": elapsed, "status": "PASS"}
                print(f"PASS {case} {name}: {elements} elements, {perf['TOTAL']['cycles']} cycles", flush=True)
            if "baseline" in entry["runs"]:
                baseline = entry["runs"]["baseline"]["perf"]
                for name, run_result in entry["runs"].items():
                    optimized = run_result["perf"]
                    for layer in ("0", "1", "3", "4", "5", "CTRL"):
                        if baseline[layer]["cycles"] != optimized[layer]["cycles"]:
                            raise AssertionError(f"unoptimized path cycle count changed {name} layer {layer}")
                    run_result["cycles_saved"] = baseline["TOTAL"]["cycles"] - optimized["TOTAL"]["cycles"]
                    run_result["speedup"] = baseline["TOTAL"]["cycles"] / optimized["TOTAL"]["cycles"]
                for name, image in mmio_images.items():
                    run = case_dir / ("mmio_" + name);run.mkdir()
                    # Second distinct case, with the same class count, starts without reset.
                    data2 = case_dir / "data_second"
                    if not data2.exists(): golden.generate(data2, seed=seed+1000, classes=classes, pattern="random")
                    text, elapsed = command(["vvp", image, f"+DATA_DIR={data}", f"+DATA_DIR2={data2}",
                        f"+OUT_DIR={run}", f"+CLASSES={classes}"], run / "simulation.log")
                    if "PASS" not in text: raise AssertionError("MMIO fileio missing PASS")
                    summary["regressions"].append({"test": "mmio_fileio", "case": case, "mode": name,
                        "jobs": 3, "status": "PASS", "seconds": elapsed})
                    with (run / "mmio_perf.csv").open() as stream:
                        profiles = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(stream)]
                    if [r["job"] for r in profiles] != [0, 1, 2]:
                        raise AssertionError("MMIO did not cover three consecutive jobs")
                    hw = entry["runs"][name]["hardware_profile"]
                    for profile in profiles:
                        for mmio_key, native_key in (("total_cycles", "total_cycles"), ("weight_rows", "weight_rows"),
                            ("matrix_issues", "issues"), ("matrix_retires", "retires"), ("peak_inflight", "peak_inflight")):
                            if profile[mmio_key] != hw[native_key]:
                                raise AssertionError(f"MMIO/native profile mismatch {name} {mmio_key}")
                        for mmio_key, native_key in zip(("conv1_cycles", "pool1_cycles", "conv2_cycles", "pool2_cycles", "gap_cycles", "fc_cycles"), ("c1", "p1", "c2", "p2", "gap", "fc")):
                            if profile[mmio_key] != hw[native_key]:
                                raise AssertionError(f"MMIO/native layer profile mismatch {name} {mmio_key}")
                    if [r["jobacceptedwrites"] for r in profiles] != [932, 320, 932]:
                        raise AssertionError("MMIO load count mismatch")
                    entry["runs"][name]["mmio_hardware_profiles"] = profiles
                    with (run / "mmio_logits.csv").open() as stream:
                        rows = list(csv.DictReader(stream))
                    if len(rows) != 24 or len({(r["job"], r["class"]) for r in rows}) != 24:
                        raise AssertionError("MMIO logit coverage mismatch")
                    for row in rows:
                        if int(row["actual"]) != int(row["golden"]):
                            raise AssertionError("MMIO logit mismatch")
            summary["cases"].append(entry)
            (output / "summary.json").write_text(json.dumps({**summary, "status": "RUNNING"}, indent=2) + "\n")
    summary["compared_elements"] = compared
    summary["status"] = "SMOKE_PASS" if options.skip_units else "PASS"
    summary["tools"] = {"iverilog": subprocess.run(["iverilog", "-V"], capture_output=True, text=True).stdout.splitlines()[0],
                        "vvp": subprocess.run(["vvp", "-V"], capture_output=True, text=True).stderr.splitlines()[0]}
    tracked = [*sources(), *sorted((NPU / "tb").glob("tb_*.sv")),
               ROOT / "hardware/soc/rtl/npu/tinycnn8_npu_mmio_wrapper.sv", NPU / "tb/tb_tinycnn8_fileio.sv", NPU / "tb/tb_tinycnn8_requant_fileio.sv",
               Path(__file__), NPU / "tools/tinycnn8_golden.py"]
    summary["source_sha256"] = {str(path.resolve().relative_to(ROOT)): sha256(path) for path in tracked}
    summary["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    with (output / "comparison.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case", "mode", "layer", "trace_observed_cycles", "hardware_cycles", "baseline_cycles", "saved_cycles", "speedup", "weight_rows", "matrix_inputs", "peak_inflight"))
        for case in summary["cases"]:
            for name, run in case["runs"].items():
                for layer in (*map(str, range(6)), "CTRL", "TOTAL"):
                    opt = run["perf"][layer]["cycles"]
                    base = case["runs"].get("baseline", {}).get("perf", {}).get(layer, {}).get("cycles")
                    hw_key = ("c1", "p1", "c2", "p2", "gap", "fc")[int(layer)] if layer.isdigit() else "total_cycles" if layer == "TOTAL" else None
                    writer.writerow((case["case"], name, LAYERS[int(layer)] if layer.isdigit() else layer,
                        opt, run["hardware_profile"].get(hw_key, "n/a"), base, base-opt if base is not None else "n/a",
                        f"{base/opt:.6f}" if base is not None and opt else "n/a", run["perf"][layer]["weight_rows"],
                        run["perf"][layer]["matrix_inputs"], run["hardware_profile"]["peak_inflight"]))
    files = {str(path.relative_to(output)): sha256(path) for path in output.rglob("*") if path.is_file()}
    (output / "run_manifest.json").write_text(json.dumps({"status": summary["status"], "files_sha256": files,
        "rerun": "python3 hardware/npu/scripts/run_tinycnn8_regression.py --output NEW_EMPTY_DIRECTORY"}, indent=2) + "\n")
    print(f"{summary['status']} complete experiment: {compared} element comparisons\nResults: {output}")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
