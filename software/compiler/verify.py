"""Three-level bit-exact verifier: Full-Graph Integer Oracle == TiledPlan == RTL/FPGA."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np

from .ir import GraphIR, oracle_reference
from .runtime import ReferenceBackend, RtlBackend, TiledRuntime
from .tiler import TiledPlan


def verify_plan(graph: GraphIR, plan: TiledPlan, sample_input: np.ndarray, *,
                run_rtl: bool = False, npu=None) -> dict:
    """Assert 0-bit mismatch between Full-Graph Oracle, TiledPlan(Ref), and optional RTL/FPGA."""
    oracle_all = oracle_reference(graph, sample_input)
    expected = oracle_all[graph.output]

    ref_out, ref_tel = TiledRuntime(ReferenceBackend()).run(plan, sample_input)
    if np.issubdtype(expected.dtype, np.floating):
        ref_exact = bool(np.allclose(expected, ref_out, atol=1e-6, rtol=1e-5))
        max_abs_diff = float(np.max(np.abs(expected - ref_out)))
    else:
        ref_exact = bool(np.array_equal(expected, ref_out))
        max_abs_diff = int(np.max(np.abs(expected.astype(np.int64) - ref_out.astype(np.int64))))
    if not ref_exact:
        raise AssertionError(f'TiledPlan Reference mismatch vs Oracle: max_abs_diff={max_abs_diff}')

    report = {
        'passed': True,
        'output_shape': list(expected.shape),
        'output_dtype': str(expected.dtype),
        'oracle_vs_tiled_ref_exact': ref_exact,
        'max_abs_diff': max_abs_diff,
        'plan_summary': plan.summary(),
        'ref_telemetry': ref_tel,
    }

    if run_rtl:
        rtl_be = RtlBackend()
        try:
            rtl_out, rtl_tel = TiledRuntime(rtl_be).run(plan, sample_input)
        finally:
            rtl_be.close()
        if np.issubdtype(expected.dtype, np.floating):
            rtl_exact = bool(np.allclose(expected, rtl_out, atol=1e-6, rtol=1e-5))
        else:
            rtl_exact = bool(np.array_equal(expected, rtl_out))
        if not rtl_exact:
            raise AssertionError('TiledPlan RTL mismatch vs Oracle')
        report['oracle_vs_rtl_exact'] = rtl_exact
        report['rtl_telemetry'] = rtl_tel

    if npu is not None:
        from .runtime import NpuBackend
        fpga_out, fpga_tel = TiledRuntime(NpuBackend(npu)).run(plan, sample_input)
        if np.issubdtype(expected.dtype, np.floating):
            fpga_exact = bool(np.allclose(expected, fpga_out, atol=1e-6, rtol=1e-5))
        else:
            fpga_exact = bool(np.array_equal(expected, fpga_out))
        if not fpga_exact:
            raise AssertionError('TiledPlan FPGA mismatch vs Oracle')
        report['oracle_vs_fpga_exact'] = fpga_exact
        report['fpga_telemetry'] = fpga_tel

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan-dir', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--rtl', action='store_true')
    args = ap.parse_args()
    plan = TiledPlan.load(args.plan_dir)
    rng = np.random.default_rng(args.seed)
    lo, hi = (0, 16) if plan.graph.input.dtype == 'u4' else (-8, 8)
    sample = rng.integers(lo, hi, size=plan.graph.input.shape, dtype=np.int64)
    res = verify_plan(plan.graph, plan, sample, run_rtl=args.rtl)
    print(json.dumps(res, indent=2))


if __name__ == '__main__':
    main()
