#!/usr/bin/env python3
"""Generate data/ for the Week 3 progress slides (11-11 -> 11-18).

Every number on the slides is read here from files committed on main:
  - docs/validation/eda_latest_20261111/   (11-11 ASIC baseline)
  - docs/validation/eda_week3_20261118/    (PR #20: synth/PnR/signoff/MMMC/VCD power)
  - docs/validation/rtl_lookup_20261118/   (PR #19: matched seven-model RTL comparison)
  - docs/validation/jtag_rpc_20261118/     (PR #18: pipelined single-RPC host JTAG)
  - docs/validation/demo_software_20261116/ + software/host/demos/models/*/training_result.json (PR #17)
  - hardware/npu/scripts/mutation_test.py  (lookup mutants l01..l07)
The PR #15 RTL regression and mutant-kill counts exist only in the local commit
messages 44a76fc / 781afc2 (main history); they are parsed from `git log`.

Output: data/numbers.tex (\\N... macros) and table/plot snippets data/*.tex.
Idempotent; run from anywhere: python3 docs/slides/week3/make_data.py
"""
from __future__ import annotations
import csv
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'data'
VAL = ROOT / 'docs/validation'
OLD = VAL / 'eda_latest_20261111'
W3 = VAL / 'eda_week3_20261118'
RTL = VAL / 'rtl_lookup_20261118'
JT = VAL / 'jtag_rpc_20261118'
DEMO = VAL / 'demo_software_20261116'
MODELS = ROOT / 'software/host/demos/models'
LOOKUP_COMMIT, MUTANT_COMMIT = '44a76fc', '781afc2'


def jload(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def rows(p, delim=','):
    with open(p, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter=delim))


def git_msg(commit):
    out = subprocess.run(['git', 'log', '-1', '--format=%B', commit], cwd=ROOT, check=True,
                         text=True, stdout=subprocess.PIPE).stdout
    return ' '.join(out.split())


def grab(pattern, text, what):
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f'pattern not found in {what}: {pattern}')
    return m


def pct(new, old):
    return 100.0 * (new - old) / old


def sgn(x, nd=3):
    """Signed number for slack values: +0.628 / -16.772."""
    return f'{x:+.{nd}f}'


def esc(s):
    return s.replace('\\', '\\textbackslash{}').replace('_', '\\_').replace('&', '\\&').replace('%', '\\%')


def lookup_area(sb):
    ff = [v for k, v in sb['flops_by_register'].items() if k.startswith('npu_tensor_lookup')]
    comb = sum(v for k, v in sb['comb_by_module'].items() if k.startswith('npu_tensor_lookup'))
    return sum(v['count'] for v in ff), sum(v['area_um2'] for v in ff) + comb


def main():
    OUT.mkdir(exist_ok=True)
    N = {}

    # ------------------------------------------------------------------ ASIC delta
    om = jload(OLD / 'metrics.json')
    op = om['physical']
    nm = jload(W3 / 'metrics.json')
    osb, nsb = jload(OLD / 'synthesis_area_breakdown.json'), jload(W3 / 'synthesis_area_breakdown.json')
    opa, npa = jload(OLD / 'physical_area_breakdown.json'), jload(W3 / 'physical_area_breakdown.json')
    ncells = jload(W3 / 'synthesis_cells.json')

    lf0, la0 = lookup_area(osb)
    lf1, la1 = lookup_area(nsb)
    N['LookupFFBefore'], N['LookupFFAfter'] = f'{lf0:,}', f'{lf1:,}'
    N['LookupFFBeforeRaw'], N['LookupFFAfterRaw'] = str(lf0), str(lf1)
    N['LookupAreaBefore'], N['LookupAreaAfter'] = f'{la0 / 1e3:.1f}', f'{la1 / 1e3:.1f}'
    N['LookupAreaBeforeRaw'], N['LookupAreaAfterRaw'] = f'{la0 / 1e3:.1f}', f'{la1 / 1e3:.1f}'
    N['LookupAreaPct'] = f'{pct(la1, la0):.2f}'
    std0 = osb['flop_area_um2'] + osb['comb_area_um2']
    std1 = nsb['flop_area_um2'] + nsb['comb_area_um2']
    N['LookupShareBefore'] = f'{100 * la0 / std0:.1f}'
    N['SynStdBefore'], N['SynStdAfter'] = f'{std0 / 1e6:.3f}', f'{std1 / 1e6:.3f}'
    N['SynStdPct'] = f'{pct(std1, std0):.2f}'
    sq0 = sum(v['count'] for v in osb['flops_by_register'].values())
    sq1 = sum(v['count'] for v in nsb['flops_by_register'].values())
    assert sq1 == ncells['sequential_count']
    N['SynSeqBefore'], N['SynSeqAfter'] = f'{sq0:,}', f'{sq1:,}'
    N['SynSeqPct'] = f'{pct(sq1, sq0):.2f}'
    N['SynLatches'] = str(len(ncells['latches']))
    act0, act1 = op['active_standard_area_um2'], nm['active_standard_area_um2']
    N['ActBefore'], N['ActAfter'] = f'{act0 / 1e6:.3f}', f'{act1 / 1e6:.3f}'
    N['ActPct'] = f'{pct(act1, act0):.2f}'
    N['ActInstBefore'], N['ActInstAfter'] = f"{op['active_standard_instances']:,}", f"{nm['active_standard_instances']:,}"
    wl0, wl1 = op['wire_length_um'], nm['wire_length_um']
    N['WireBefore'], N['WireAfter'] = f'{wl0 / 1e6:.3f}', f'{wl1 / 1e6:.3f}'
    N['WirePct'] = f'{pct(wl1, wl0):.2f}'
    pw = jload(W3 / 'power_summary.json')
    vp0, vp1 = om['power']['total_mw'], pw['vectorless']['total_mw']
    N['VlessBefore'], N['VlessAfter'] = f'{vp0:.3f}', f'{vp1:.3f}'
    N['VlessPct'] = f'{pct(vp1, vp0):.2f}'
    N['DieBefore'], N['DieAfter'] = f"{op['die_area_mm2']:.6f}", f"{nm['die_area_mm2']:.6f}"
    N['DiePct'] = f"{pct(nm['die_area_mm2'], op['die_area_mm2']):.2f}"
    N['CoreAfter'] = f"{nm['core_area_mm2']:.6f}"
    N['TTSetupBefore'], N['TTHoldBefore'] = sgn(op['setup_wns_ns']), sgn(op['hold_wns_ns'])
    N['TTSetup'], N['TTHold'] = sgn(nm['setup_wns_ns']), sgn(nm['hold_wns_ns'])
    N['TTTns'] = f"{nm['setup_tns_ns'] + nm['hold_tns_ns']:.1f}"
    N['MacroCount'] = str(nm['macro_count'])
    N['MacroArea'] = f"{nm['macro_area_um2'] / 1e6:.3f}"
    N['CtsRepairArea'] = f"{nm['cts_and_repair_area_change_um2'] / 1e3:.1f}"
    N['CtsRepairInst'] = f"{npa['cts_and_later_net_instance_change']:,}"

    # Hierarchy table (post-route active area by group)
    om_rows = {r['group']: r for r in rows(OLD / 'physical_area_modules.csv')}
    nm_rows = {r['group']: r for r in rows(W3 / 'physical_area_modules.csv')}
    order = ['NPU', 'shared_store_logic', 'controller', 'host']
    lines, plot0, plot1 = [], [], []
    for g in order:
        a0, a1 = float(om_rows[g]['area_um2']), float(nm_rows[g]['area_um2'])
        s0, s1 = int(om_rows[g]['sequential_count']), int(nm_rows[g]['sequential_count'])
        lines.append(f'\\texttt{{{esc(g)}}} & {a0 / 1e3:,.1f} & {a1 / 1e3:,.1f} & {pct(a1, a0):+.2f}\\% '
                     f'& {s0:,} $\\to$ {s1:,} \\\\\n')
    (OUT / 'hier_rows.tex').write_text(''.join(lines), encoding='utf-8')
    assert abs(sum(float(r['area_um2']) for r in nm_rows.values()) - act1) < 1.0
    npu0, npu1 = float(om_rows['NPU']['area_um2']), float(nm_rows['NPU']['area_um2'])
    N['NpuAreaPct'] = f'{pct(npu1, npu0):.2f}'

    # Bar chart: before/after deltas for the five headline metrics
    deltas = [('查找表面积', pct(la1, la0)), ('触发器数', pct(sq1, sq0)),
              ('综合面积', pct(std1, std0)), ('布线后面积', pct(act1, act0)),
              ('总线长', pct(wl1, wl0)), ('无向量功耗', pct(vp1, vp0))]
    (OUT / 'delta_plot.tex').write_text('\\addplot[fill=oiblue, draw=none] coordinates {'
        + ' '.join(f'({-v:.2f},{k})' for k, v in deltas) + '};\n', encoding='utf-8')
    N['DeltaTicks'] = ','.join(k for k, _ in deltas)

    # Geometry checks
    checks = jload(W3 / 'checks.json')
    N['GeoPass'], N['GeoTotal'] = str(sum(bool(v) for v in checks.values())), str(len(checks))
    exp = jload(W3 / 'experiment.json')
    N['DieSide'] = f"{exp['geometry']['die_side_um']:,}"
    N['CoreSide'] = f"{exp['geometry']['core_side_um']:,}"
    N['HaloUm'] = f"{exp['geometry']['halo_um']:.1f}"
    N['Layout'] = esc(exp['geometry']['layout'])
    readme = (W3 / 'README.md').read_text(encoding='utf-8')
    N['Candidate'] = esc(grab(r'Selected P&R Candidate\*\*.*?`(\w+)`', readme, 'README')[1])
    N['Util'] = grab(r'utilization = ([\d.]+)', readme, 'README')[1]
    N['HoldTarget'] = grab(r'hold_target = ([\d.]+) ns', readme, 'README')[1]
    m = grab(r'Core `([\d.]+) ns` \(`(\d+) MHz`\)', readme, 'README (core clock)')
    N['CorePeriod'], N['CoreMHz'] = m[1], m[2]
    bp = jload(W3 / 'build_profile.json')
    N['Cols'] = str(bp['array_cols'])
    COURSE_DIE_LIMIT_UM = 1950  # Lab 4 die limit (course site, see course_lab_audit.md)
    N['CourseDieLimit'] = f'{COURSE_DIE_LIMIT_UM:,}'
    assert exp['geometry']['die_side_um'] <= COURSE_DIE_LIMIT_UM

    # ------------------------------------------------------------------ signoff
    so = jload(W3 / 'signoff_summary.json')
    r = so['results']
    assert so['passed'] and all(v['passed'] for v in r.values())
    N['StaSetup'], N['StaHold'] = sgn(r['sta']['setup_wns_ns']), sgn(r['sta']['hold_wns_ns'])
    N['StaUnannot'] = 'false' if not r['sta']['unannotated'] else 'true'
    N['LecVerified'] = 'verified' if r['lec']['verified'] else 'FAILED'
    N['DrcTotal'] = f"{r['drc']['total']:,}"
    N['DrcBlocking'] = str(len(r['drc']['blocking']))
    io = sum(c for loc in r['drc']['locations'].values() for k, c in loc.items() if k == 'io_ring')
    N['DrcIoRing'] = f'{io:,}'
    N['DrcCore'] = str(sum(c for loc in r['drc']['locations'].values() for k, c in loc.items() if k == 'core'))
    N['DrcEsdTwoFive'] = f"{r['drc']['violated_rules']['ESD.25g']:,}"
    N['LvsErr'] = str(r['lvs']['accepted_io_resistor_errors'])
    N['LvsMaxDev'] = f"{r['lvs']['max_io_resistor_deviation_percent']:.2f}"
    N['LvsRaw'] = 'INCORRECT' if r['lvs']['incorrect'] else 'CORRECT'
    m = grab(r'`(\d+a)` SPICE vs `(\d+a)` GDS IO library.*?`≤ (\d+)%`', readme, 'README')
    N['LvsLibA'], N['LvsLibB'], N['LvsPolicy'] = m[1], m[2], m[3]
    sdf = jload(W3 / 'sdf_result.json')
    assert sdf['passed']
    N['SdfCmds'] = str(sdf['jtag_commands'])
    N['SdfIfnone'] = f"{sdf['diagnostics']['accepted_ifnone_paths']['total']:,}"
    N['SdfFatal'] = str(len(sdf['diagnostics']['fatal']) + len(sdf['diagnostics']['unexpected_sdfnep'])
                        + len(sdf['diagnostics']['unexpected_sdfnet']))
    N['SdfPadConf'] = str(sdf['accepted_time0_input_pad_conflicts'])

    # ------------------------------------------------------------------ MMMC / SRAM / CDC
    mm = jload(W3 / 'mmmc_summary.json')
    cor = {(c['corner'], c['mode']): c for c in rows(W3 / 'mmmc_corners.csv')}
    key = {'tt25': 'TT', 'ff0': 'FFz', 'ffm40': 'FFm', 'ss125': 'SS', 'SS125_DERATED': 'SSD'}
    for c, k in key.items():
        N[f'{k}Setup'] = sgn(float(cor[(c, 'setup')]['wns']))
        N[f'{k}HoldW'] = sgn(float(cor[(c, 'hold')]['wns']))
    clean = ['tt25', 'ff0', 'ffm40']
    assert all(float(cor[(c, m)]['wns']) >= 0 for c in clean for m in ('setup', 'hold'))
    hmin = min(clean, key=lambda c: float(cor[(c, 'hold')]['wns']))
    N['CleanHoldMin'], N['CleanHoldMinCorner'] = sgn(float(cor[(hmin, 'hold')]['wns'])), hmin
    ss = cor[('ss125', 'setup')]
    N['SSTns'] = f"{float(ss['tns_2000']):,.1f}"
    N['SSViol'] = f"{int(ss['violating_endpoints_2000']):,}"
    N['SSArr'], N['SSReq'] = ss['arrival'], ss['required']
    N['SSEndSram'] = 'SRAM' if 'i_sram' in ss['end'] else esc(ss['end'])
    m = grab(r'derating `core_clk` to \*\*`([\d.]+) ns` \(`([\d.]+) MHz`\)', readme, 'README (SS125_DERATED period)')
    N['SSDPeriod'], N['SSDFreq'] = m[1], m[2]
    N['RcNote'] = esc(mm['rc_note'])
    sram = rows(W3 / 'sram_interface_timing.csv')
    assert len(sram) == mm['sram_rows']
    N['SramRows'] = str(len(sram))
    N['SramNA'] = str(sum(v in ('', 'NA') for r_ in sram for v in r_.values()))
    sl = []
    for r_ in sram:
        if r_['corner'] == 'ss125':
            continue
        sl.append(f"\\texttt{{{r_['corner']}}} & {r_['role']} & {float(r_['setup_to']):+.3f} & {float(r_['hold_to']):+.3f} "
                  f"& {float(r_['setup_from']):+.3f} & {float(r_['hold_from']):+.3f} \\\\\n")
    (OUT / 'sram_rows.tex').write_text(''.join(sl), encoding='utf-8')
    ssr = [r_ for r_ in sram if r_['corner'] == 'ss125']
    N['SramSSWorst'] = sgn(min(float(r_['setup_to']) for r_ in ssr))
    cdc = rows(W3 / 'cdc_4corners.tsv', '\t')
    assert len(cdc) == mm['cdc_rows']
    N['CdcRows'] = str(len(cdc))
    N['CdcPos'] = str(sum(float(r_['slack_ns']) > 0 for r_ in cdc))
    N['CdcCorners'] = str(len({r_['corner'] for r_ in cdc}))
    N['CdcPaths'] = str(len(cdc) // len({r_['corner'] for r_ in cdc}))
    by = {}
    for r_ in cdc:
        by[r_['corner']] = min(by.get(r_['corner'], 1e9), float(r_['slack_ns']))
    for c, k in key.items():
        if c in by:
            N[f'Cdc{k}Min'] = sgn(by[c])
    cmin = min(by, key=by.get)
    N['CdcMin'], N['CdcMinCorner'] = sgn(by[cmin]), cmin
    N['MmmcCorners'] = str(len({c for c, _ in cor} - {'SS125_DERATED'}))
    N['CdcKinds'] = str(len({r_['kind'] for r_ in cdc}))

    # ------------------------------------------------------------------ power
    wl = {w['name']: w for w in pw['workloads']}
    order = ['cnn_f1', 'cnn_f0', 'ecg', 'har', 'mnist_l']
    assert [w['name'] for w in pw['workloads']] == order
    assert all(w['activity_percent'] == 100.0 for w in pw['workloads'])
    N['PwAnnot'] = f"{min(w['activity_percent'] for w in pw['workloads']):.1f}"
    N['PwN'] = str(len(order))
    pl = []
    for n in order:
        w = wl[n]
        pl.append(f"\\texttt{{{esc(n)}}} & {w['npu_cycles']:,} & {w['dt_ns'] / 1e3:,.2f} & {w['total_mw']:.2f} "
                  f"& {w['energy_uJ']:.4f} \\\\\n")
    (OUT / 'power_rows.tex').write_text(''.join(pl), encoding='utf-8')
    vl = pw['vectorless']['total_mw']
    (OUT / 'power_plot.tex').write_text(
        '\\addplot[fill=oiblue, draw=none] coordinates {'
        + ' '.join(f"({i},{wl[n]['total_mw']:.2f})" for i, n in enumerate(order)) + '};\n'
        + f'\\draw[oiverm, dashed, line width=0.8pt] (axis cs:-0.5,{vl:.2f}) -- (axis cs:{len(order) - 0.5},{vl:.2f});\n'
        + f'\\node[font=\\tiny, text=oiverm, anchor=south east, inner sep=1pt] at (axis cs:{len(order) - 0.5},{vl:.2f}) '
        + f'{{vectorless {vl:.2f} mW}};\n', encoding='utf-8')
    N['PowerTicks'] = ','.join(f'\\texttt{{{esc(n)}}}' for n in order)
    N['PowerXmax'] = f'{len(order) - 0.5}'
    N['PowerLast'] = str(len(order) - 1)
    sav = 100 * (1 - wl['cnn_f1']['energy_uJ'] / wl['cnn_f0']['energy_uJ'])
    assert abs(sav - pw['cnn_energy_savings_percent']) < 1e-6
    N['FusionSave'] = f'{sav:.2f}'
    N['FusedE'], N['UnfusedE'] = f"{wl['cnn_f1']['energy_uJ']:.4f}", f"{wl['cnn_f0']['energy_uJ']:.4f}"
    N['FusedCyc'], N['UnfusedCyc'] = f"{wl['cnn_f1']['npu_cycles']:,}", f"{wl['cnn_f0']['npu_cycles']:,}"
    N['FusionCycPct'] = f"{100 * (1 - wl['cnn_f1']['npu_cycles'] / wl['cnn_f0']['npu_cycles']):.2f}"
    tot = [w['total_mw'] for w in pw['workloads']]
    N['VcdMin'], N['VcdMax'] = f'{min(tot):.2f}', f'{max(tot):.2f}'
    N['VlessRaw'] = f'{vp1:.2f}'
    N['VlessOverMin'], N['VlessOverMax'] = f'{vp1 / max(tot):.2f}', f'{vp1 / min(tot):.2f}'

    # ------------------------------------------------------------------ RTL lookup (PR #15 / #19)
    prov = jload(RTL / 'provenance.json')
    N['RtlModels'] = str(len(prov['packaged_models']))
    N['RtlConfigs'] = str(len(prov['runs']))
    N['RtlNeg'] = str(len(prov['runs']['behavioral']['negative_checks_after']))
    N['RtlBefore'], N['RtlAfter'] = prov['before_commit'], prov['after_commit']
    mc = rows(RTL / 'model_cycles.csv')
    assert all(r_['writes_before'] == r_['writes_after'] for r_ in mc)
    N['RtlRuns'] = str(len(mc))
    d = [int(r_['cold_cycle_delta']) for r_ in mc]
    p = [float(r_['cold_cycle_pct']) for r_ in mc]
    N['CycDeltaMin'], N['CycDeltaMax'] = str(-max(d)), str(-min(d))
    N['CycPctMin'], N['CycPctMax'] = f'{-max(p):.2f}', f'{-min(p):.2f}'
    cl = []
    beh = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'behavioral'}
    fpg = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'fpga_mem'}
    for mdl in prov['packaged_models']:
        b, f = beh[mdl], fpg[mdl]
        cl.append(f"\\texttt{{{esc(mdl)}}} & {int(b['cold_cycles_before']):,} $\\to$ {int(b['cold_cycles_after']):,} "
                  f"& {int(b['cold_cycle_delta']):+d} & {int(f['cold_cycles_before']):,} $\\to$ {int(f['cold_cycles_after']):,} "
                  f"& {float(f['cold_cycle_pct']):+.2f}\\% \\\\\n")
    (OUT / 'cycle_rows.tex').write_text(''.join(cl), encoding='utf-8')

    msg = git_msg(LOOKUP_COMMIT)
    m = grab(r'(\d+)x(\d+)-bit provenance table', msg, LOOKUP_COMMIT)
    N['Records'], N['RecordBitsOld'] = m[1], m[2]
    N['RecordWordBits'] = grab(r'one (\d+)-bit word', msg, LOOKUP_COMMIT)[1]
    for k, pat in [('RegBeh', r'behavioral (\d+/\d+)'), ('RegFpga', r'FPGA_MEM (\d+/\d+)'),
                   ('RegMacro', r'RA1SHD (\d+/\d+)'), ('FuzzPass', r'fuzz (\d+) pass'),
                   ('CdcLimits', r'CDC limits (\d+/\d+)'), ('MutOld', r'strict mutation (\d+/\d+)')]:
        N[k] = grab(pat, msg, LOOKUP_COMMIT)[1]

    src = (ROOT / 'hardware/npu/scripts/mutation_test.py').read_text(encoding='utf-8')
    muts = re.findall(r"\('(l\d\d)', (CORE|CTRL),", src)
    zh = {'l01': '输出覆盖源张量后未作废旧记录',
          'l02': '记录结束地址差一（off-by-one）',
          'l03': '作废写未被接受就报告完成',
          'l04': '前一条记录写未完成即读下一条（RAW）',
          'l05': '记录写未排空即公布校验通过',
          'l06': '忽略校验结果，未知来源也放行',
          'l07': '校验期间未把激活端口交给 NPU'}
    ids = sorted(i for i, _ in muts)
    assert ids == sorted(zh), ids
    where = dict(muts)
    (OUT / 'mutants.tex').write_text(''.join(
        f"\\texttt{{{i}}} & {'core' if where[i] == 'CORE' else 'ctrl'} & {zh[i]} \\\\\n" for i in ids), encoding='utf-8')
    N['MutNew'] = str(len(ids))
    km = grab(r'All (\w+) are killed \(([^)]*)\)', git_msg(MUTANT_COMMIT), MUTANT_COMMIT)
    words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
    killed = words.get(km[1], None) or int(km[1])
    assert killed == len(ids)
    N['MutKilled'] = str(killed)
    N['MutKillPct'] = f'{100 * killed / len(ids):.0f}'
    N['MutKilledBy'] = '、'.join({'lookup equivalence bench': '查表等价测试', "negative 'source' test": '负例 source 测试',
                                 'protocol checker': '协议检查器', 'timeout': '超时'}[x.strip()] for x in km[2].split(','))

    # ------------------------------------------------------------------ JTAG single-RPC (PR #18)
    e2e = [r_ for r_ in jload(JT / 'e2e_timing.json') if r_['protocol'] == 'burst']
    sp = []
    jl = []
    for r_ in e2e:
        b, q, lg = r_['rpc_counts']['batched'], r_['rpc_counts']['pipeline'], r_['rpc_counts']['legacy']
        s = b['estimated_rpc_ms'] / q['estimated_rpc_ms']
        sp.append(s)
        jl.append(f"\\texttt{{{esc(r_['model'])}}} & {lg['load_rpcs'] + lg['infer_rpcs']} & {b['load_rpcs'] + b['infer_rpcs']} "
                  f"& {q['load_rpcs'] + q['infer_rpcs']} & {b['estimated_rpc_ms']:.0f} $\\to$ {q['estimated_rpc_ms']:.0f} "
                  f"& {s:.1f}$\\times$ \\\\\n")
    (OUT / 'jtag_rows.tex').write_text(''.join(jl), encoding='utf-8')
    N['RpcModels'] = str(len(e2e))
    N['RpcSpMin'], N['RpcSpMax'] = f'{min(sp):.1f}', f'{max(sp):.1f}'
    pipe_loads = {r_['rpc_counts']['pipeline']['load_rpcs'] for r_ in e2e}
    pipe_infers = {r_['rpc_counts']['pipeline']['infer_rpcs'] for r_ in e2e}
    assert len(pipe_loads) == 1 and len(pipe_infers) == 1
    N['RpcPipeLoad'] = str(next(iter(pipe_loads)))
    N['RpcPipeInfer'] = str(next(iter(pipe_infers)))
    N['RpcPipe'] = str(next(iter(pipe_loads)) + next(iter(pipe_infers)))
    N['RpcMsPer'] = grab(r'([\d.]+) ms/RPC', e2e[0]['rpc_latency_assumption'], 'e2e_timing.json')[1]
    anc = {r_['protocol']: r_ for r_ in jload(JT / 'ecg_1mhz_anchor.json')}
    N['AnchorTck'] = f"{1e3 / anc['burst']['tck_ns']:.0f}"
    N['AnchorBurstMs'] = f"{anc['burst']['total_tck'] * anc['burst']['tck_ns'] / 1e6:.1f}"
    N['AnchorWordMs'] = f"{anc['wordwise']['total_tck'] * anc['wordwise']['tck_ns'] / 1e6:.1f}"
    N['AnchorUpPct'] = f"{100 * anc['burst']['phases']['model_upload']['tck'] / anc['burst']['total_tck']:.1f}"
    N['AnchorRatio'] = f"{anc['wordwise']['total_tck'] / anc['burst']['total_tck']:.1f}"

    # ------------------------------------------------------------------ Demo (PR #17)
    s = jload(DEMO / 'summary.json')
    assert all(c['passed'] for c in s['checks'].values())
    N['DemoUnit'], N['DemoJtag'] = str(s['demo_unit_tests']), str(s['jtag_unit_tests'])
    N['DemoGolden'], N['DemoGoldenTotal'] = str(s['golden_complete_outputs_equal']), str(s['golden_frames'])
    N['DemoZoo'] = str(s['existing_zoo_frames'])
    N['DemoChecks'] = str(len(s['checks']))
    N['DemoFpga'] = 'true' if s['fpga_validated'] else 'false'
    N['DemoNotTested'] = str(len(s['not_tested']))
    emo = jload(MODELS / 'emotion/training_result.json')['evaluation']
    face = jload(MODELS / 'face/training_result.json')['evaluation']
    N['EmoAcc'], N['EmoSamples'] = f"{100 * emo['accuracy']:.2f}", f"{emo['samples']:,}"
    N['FaceP'], N['FaceR'], N['FaceF'] = (f"{100 * face[k]:.1f}" for k in ('precision', 'recall', 'f1'))
    N['FaceSamples'] = f"{face['samples']:,}"
    demo_py = (ROOT / 'software/host/demo.py').read_text(encoding='utf-8')
    N['NumScenes'] = str(len(re.findall(r'^\s+"\w+": \(', demo_py, re.M)))
    # Review fixes in 1759302 (PR #17), each pinned by a regression test in test_demos.py
    tests = (ROOT / 'software/tests/test_demos.py').read_text(encoding='utf-8')
    fixes = ['test_browser_restart_waits_for_the_previous_upload_queue',
             'test_phone_follow_resumes_after_gap_and_ignores_partial_tail',
             'test_fpga_validation_requires_every_vector_from_every_demo']
    assert all(f'def {t}(' in tests for t in fixes)
    N['DemoFixes'] = str(len(fixes))
    gap = grab(r'dt > ([\d.]+)', (ROOT / 'software/host/demos/signals.py').read_text(encoding='utf-8'), 'signals.py')
    N['HarGapMs'] = f'{float(gap[1]) * 1e3:.0f}'

    bad = [k for k in N if not re.fullmatch(r'[A-Za-z]+', k)]
    assert not bad, bad
    (OUT / 'numbers.tex').write_text('% generated by make_data.py -- do not edit\n' + ''.join(
        f'\\newcommand{{\\N{k}}}{{{v}}}\n' for k, v in N.items()), encoding='utf-8')
    for k, v in N.items():
        print(f'{k:20s} {v}')


if __name__ == '__main__':
    main()
