#!/usr/bin/env python3
'''Generate data/ for the Week 3 group-meeting slides.

Every number on the slides is read here from committed files in this worktree:
  docs/validation/eda_latest_20261111/       (11-11 ASIC baseline)
  docs/validation/eda_week3_lab5_20261119/   (unified Lab 4 + Lab 5 single-GDS record)
  docs/validation/rtl_lookup_20261118/       (matched seven-model RTL comparison)
  docs/validation/jtag_rpc_20261118/         (single-RPC host JTAG evidence)
  docs/validation/demo_software_20261116/ + software/host/demos/models/*/training_result.json
  verification/fpga/results/2026-11-11-jtag-frequency/  (EGo1 8x8 board sweep)
  hardware/npu/scripts/mutation_test.py and git log 44a76fc / 781afc2 (regression and mutant counts)

Output: data/numbers.tex (macros with an N prefix) plus table and plot snippets data/*.tex.
Idempotent; run from anywhere: python3 docs/slides/week3/make_data.py
'''
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
W3 = VAL / 'eda_week3_lab5_20261119'
RTL = VAL / 'rtl_lookup_20261118'
JT = VAL / 'jtag_rpc_20261118'
DEMO = VAL / 'demo_software_20261116'
FPGA = ROOT / 'verification/fpga/results/2026-11-11-jtag-frequency'
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
    return f'{x:+.{nd}f}'


def esc(s):
    return (str(s).replace('\\', '\\textbackslash{}').replace('_', '\\_')
            .replace('&', '\\&').replace('%', '\\%'))


# <<SECTION:asic>>
BS = chr(92)


def lookup_area(sb):
    ff = [v for k, v in sb['flops_by_register'].items() if k.startswith('npu_tensor_lookup')]
    comb = sum(v for k, v in sb['comb_by_module'].items() if k.startswith('npu_tensor_lookup'))
    return sum(v['count'] for v in ff), sum(v['area_um2'] for v in ff) + comb


def sec_asic(N):
    om = jload(OLD / 'metrics.json')
    op = om['physical']
    nm = jload(W3 / 'metrics.json')
    osb, nsb = jload(OLD / 'synthesis_area_breakdown.json'), jload(W3 / 'synthesis_area_breakdown.json')
    ncells = jload(W3 / 'synthesis_cells.json')
    pw = jload(W3 / 'power_summary.json')
    readme = (W3 / 'README.md').read_text(encoding='utf-8')
    lf0, la0 = lookup_area(osb)
    lf1, la1 = lookup_area(nsb)
    N['LookupFFBefore'], N['LookupFFAfter'] = f'{lf0:,}', f'{lf1:,}'
    N['LookupAreaBefore'], N['LookupAreaAfter'] = f'{la0 / 1e3:.1f}', f'{la1 / 1e3:.1f}'
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
    act0, act1 = op['active_standard_area_um2'], nm['active_standard_area_um2']
    N['ActBefore'], N['ActAfter'] = f'{act0 / 1e6:.3f}', f'{act1 / 1e6:.3f}'
    N['ActPct'] = f'{pct(act1, act0):.2f}'
    N['ActInstAfter'] = f"{nm['active_standard_instances']:,}"
    wl0, wl1 = op['wire_length_um'], nm['wire_length_um']
    N['WireBefore'], N['WireAfter'] = f'{wl0 / 1e6:.3f}', f'{wl1 / 1e6:.3f}'
    N['WirePct'] = f'{pct(wl1, wl0):.2f}'
    vp0, vp1 = om['power']['total_mw'], pw['vectorless']['total_mw']
    N['VlessBefore'], N['VlessAfter'] = f'{vp0:.2f}', f'{vp1:.2f}'
    N['VlessPct'] = f'{pct(vp1, vp0):.2f}'
    N['DieBefore'], N['DieAfter'] = f"{op['die_area_mm2']:.3f}", f"{nm['die_area_mm2']:.3f}"
    N['CoreAfter'] = f"{nm['core_area_mm2']:.3f}"
    N['TTSetup'], N['TTHold'] = sgn(nm['setup_wns_ns']), sgn(nm['hold_wns_ns'])
    N['MacroCount'] = str(nm['macro_count'])
    N['MacroArea'] = f"{nm['macro_area_um2'] / 1e6:.3f}"
    N['CtsRepairArea'] = f"{nm['cts_and_repair_area_change_um2'] / 1e3:.1f}"
    N['SramSeqCount'] = f"{ncells['sequential_count']:,}"

    om_rows = {r['group']: r for r in rows(OLD / 'physical_area_modules.csv')}
    nm_rows = {r['group']: r for r in rows(W3 / 'physical_area_modules.csv')}
    lines = []
    for g in ['NPU', 'shared_store_logic', 'controller', 'host']:
        a0, a1 = float(om_rows[g]['area_um2']), float(nm_rows[g]['area_um2'])
        s0, s1 = int(om_rows[g]['sequential_count']), int(nm_rows[g]['sequential_count'])
        lines.append(f'{BS}texttt{{{esc(g)}}} & {a0 / 1e3:,.1f} & {a1 / 1e3:,.1f} & {pct(a1, a0):+.2f}{BS}% '
                     f'& {s0:,} ${BS}to$ {s1:,} {BS}{BS}\n')
    (OUT / 'hier_rows.tex').write_text(''.join(lines), encoding='utf-8')
    assert abs(sum(float(r['area_um2']) for r in nm_rows.values()) - act1) < 1.0

    deltas = [('查找表面积', pct(la1, la0)), ('触发器数', pct(sq1, sq0)), ('综合面积', pct(std1, std0)),
              ('布线后面积', pct(act1, act0)), ('总线长', pct(wl1, wl0)), ('无向量功耗', pct(vp1, vp0))]
    (OUT / 'delta_plot.tex').write_text(f'{BS}addplot[fill=oiblue, draw=none] coordinates {{'
        + ' '.join(f'({-v:.2f},{k})' for k, v in deltas) + '};\n', encoding='utf-8')
    N['DeltaTicks'] = ','.join(k for k, _ in deltas)

    checks = jload(W3 / 'checks.json')
    N['GeoPass'], N['GeoTotal'] = str(sum(bool(v) for v in checks.values())), str(len(checks))
    l5 = jload(W3 / 'lab5_summary.json')
    N['DieSide'] = f"{l5['raw_die_bbox_um'][2]:.2f}"
    N['DieLimit'] = grab(r'`<= (\d+) µm` Lab 4 die limit', readme, 'README (Lab 4 die limit)')[1]
    assert l5['raw_die_bbox_um'][2] <= float(N['DieLimit'])
    N['Candidate'] = esc(grab(r'unified candidate \(`(\w+)`\)', readme, 'README (candidate)')[1])
    N['Util'] = grab(r'utilization `([\d.]+)`', readme, 'README (utilization)')[1]
    N['HoldTarget'] = grab(r'hold target `([\d.]+) ns`', readme, 'README (hold target)')[1]
    m = grab(r'Core `(\d+) MHz` \(`([\d.]+) ns`\)', readme, 'README (core clock)')
    N['CoreMHz'], N['CorePeriod'] = m[1], m[2]
    N['BaseCommit'] = grab(r'`(bf75486\w{0,33})`', readme, 'README (baseline commit)')[1][:7]
    N['Cols'] = str(jload(W3 / 'build_profile.json')['array_cols'])

    so = jload(W3 / 'signoff_summary.json')['results']
    N['StaSetup'], N['StaHold'] = sgn(so['sta']['setup_wns_ns']), sgn(so['sta']['hold_wns_ns'])
    N['LecVerified'] = 'verified' if so['lec']['verified'] else 'FAILED'
    sdf = jload(W3 / 'sdf_result.json')
    assert sdf['passed']
    d = sdf['diagnostics']
    N['SdfCmds'] = str(sdf['jtag_commands'])
    N['SdfUnexpected'] = str(len(d['fatal']) + len(d['unexpected_sdfnep']) + len(d['unexpected_sdfnet']))
    N['SdfPadConf'] = str(sdf['accepted_time0_input_pad_conflicts'])


# <<SECTION:lab5>>
def sec_lab5(N):
    l5 = jload(W3 / 'lab5_summary.json')
    ck = l5['checks']
    seal = l5['sealed_die_um']
    lim = l5['max_allowed_sealed_die_um']
    assert seal[0] == seal[1] and seal[0] <= lim
    N['DbuPerUm'] = str(l5['dbu_per_um'])
    N['SealSide'] = '%.1f' % seal[0]
    N['SealLimit'] = '%.0f' % lim
    N['FormalTapeout'] = 'true' if l5['formal_tapeout_signoff'] else 'false'
    dc = l5['dummy_config']
    N['DummyMsix'] = '%.1f' % dc['dmW_M6_um']
    assert dc['dmW_M6_um'] == dc['dmL_M6_um']

    drc = ck['drc']
    cats = drc['accepted_teaching_categories']
    assert drc['passed'] and not drc['blocking']
    assert sum(v['hier'] for v in cats.values()) == drc['raw_total_hier']
    assert sum(v['expanded'] for v in cats.values()) == drc['raw_total_expanded']
    N['DrcRules'] = str(drc['rules_executed'])
    N['DrcHier'] = '{:,}'.format(drc['raw_total_hier'])
    N['DrcExp'] = '{:,}'.format(drc['raw_total_expanded'])
    N['DrcBlocking'] = str(len(drc['blocking']))
    N['CorFourAfter'] = str(cats.get('CO.R.4', {}).get('hier', 0))
    N['CorFourBefore'] = str(jload(W3 / 'signoff_summary.json')['results']['drc']['accepted']['CO.R.4']['count'])

    lv = ck['lvs']
    strict, relaxed = lv['pr_0pct'], lv['pr_3_48pct']
    assert not lv['sram_blackboxed']
    assert relaxed['layout_ports'] == relaxed['source_ports'] == len(lv['top_ports'])
    N['LvsPorts'] = str(relaxed['layout_ports'])
    N['LvsStrictStatus'], N['LvsStatus'] = strict['status'], relaxed['status']
    N['LvsErr'] = str(strict['property_error_count'])
    N['LvsMax'] = '%.2f' % strict['max_io_resistor_deviation_pct']
    N['LvsTol'] = '%.2f' % relaxed['r_pr_tolerance_pct']
    assert strict['max_io_resistor_deviation_pct'] < relaxed['r_pr_tolerance_pct']

    ant, wb = ck['antenna'], ck['wirebond']
    assert ant['passed'] and wb['passed']
    N['AntRules'], N['WbRules'] = str(ant['rules_executed']), str(wb['rules_executed'])
    N['AntHier'], N['AntExp'] = str(ant['total_hier']), str(ant['total_expanded'])
    N['WbHier'], N['WbExp'] = str(wb['total_hier']), str(wb['total_expanded'])

    erc = ck['erc']
    er = erc['rules']
    assert sum(v['hier_count'] for v in er.values()) == erc['total_hier']
    assert sum(v['expanded_count'] for v in er.values()) == erc['total_expanded']
    N['ErcRules'] = str(erc['rules_executed'])
    N['ErcHier'], N['ErcExp'] = str(erc['total_hier']), str(erc['total_expanded'])
    for key, short in [('mnpg', 'Mnpg'), ('floating.nxwell_float', 'Nxwell'),
                       ('floating.psub_float', 'Psub'), ('npvss49', 'Vss')]:
        N['Erc' + short + 'H'] = str(er[key]['hier_count'])
        N['Erc' + short + 'E'] = str(er[key]['expanded_count'])
    geo = jload(W3 / 'final_erc_geometry.json')
    assert (geo['total_hier'], geo['total_expanded']) == (erc['total_hier'], erc['total_expanded'])

    with open(W3 / 'wirebond_keepouts.tsv', newline='', encoding='utf-8') as f:
        wbrows = [r for r in csv.reader(f, delimiter='\t') if r]
    margins = set()
    die = l5['raw_die_bbox_um']
    for r in wbrows:
        pad = [float(v) for v in r[1].split()]
        keep = [float(v) for v in r[2].split()]
        for i, (a, b) in enumerate(zip(pad, keep)):
            d = round(abs(a - b), 3)
            if d == 0:
                assert abs(b - die[i]) < 1e-6, (r[0], i)
            else:
                margins.add(d)
    assert len(margins) == 1
    N['PadCount'] = str(len(wbrows))
    N['PadMargin'] = '%.0f' % next(iter(margins))

    # Transistor-level ERC accounting (per IO cell type) from the deep-dive record
    dd = (W3 / 'erc_deep_dive.md').read_text(encoding='utf-8')
    m = grab(r'`(\d+) × (\d+)` \(`\d+`\) \+ `\((\d+) \+ (\d+)\) × (\d+)` \(`\d+`\) = \*\*`(\d+)`\*\*',
             dd, 'erc_deep_dive.md (mnpg accounting)')
    v1n, v1f, v2n, vpn, v2f = (int(m[i]) for i in range(1, 6))
    assert v1n * v1f + (v2n + vpn) * v2f == int(m[6]) == er['mnpg']['expanded_count']
    m2 = grab(r'`(\d+)` `PVSS1CDG` pads × `(\d+)` diode units = \*\*`(\d+)`\*\*', dd,
              'erc_deep_dive.md (PVSS1CDG accounting)')
    vsn, vsd = int(m2[1]), int(m2[2])
    assert vsn * vsd == int(m2[3]) == er['npvss49']['expanded_count'] \
        == er['floating.nxwell_float']['expanded_count']
    N['ErcVddOneN'], N['ErcVddOneF'] = str(v1n), str(v1f)
    N['ErcVddTwoN'], N['ErcVddPocN'], N['ErcVddTwoF'] = str(v2n), str(vpn), str(v2f)
    N['ErcVddOneE'], N['ErcVddTwoE'] = str(v1n * v1f), str((v2n + vpn) * v2f)
    N['ErcVssN'], N['ErcVssD'] = str(vsn), str(vsd)


# <<SECTION:mmmc>>
def sec_mmmc(N):
    mm = jload(W3 / 'mmmc_summary.json')
    readme = (W3 / 'README.md').read_text(encoding='utf-8')
    cor = {(c['corner'], c['mode']): c for c in rows(W3 / 'mmmc_corners.csv')}
    key = {'tt25': 'TT', 'ff0': 'FFz', 'ffm40': 'FFm', 'ss125': 'SS', 'SS125_DERATED': 'SSD'}
    for c, k in key.items():
        N[k + 'Setup'] = sgn(float(cor[(c, 'setup')]['wns']))
        N[k + 'HoldW'] = sgn(float(cor[(c, 'hold')]['wns']))
    clean = ['tt25', 'ff0', 'ffm40']
    assert all(float(cor[(c, m)]['wns']) >= 0 for c in clean for m in ('setup', 'hold'))
    hmin = min(clean, key=lambda c: float(cor[(c, 'hold')]['wns']))
    N['CleanHoldMin'] = sgn(float(cor[(hmin, 'hold')]['wns']))
    N['CleanHoldMinCorner'] = hmin
    ss = cor[('ss125', 'setup')]
    N['SSTns'] = '{:,.1f}'.format(float(ss['tns_2000']))
    N['SSViol'] = '{:,}'.format(int(ss['violating_endpoints_2000']))
    N['SSArr'], N['SSReq'] = ss['arrival'], ss['required']
    N['SSEndSram'] = 'SRAM' if 'i_sram' in ss['end'] else esc(ss['end'])
    m = grab(r'SS125_DERATED` \(SS, 125 .C\) \| `([\d.]+) ns` \(`([\d.]+) MHz`', readme, 'README (SS125_DERATED period)')
    N['SSDPeriod'], N['SSDFreq'] = m[1], m[2]
    assert float(N['SSDSetup']) > 0 and float(N['SSDHoldW']) > 0
    N['RcNote'] = esc(mm['rc_note'])

    sram = rows(W3 / 'sram_interface_timing.csv')
    assert len(sram) == mm['sram_rows']
    N['SramRows'] = str(len(sram))
    sl = []
    for r_ in sram:
        vals = tuple(float(r_[k]) for k in ('setup_to', 'hold_to', 'setup_from', 'hold_from'))
        sl.append(BS + 'texttt{' + r_['corner'] + '} & ' + r_['role'] + ' & %+.3f & %+.3f & %+.3f & %+.3f ' % vals + BS + BS + '\n')
    (OUT / 'sram_rows.tex').write_text(''.join(sl), encoding='utf-8')
    ssr = [r_ for r_ in sram if r_['corner'] == 'ss125']
    N['SramSSWorst'] = sgn(min(float(r_['setup_to']) for r_ in ssr))
    tt = [r_ for r_ in sram if r_['corner'] == 'tt25']
    N['SramTTWorst'] = sgn(min(float(r_['setup_to']) for r_ in tt))

    cdc = rows(W3 / 'cdc_4corners.tsv', '\t')
    assert len(cdc) == mm['cdc_rows']
    N['CdcRows'] = str(len(cdc))
    N['CdcPos'] = str(sum(float(r_['slack_ns']) > 0 for r_ in cdc))
    ncor = len({r_['corner'] for r_ in cdc})
    N['CdcCorners'] = str(ncor)
    N['CdcPaths'] = str(len(cdc) // ncor)
    by = {}
    for r_ in cdc:
        by[r_['corner']] = min(by.get(r_['corner'], 1e9), float(r_['slack_ns']))
    for c, k in key.items():
        if c in by:
            N['Cdc' + k + 'Min'] = sgn(by[c])
    cmin = min(by, key=by.get)
    N['CdcMin'], N['CdcMinCorner'] = sgn(by[cmin]), esc(cmin)
    N['MmmcCorners'] = str(len({c for c, _ in cor} - {'SS125_DERATED'}))


# <<SECTION:power>>
def sec_power(N):
    pw = jload(W3 / 'power_summary.json')
    wl = {w['name']: w for w in pw['workloads']}
    order = ['cnn_f1', 'cnn_f0', 'ecg', 'har', 'mnist_l']
    assert [w['name'] for w in pw['workloads']] == order
    assert all(w['activity_percent'] == 100.0 for w in pw['workloads'])
    N['PwAnnot'] = '%.0f' % min(w['activity_percent'] for w in pw['workloads'])
    N['PwN'] = str(len(order))
    pl = []
    for n in order:
        w = wl[n]
        pl.append(BS + 'texttt{' + esc(n) + '} & ' + '{:,} & {:,.2f} & {:.2f} & {:.4f} '.format(
            w['npu_cycles'], w['dt_ns'] / 1e3, w['total_mw'], w['energy_uJ']) + BS + BS + '\n')
    (OUT / 'power_rows.tex').write_text(''.join(pl), encoding='utf-8')
    vl = pw['vectorless']['total_mw']
    plot = (BS + 'addplot[fill=oiblue, draw=none] coordinates {'
            + ' '.join('(%d,%.2f)' % (i, wl[n]['total_mw']) for i, n in enumerate(order)) + '};\n'
            + BS + 'draw[oiverm, dashed, line width=0.8pt] (axis cs:-0.5,%.2f) -- (axis cs:%.1f,%.2f);\n' % (vl, len(order) - 0.5, vl)
            + BS + 'node[font=' + BS + 'tiny, text=oiverm, anchor=south east, inner sep=1pt] at (axis cs:%.1f,%.2f) {vectorless %.2f mW};\n' % (len(order) - 0.5, vl, vl))
    (OUT / 'power_plot.tex').write_text(plot, encoding='utf-8')
    N['PowerTicks'] = ','.join(BS + 'texttt{' + esc(n) + '}' for n in order)
    N['PowerXmax'] = '%.1f' % (len(order) - 0.5)
    N['PowerLast'] = str(len(order) - 1)
    f1, f0 = wl['cnn_f1'], wl['cnn_f0']
    sav = 100 * (1 - f1['energy_uJ'] / f0['energy_uJ'])
    assert abs(sav - pw['cnn_energy_savings_percent']) < 1e-6
    N['FusionSave'] = '%.2f' % sav
    N['FusedE'], N['UnfusedE'] = '%.4f' % f1['energy_uJ'], '%.4f' % f0['energy_uJ']
    N['FusedCyc'], N['UnfusedCyc'] = '{:,}'.format(f1['npu_cycles']), '{:,}'.format(f0['npu_cycles'])
    N['FusionSpeed'] = '%.2f' % (f0['npu_cycles'] / f1['npu_cycles'])
    N['FusedMw'], N['UnfusedMw'] = '%.2f' % f1['total_mw'], '%.2f' % f0['total_mw']
    tot = [w['total_mw'] for w in pw['workloads']]
    N['VcdMin'], N['VcdMax'] = '%.2f' % min(tot), '%.2f' % max(tot)
    N['VlessRaw'] = '%.2f' % vl
    N['VlessOverMin'], N['VlessOverMax'] = '%.2f' % (vl / max(tot)), '%.2f' % (vl / min(tot))


# <<SECTION:rtl>>
def sec_rtl(N):
    ARR = '$' + BS + 'to$'
    prov = jload(RTL / 'provenance.json')
    N['RtlModels'] = str(len(prov['packaged_models']))
    N['RtlConfigs'] = str(len(prov['runs']))
    N['RtlNeg'] = str(len(prov['runs']['behavioral']['negative_checks_after']))
    N['RtlBefore'], N['RtlAfter'] = prov['before_commit'][:7], prov['after_commit'][:7]
    mc = rows(RTL / 'model_cycles.csv')
    assert all(r_['writes_before'] == r_['writes_after'] for r_ in mc)
    N['RtlRuns'] = str(len(mc))
    d = [int(r_['cold_cycle_delta']) for r_ in mc]
    p = [float(r_['cold_cycle_pct']) for r_ in mc]
    N['CycDeltaMin'], N['CycDeltaMax'] = str(-max(d)), str(-min(d))
    N['CycPctMin'], N['CycPctMax'] = '%.2f' % -max(p), '%.2f' % -min(p)
    beh = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'behavioral'}
    fpg = {r_['model']: r_ for r_ in mc if r_['memory_config'] == 'fpga_mem'}
    cl = []
    for mdl in prov['packaged_models']:
        b, f = beh[mdl], fpg[mdl]
        cl.append(BS + 'texttt{' + esc(mdl) + '} & ' + '{:,}'.format(int(b['cold_cycles_before'])) + ' ' + ARR + ' '
                  + '{:,}'.format(int(b['cold_cycles_after'])) + ' & ' + '{:+d}'.format(int(b['cold_cycle_delta'])) + ' & '
                  + '{:,}'.format(int(f['cold_cycles_before'])) + ' ' + ARR + ' ' + '{:,}'.format(int(f['cold_cycles_after']))
                  + ' & ' + '{:+.2f}'.format(float(f['cold_cycle_pct'])) + BS + '% ' + BS + BS + '\n')
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
        BS + 'texttt{' + i + '} & ' + ('core' if where[i] == 'CORE' else 'ctrl') + ' & ' + zh[i] + ' ' + BS + BS + '\n'
        for i in ids), encoding='utf-8')
    N['MutNew'] = str(len(ids))
    km = grab(r'All (\w+) are killed \(([^)]*)\)', git_msg(MUTANT_COMMIT), MUTANT_COMMIT)
    words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
    killed = words.get(km[1]) or int(km[1])
    assert killed == len(ids)
    N['MutKilled'] = str(killed)
    N['MutKillPct'] = '%.0f' % (100 * killed / len(ids))
    names = {'lookup equivalence bench': '查表等价测试', "negative 'source' test": '负例 source 测试',
             'protocol checker': '协议检查器', 'timeout': '超时'}
    N['MutKilledBy'] = '、'.join(names[x.strip()] for x in km[2].split(','))


# <<SECTION:fpga>>
def sec_fpga(N):
    import math
    audit = jload(FPGA / 'audit.json')
    assert audit['fine']['passed'] and audit['coarse']['passed']
    N['FpgaCoarse'] = '{:,}'.format(audit['coarse']['inferences'])
    N['FpgaFine'] = '{:,}'.format(audit['fine']['inferences'])
    N['FpgaTotal'] = '{:,}'.format(audit['coarse']['inferences'] + audit['fine']['inferences'])

    def gm(v):
        return math.exp(sum(math.log(x) for x in v) / len(v))
    by = {}
    for r_ in rows(FPGA / 'fine/timing.csv'):
        by.setdefault(int(r_['khz']), []).append(r_)
    cold = {k: gm([float(r_['cold_ms']) for r_ in v]) for k, v in by.items()}
    res = {k: gm([float(r_['resident_ms']) for r_ in v]) for k, v in by.items()}
    assert min(cold, key=cold.get) == 1000
    N['FpgaBestMhz'] = '%g' % (min(cold, key=cold.get) / 1000)
    N['FpgaFreqs'] = str(len(by))
    N['FpgaModels'] = str(len(by[1000]))
    N['FpgaCold'], N['FpgaRes'] = '%.2f' % cold[1000], '%.2f' % res[1000]
    alt = min(res, key=res.get)
    N['FpgaAltKhz'], N['FpgaAltRes'] = str(alt), '%.2f' % res[alt]
    N['FpgaAltPct'] = '%.2f' % (100 * (res[1000] - res[alt]) / res[1000])
    N['FpgaColdMax'] = '%.2f' % max(cold.values())

    summ = (FPGA / 'implementation/summary.md').read_text(encoding='utf-8')
    m = grab(r'WNS: ([\d.]+) ns; TNS: ([\d.]+) ns; WHS: ([\d.]+) ns', summ, 'implementation/summary.md timing')
    N['VivWns'], N['VivWhs'] = '+' + m[1], '+' + m[3]
    assert float(m[2]) == 0.0
    m = grab(r'Slice LUTs \| (\d+) \| (\d+) \| ([\d.]+)', summ, 'summary LUT')
    N['VivLut'], N['VivLutPct'] = '{:,}'.format(int(m[1])), m[3]
    m = grab(r'Slice Registers \| (\d+) \| (\d+) \| ([\d.]+)', summ, 'summary FF')
    N['VivFf'], N['VivFfPct'] = '{:,}'.format(int(m[1])), m[3]
    N['VivBram'] = grab(r'Block RAM Tile \| (\d+)', summ, 'summary BRAM')[1]
    N['VivDsp'] = grab(r'DSPs \| (\d+)', summ, 'summary DSP')[1]
    N['VivPart'] = esc(grab(r'Part: (\S+?);', summ, 'summary part')[1])
    N['VivClk'] = grab(r'(\d+) MHz output', summ, 'summary clock')[1]


# <<SECTION:rpc>>
def sec_rpc(N):
    e2e = [r_ for r_ in jload(JT / 'e2e_timing.json') if r_['protocol'] == 'burst']
    assert len({r_['model'] for r_ in e2e}) == len(e2e)
    sp, nbs, jl = [], [], []
    for r_ in e2e:
        b, q = r_['rpc_counts']['batched'], r_['rpc_counts']['pipeline']
        nb, nq = b['load_rpcs'] + b['infer_rpcs'], q['load_rpcs'] + q['infer_rpcs']
        s = b['estimated_rpc_ms'] / q['estimated_rpc_ms']
        sp.append(s)
        nbs.append(nb)
        jl.append(BS + 'texttt{' + esc(r_['model']) + '} & %d & %d & %.0f ' % (nb, nq, b['estimated_rpc_ms'])
                  + '$' + BS + 'to$ %.0f & %.1f$' % (q['estimated_rpc_ms'], s) + BS + 'times$ ' + BS + BS + '\n')
    (OUT / 'jtag_rows.tex').write_text(''.join(jl), encoding='utf-8')
    N['RpcModels'] = str(len(e2e))
    N['RpcSpMin'], N['RpcSpMax'] = '%.1f' % min(sp), '%.1f' % max(sp)
    N['RpcBatchedMin'], N['RpcBatchedMax'] = str(min(nbs)), str(max(nbs))
    pipe = {(r_['rpc_counts']['pipeline']['load_rpcs'], r_['rpc_counts']['pipeline']['infer_rpcs']) for r_ in e2e}
    assert len(pipe) == 1
    pl, pi = next(iter(pipe))
    N['RpcPipeLoad'], N['RpcPipeInfer'], N['RpcPipe'] = str(pl), str(pi), str(pl + pi)
    N['RpcMsPer'] = grab(r'([\d.]+) ms/RPC', e2e[0]['rpc_latency_assumption'], 'e2e_timing.json')[1]
    anc = {r_['protocol']: r_ for r_ in jload(JT / 'ecg_1mhz_anchor.json')}
    N['AnchorTck'] = '%.0f' % (1e3 / anc['burst']['tck_ns'])
    N['AnchorBurstMs'] = '%.1f' % (anc['burst']['total_tck'] * anc['burst']['tck_ns'] / 1e6)
    N['AnchorWordMs'] = '%.1f' % (anc['wordwise']['total_tck'] * anc['wordwise']['tck_ns'] / 1e6)
    N['AnchorUpPct'] = '%.1f' % (100 * anc['burst']['phases']['model_upload']['tck'] / anc['burst']['total_tck'])
    N['AnchorRatio'] = '%.1f' % (anc['wordwise']['total_tck'] / anc['burst']['total_tck'])


# <<SECTION:demo>>
def sec_demo(N):
    s = jload(DEMO / 'summary.json')
    assert all(c['passed'] for c in s['checks'].values())
    N['DemoUnit'], N['DemoJtag'] = str(s['demo_unit_tests']), str(s['jtag_unit_tests'])
    N['DemoGolden'], N['DemoGoldenTotal'] = str(s['golden_complete_outputs_equal']), str(s['golden_frames'])
    assert s['golden_complete_outputs_equal'] == s['golden_frames']
    N['DemoZoo'] = str(s['existing_zoo_frames'])
    N['DemoChecks'] = str(len(s['checks']))
    N['DemoFpga'] = 'true' if s['fpga_validated'] else 'false'
    N['DemoNotTested'] = str(len(s['not_tested']))
    emo = jload(MODELS / 'emotion/training_result.json')['evaluation']
    face = jload(MODELS / 'face/training_result.json')['evaluation']
    N['EmoAcc'], N['EmoSamples'] = '%.2f' % (100 * emo['accuracy']), '{:,}'.format(emo['samples'])
    N['FaceP'], N['FaceR'], N['FaceF'] = ('%.1f' % (100 * face[k]) for k in ('precision', 'recall', 'f1'))
    N['FaceSamples'] = '{:,}'.format(face['samples'])
    demo_py = (ROOT / 'software/host/demo.py').read_text(encoding='utf-8')
    N['NumScenes'] = str(len(re.findall(r'^\s+"\w+": \(', demo_py, re.M)))
    tests = (ROOT / 'software/tests/test_demos.py').read_text(encoding='utf-8')
    fixes = ['test_browser_restart_waits_for_the_previous_upload_queue',
             'test_phone_follow_resumes_after_gap_and_ignores_partial_tail',
             'test_fpga_validation_requires_every_vector_from_every_demo']
    assert all('def ' + t + '(' in tests for t in fixes)
    N['DemoFixes'] = str(len(fixes))
    gap = grab(r'dt > ([\d.]+)', (ROOT / 'software/host/demos/signals.py').read_text(encoding='utf-8'), 'signals.py')
    N['HarGapMs'] = '%.0f' % (float(gap[1]) * 1e3)


def main():
    OUT.mkdir(exist_ok=True)
    N = {}
    for sec in (sec_asic, sec_lab5, sec_mmmc, sec_power, sec_rtl, sec_fpga, sec_rpc, sec_demo):
        sec(N)
    bad = [k for k in N if not re.fullmatch(r'[A-Za-z]+', k)]
    assert not bad, bad
    (OUT / 'numbers.tex').write_text('% generated by make_data.py -- do not edit\n' + ''.join(
        f'\\newcommand{{\\N{k}}}{{{v}}}\n' for k, v in N.items()), encoding='utf-8')
    for k, v in N.items():
        print(f'{k:22s} {v}')


if __name__ == '__main__':
    main()
