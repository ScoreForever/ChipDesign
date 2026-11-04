#!/usr/bin/env python3
"""Turn docs/validation/int4_v2 results into pgfplots tables (data/*.dat) and
number macros (data/numbers.tex) for the weekly slides.

Run from anywhere: python3 docs/slides/int4_v2/make_data.py
"""
from __future__ import annotations
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VAL = ROOT / 'docs/validation/int4_v2'
DASH = VAL / 'dashboard'
OUT = HERE / 'data'
ORDER = ['MNIST-L', 'Fashion-CNN', 'KWS-L', 'KWS-M', 'HAR-1D', 'ECG-1D', 'GTSRB-L']


def load(name):
    return json.loads((DASH / f'{name}.json').read_text())


def write(name, header, rows):
    lines = [' '.join(header)] + [' '.join(str(c) for c in r) for r in rows]
    (OUT / name).write_text('\n'.join(lines) + '\n')


def main():
    OUT.mkdir(exist_ok=True)
    macros = {}
    models = {m['model']: m for m in load('models')}

    write('acc.dat', ['model', 'float', 'int4'],
          [[m, f"{100 * models[m]['float_test']:.2f}", f"{100 * models[m]['int4_test']:.2f}"] for m in ORDER])
    low = min(models.values(), key=lambda m: m['int4_test'])
    macros['MinAcc'] = f"{100 * low['int4_test']:.2f}\\%"
    macros['MinAccModel'] = low['model']
    macros['NumModels'] = str(len(models))

    cats = ['MAC issue', 'Wait activation', 'Wait weight', 'Wait result slot', 'Kernel prepare', 'Element / other']
    bd = collections.defaultdict(dict)
    for r in load('breakdown'):
        bd[r['model']][r['category']] = r['cycles']
    rows = []
    for m in ORDER:
        tot = sum(bd[m].values())
        rows.append([m] + [f'{100 * bd[m][c] / tot:.1f}' for c in cats])
    write('breakdown.dat', ['model', 'mac', 'act', 'wt', 'slot', 'kprep', 'other'], rows)

    st = collections.defaultdict(dict)
    stage_names = []
    for r in load('stages'):
        st[r['model']][r['stage']] = r['cycles']
        if r['stage'] not in stage_names:
            stage_names.append(r['stage'])
    rows, cuts = [], []
    for m in ORDER:
        vals = [st[m].get(s) for s in stage_names]
        rows.append([m] + [f'{v / 1000:.1f}' if v else 'nan' for v in vals])
        if vals[0] and vals[-1]:
            cuts.append(1 - vals[-1] / vals[0])
    write('stages.dat', ['model', 's1', 's2', 's3'], rows)
    macros['CycleCutMin'] = f'{100 * min(cuts):.0f}'
    macros['CycleCutMax'] = f'{100 * max(cuts):.0f}'

    groups = {'Model upload': 'model', 'Input upload': 'input', 'Compute': 'compute', 'Output readout': 'readout'}
    e2e = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in load('e2e'):
        e2e[(r['model'], r['protocol'])][groups.get(r['phase'], 'control')] += r['ms']
    rows, speed, upload, compute = [], [], [], []
    for m in ORDER:
        b, w = e2e[(m, 'Burst')], e2e[(m, 'Per-word (ABI 3)')]
        tb, tw = sum(b.values()), sum(w.values())
        speed.append(tw / tb)
        upload.append(b['model'] / tb)
        compute.append(b['compute'] / tb)
        rows.append([m] + [f"{b[k]:.2f}" for k in ('model', 'input', 'compute', 'readout', 'control')] + [f'{tb:.1f}', f'{tw:.1f}'])
    write('e2e.dat', ['model', 'model_ms', 'input_ms', 'compute_ms', 'readout_ms', 'control_ms', 'burst_ms', 'word_ms'], rows)
    macros['SpeedMin'] = f'{min(speed):.1f}'
    macros['SpeedMax'] = f'{max(speed):.1f}'
    macros['UploadShareMin'] = f'{100 * min(upload):.0f}'
    macros['UploadShareMax'] = f'{100 * max(upload):.0f}'
    macros['ComputeShareMax'] = f'{100 * max(compute):.0f}'

    # Fusion ablation table (cycles in k, or the limit that is exceeded).
    fus = collections.defaultdict(dict)
    for r in load('fusion'):
        fus[r['model']][r['config']] = r
    cfgs = ['All fused', 'No requant fusion', 'No maxpool fusion', 'Nothing fused']
    limit_zh = {'16 descriptors': '>16 描述符', 'activation SRAM': '激活 SRAM 超限', 'model SRAM': '模型 SRAM 超限'}
    lines, fits_none = [], 0
    for m in ORDER:
        cells = []
        for c in cfgs:
            r = fus[m].get(c)
            if r is None:
                cells.append('--')
            elif r['fits']:
                cells.append(f"{r['cycles'] / 1000:.1f}k / {r['writes'] / 1000:.1f}k")
            else:
                cells.append(f"\\textcolor{{peking}}{{{limit_zh[r['limit']]}}}")
        if not fus[m]['Nothing fused']['fits']:
            fits_none += 1
        lines.append(f'{m} & ' + ' & '.join(cells) + r' \\')
    (OUT / 'fusion.tex').write_text('\n'.join(lines) + '\n')
    macros['UnfusedOverflow'] = str(fits_none)

    # Model table.
    lines = []
    for m in ORDER:
        r = models[m]
        lines.append(f"{m} & {r['dataset']} & {100 * r['float_test']:.2f}\\% & \\textbf{{{100 * r['int4_test']:.2f}\\%}} & "
                     f"{r['model_bytes']:,} & {r['activation_peak']:,} & {r['descriptors']} & {r['cycles_c8'] / 1000:.1f}k & {r['latency_us'] / 1000:.2f} \\\\")
    (OUT / 'models.tex').write_text('\n'.join(lines) + '\n')
    # Same table with the network structure (from software/zoo/models.py specs).
    arch = {'MNIST-L': 'Conv16 · DS32 · Res32 · DS64', 'Fashion-CNN': 'Conv16 · Conv32 · Conv32',
            'KWS-L': 'Conv48 (7$\\times$3, /2) · 3$\\times$DS48', 'KWS-M': 'Conv40 (7$\\times$3, /2) · 4$\\times$DS40',
            'HAR-1D': 'Conv32 (1$\\times$5) · DS32 · DS64', 'ECG-1D': 'Conv16 (1$\\times$7) · Res16 · DS32 · Res32 · DS32',
            'GTSRB-L': 'Conv24 · DS48 · Res48 · DS56'}
    lines = []
    for m in ORDER:
        r = models[m]
        lines.append(f"{m} & {r['dataset']} & {arch[m]} & {100 * r['float_test']:.2f} & \\textbf{{{100 * r['int4_test']:.2f}}} & "
                     f"{r['model_bytes']:,} & {r['activation_peak']:,} & {r['descriptors']} & {r['latency_us'] / 1000:.2f} \\\\")
    (OUT / 'models_arch.tex').write_text('\n'.join(lines) + '\n')

    # Final area composition (Genus, freeze RTL).
    area = json.loads((VAL / 'area_final.json').read_text())
    core = 'npu_program_core'
    fl = collections.Counter()
    groups = [('psum 累加器', ('psum',)), ('结果槽', ('results',)), ('性能计数器', ('perf_count',)),
              ('参数与读缓存', ('cache_data', 'cache_tag', 'bias_cache', 'mult_cache', 'shift_cache')),
              ('描述符与区域表', ('d', 'known_base', 'known_size')),
              ('写队列', ('write_data', 'write_addr', 'write_mask', 'q_data', 'q_addr', 'q_mask'))]
    for key, v in area['flops_by_register'].items():
        mod, reg = key.split(':', 1)
        name = 'JTAG / mailbox / 控制器' if not mod.startswith(core) and not mod.startswith('requant') and not mod.startswith('npu_activation') else None
        if mod.startswith(core):
            name = next((g for g, regs in groups if reg in regs), '其它 NPU 寄存器')
        elif mod.startswith('requant'):
            name = 'Requant lane'
        elif mod.startswith('npu_activation'):
            name = '激活存储接口'
        fl[name] += v['area_um2']
    comb = collections.Counter()
    for mod, a in area['comb_by_module'].items():
        comb['NPU 组合逻辑（MAC 阵列、地址、控制）' if mod.startswith(core) else 'Requant lane' if mod.startswith('requant')
             else '激活存储接口' if mod.startswith('npu_activation') else 'JTAG / mailbox / 控制器'] += a
    parts = collections.Counter()
    for k, v in fl.items():
        parts[k if k in ('Requant lane', '激活存储接口', 'JTAG / mailbox / 控制器') else k + '（触发器）'] += v
    for k, v in comb.items():
        parts[k] += v
    parts['SRAM 宏 ×2'] = area['macro_area_um2']
    rows = sorted(parts.items(), key=lambda kv: -kv[1])
    (OUT / 'area.tex').write_text('\\def\\AreaList{' + ','.join(f"{{{k}}}/{v / 1e6:.3f}" for k, v in rows) + '}\n')
    std = (area['flop_area_um2'] + area['comb_area_um2']) / 1e6
    macros['StdArea'] = f'{std:.2f}'
    macros['FlopArea'] = f"{area['flop_area_um2'] / 1e6:.2f}"
    macros['SramArea'] = f"{area['macro_area_um2'] / 1e6:.2f}"
    macros['TotalArea'] = f"{std + area['macro_area_um2'] / 1e6:.2f}"
    macros['PsumShare'] = f"{100 * fl['psum 累加器'] / (std * 1e6):.0f}"

    # Layout and signoff.
    lay = json.loads((VAL / 'layout_final.json').read_text())
    sel = next(c for c in lay['candidates'] if c['name'] == lay['selected'])
    macros['DieSide'] = f"{sel['die_side_um']:.0f}"
    macros['DieArea'] = f"{sel['die_area_mm2']:.2f}"
    macros['PtSetup'] = f"{sel['pt_setup_wns_ns']:+.3f}"
    macros['PtHold'] = f"{sel['pt_hold_wns_ns']:+.3f}"
    macros['HoldTarget'] = f"{sel['hold_target']:.2f}"
    macros['WireM'] = f"{sel['wire_length_um'] / 1e6:.2f}"
    macros['Instances'] = f"{sel['instances']:,}"
    names = {'ws_u70': 'west\\_stack', 'mx_u70': 'south\\_row\\_mx', 'r90_u70': 'west\\_stack\\_r90',
             'r90_u75': 'west\\_stack\\_r90', 'r90_u70_h15': 'west\\_stack\\_r90'}
    lines = []
    for c in lay['candidates']:
        f = lambda v: '--' if v is None else (f'\\textcolor{{peking}}{{{v:+.3f}}}' if v < 0 else f'{v:+.3f}')
        mark = '\\textbf{选定}' if c['name'] == lay['selected'] else ('$\\checkmark$' if c['eligible'] else '$\\times$')
        lines.append(f"{names[c['name']]} & {100 * c['utilization']:.0f}\\% & {c['hold_target']:.2f} & {c['die_side_um']:.0f} & "
                     f"{f(c['setup_wns_ns'])} & {f(c['hold_wns_ns'])} & {f(c.get('pt_setup_wns_ns'))} & {f(c.get('pt_hold_wns_ns'))} & {mark} \\\\")
    (OUT / 'layout.tex').write_text('\n'.join(lines) + '\n')
    so = json.loads((VAL / 'signoff.json').read_text())
    import re
    num = lambda k: re.findall(r'[-+]?\d+(?:\.\d+)?', so['checks'][k]['detail'])
    lvs, sta, sdf = num('Calibre LVS'), num('PrimeTime STA'), num('Xcelium SDF gate-level')
    zh = {'Calibre DRC': '无阻塞违例；报告项均为 IO 单元内的 ESD/闩锁指导规则',
          'Calibre LVS': f'网表一致；{lvs[0]} 处 IO 电阻属性偏差（最大 {lvs[1]}\\%），已接受',
          'PrimeTime STA': f'SPEF 反标：setup {sta[0]} ns，hold {sta[1]} ns',
          'Formality LEC': '布线后网表与综合网表等价',
          'Xcelium SDF gate-level': f'门级 + SDF 跑通 {sdf[0]} 条 JTAG 命令（突发 IR，覆盖全部新算子）'}
    lines = []
    for k, v in so['checks'].items():
        ok = '\\textcolor{peking_darkgreen}{通过}' if v['pass'] else '\\textcolor{peking}{未过}'
        lines.append(f"{k} & {ok} & {zh[k]} \\\\")
    (OUT / 'signoff.tex').write_text('\n'.join(lines) + '\n')

    host = load('host')
    hb = [h for h in host if h['protocol'] == 'Burst']
    macros['HostBurstMax'] = f"{max(h['host_seconds'] for h in hb):.2f}"
    hw = [h for h in host if h['protocol'] != 'Burst']
    macros['HostWordMax'] = f"{max(h['host_seconds'] for h in hw):.1f}"

    (OUT / 'numbers.tex').write_text('% generated by make_data.py\n' + ''.join(
        f'\\newcommand{{\\N{k}}}{{{v}}}\n' for k, v in macros.items()))
    for k, v in macros.items():
        print(k, v)


if __name__ == '__main__':
    main()
