#!/usr/bin/env python3
"""Sweep the JTAG mailbox CDC across its operating limits (tb_npu_jtag_cdc_limits).

Grid: system clock x TCK rate (both directions far past the 50 MHz board
point), then stress points per system clock (per-edge TCK jitter + duty
extremes, 50 % late-resolving synchronizers, random sub-cycle asynchronous
resets, long destination backpressure) and overrun points (host issues a
second command without polling), several seeds each.

Any functional failure (assertion) makes the run fail. Bundled-data margins
are measured, not asserted: the result lists, per system clock, the largest
max-delay bound the request/response bundles can be constrained to, and
whether the constraint the implementation uses (--bound-ns, default 20 ns)
lies inside that envelope.

  run_cdc_limits.py --output build/cdc_limits [--quick] [--jobs 2]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
RTL = ROOT / 'hardware/soc/rtl/control/npu_jtag_transport.sv'
BENCH = Path(__file__).with_name('tb_npu_jtag_cdc_limits.sv')

SYS_NS = [5, 8, 10, 20, 40, 100]                 # 200 .. 10 MHz system clock
TCK_HALF = [2.5, 5, 10, 25, 50, 100, 500, 2500]   # TCK 200 MHz .. 200 kHz


def points(quick):
    sys_ns = [5, 20, 100] if quick else SYS_NS
    tck = [2.5, 25, 500] if quick else TCK_HALF
    seeds = [1] if quick else [1, 2]
    for s in sys_ns:
        for t in tck:
            n = 40 if t >= 500 else 120
            for seed in seeds:
                yield dict(kind='grid', SYS_NS=s, TCK_LO=t, TCK_HI=t, N=n, SEED=seed)
        for seed in (seeds if quick else [1, 2, 3]):
            yield dict(kind='stress', SYS_NS=s, TCK_LO=2, TCK_HI=max(60, 4 * s), JITTER=1, META=50, RESET=20,
                       BP=40, N=200, SEED=100 + seed)
            yield dict(kind='meta_only', SYS_NS=s, TCK_LO=2, TCK_HI=max(60, 4 * s), JITTER=1, META=50,
                       N=200, SEED=200 + seed)
            # Host protocol violation: a second command without polling. Slow destination +
            # fast TCK makes the first still pending, so the overrun guard must hold.
            yield dict(kind='overrun', SYS_NS=s, TCK_LO=2.5, TCK_HI=2.5, BP=60, OVR=40, N=150, SEED=300 + seed)


def run_point(image, p, bound):
    args = [f'+{k}={v}' for k, v in p.items() if k != 'kind'] + [f'+BOUND_NS={bound}']
    r = subprocess.run(['vvp', '-n', str(image), *args], capture_output=True, text=True, timeout=3600)
    text = r.stdout + r.stderr
    m = re.search(r'^RESULT (.*)$', text, re.M)
    row = dict(p)
    if r.returncode == 0 and m and 'PASS NPU_JTAG_CDC_LIMITS' in text:
        for k, v in re.findall(r'(\w+)=\s*(\S+)', m.group(1)):
            row[k] = v
        row['status'] = 'PASS'
    else:
        row['status'] = 'FAIL'
        row['detail'] = (re.search(r'(FATAL|fatal)[^\n]*\n?[^\n]*', text) or re.search(r'.*$', text)).group(0)[:200]
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--bound-ns', type=float, default=20.0)
    ap.add_argument('--rtl', type=Path, default=RTL, help='transport RTL (mutation runs pass a copy)')
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    image = out / 'cdc_limits.vvp'
    c = subprocess.run(['iverilog', '-g2012', '-s', 'tb_npu_jtag_cdc_limits', '-o', str(image), str(args.rtl), str(BENCH)],
                       capture_output=True, text=True)
    (out / 'compile.log').write_text(c.stdout + c.stderr)
    if c.returncode:
        sys.exit(f'compile failed:\n{c.stdout}{c.stderr}')
    pts = list(points(args.quick))
    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda p: run_point(image, p, args.bound_ns), pts))
    for r in rows:
        print(r['status'], r['kind'], f"sys={r['SYS_NS']}ns tck_half={r['TCK_LO']}-{r['TCK_HI']}ns",
              r.get('min_req_margin_ns', ''), r.get('min_rsp_margin_ns', ''), r.get('detail', ''), flush=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (out / 'cdc_limits.csv').open('w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    # Envelope per system clock: request margin depends on the system clock;
    # response margin on TCK, so report the TCK half-period where it drops below the bound.
    env = []
    for s in sorted({r['SYS_NS'] for r in rows}):
        ok = [r for r in rows if r['SYS_NS'] == s and r['status'] == 'PASS']
        req = min(float(r['min_req_margin_ns']) for r in ok) if ok else None
        rsp_by_tck = {}
        for r in ok:
            if r['kind'] == 'grid':
                rsp_by_tck[r['TCK_LO']] = min(rsp_by_tck.get(r['TCK_LO'], 1e9), float(r['min_rsp_margin_ns']))
        fastest_ok = min((t for t, v in rsp_by_tck.items() if v >= args.bound_ns), default=None)
        env.append(dict(sys_ns=s, sys_mhz=round(1000 / s, 1), min_request_margin_ns=req,
                        request_bound_ok=req is not None and req >= args.bound_ns,
                        fastest_tck_half_ns_with_response_margin=fastest_ok,
                        response_margin_by_tck_half=rsp_by_tck))
    fails = [r for r in rows if r['status'] != 'PASS']
    summary = dict(bound_ns=args.bound_ns, points=len(rows), failures=len(fails), envelope=env,
                   resets=sum(int(r.get('resets', 0)) for r in rows),
                   metastability_injections=sum(int(r.get(k, 0)) for r in rows for k in ('meta_req', 'meta_ack', 'meta_rst')),
                   overruns_dropped=sum(int(r.get('ovr_hit', 0)) for r in rows))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'envelope'}))
    for e in env:
        print(f"ENVELOPE sys {e['sys_mhz']} MHz: request margin {e['min_request_margin_ns']} ns "
              f"({'OK' if e['request_bound_ok'] else 'BELOW'} {args.bound_ns} ns); "
              f"fastest TCK half-period meeting the response bound: {e['fastest_tck_half_ns_with_response_margin']} ns")
    if fails:
        print(f'CDC LIMITS FAILED: {len(fails)} operating points')
        sys.exit(1)
    if summary['overruns_dropped'] == 0:
        print('CDC LIMITS FAILED: coverage hole, no overrun was ever dropped (overrun guard unexercised)')
        sys.exit(1)
    print(f'ALL {len(rows)} CDC LIMIT POINTS PASSED')


if __name__ == '__main__':
    main()
