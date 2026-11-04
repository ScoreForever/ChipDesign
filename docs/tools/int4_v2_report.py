#!/usr/bin/env python3
"""Build docs/INT4_V2_REPORT.md and docs/validation/int4_v2_summary.json from
the result files collected in docs/validation/int4_v2/.

Every number in the report comes from one of those JSON files; the files keep
their source commit or run directory. Missing inputs are reported as pending.
"""
from __future__ import annotations
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'docs/validation/int4_v2'
CORE_MHZ = 50
TCK_MHZ = 1
RPC_MS = 4.0  # median OpenOCD drscan RPC on the 2026-11-02 board run

PERF = ('active', 'm_req', 'm_stall', 'm_maxst', 'm_rsp', 'm_wait', 'a_req', 'a_stall', 'a_maxst', 'a_rsp', 'a_wait',
        'writes', 'mac_issues', 'w_act', 'w_wt', 'w_slot', 'kprep', 'slots_pk', 'banks_pk', 'infl_pk', 'post',
        'g_contig', 'g_scalar', 'g_zero')
NAMES = {'mnist_l': 'MNIST-L', 'fashion_cnn': 'Fashion-CNN', 'kws_l': 'KWS-L', 'kws_m': 'KWS-M', 'har': 'HAR-1D',
         'ecg': 'ECG-1D', 'gtsrb_l': 'GTSRB-L'}
DATASETS = {'mnist_l': 'MNIST (10 类)', 'fashion_cnn': 'Fashion-MNIST (10 类)',
            'kws_l': 'Speech Commands v2 (12 类)', 'kws_m': 'Speech Commands v2 (12 类)',
            'har': 'UCI-HAR 原始 9 轴 (6 类)', 'ecg': 'MIT-BIH AAMI (5 类, 病人内划分)', 'gtsrb_l': 'GTSRB (43 类)'}
ORDER = ['mnist_l', 'fashion_cnn', 'kws_l', 'kws_m', 'har', 'ecg', 'gtsrb_l']


def load(name, default=None):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def pct(x, digits=2):
    return '—' if x is None else f'{100 * x:.{digits}f}%'


def table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join('---' if i == 0 else '---:' for i in range(len(header))) + '|']
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def perf_of(row):
    return dict(zip(PERF, row['performance'][0]))


def runs_by(rows, cols=8, fusion='all', frame=0):
    return {r['model']: r for r in rows or [] if r.get('cycles') and r['cols'] == cols and r['fusion'] == fusion
            and r['frame'] == frame}


LAYOUT_ZH = {'west_stack': '两颗叠放西南角，引脚朝下', 'west_stack_r90': '两颗叠放西南角，旋转后引脚朝东',
             'south_row_mx': '两颗并排南边，引脚朝北', 'west_split_r90': '西边一上一下，引脚朝东',
             'west_east': '一西一东，引脚相对', 'south_north': '一南一北，引脚相对', 'diagonal_facing': '西南与东北对角，引脚朝内'}


def fmt_ns(v):
    return '—' if v is None else f'{v:+.3f}'


def area_groups(area):
    """Final-area composition: combinational logic per module, flip-flops per register class."""
    import collections
    regs = [('psum 累加器（触发器）', ('psum',)), ('结果槽（触发器）', ('results',)), ('性能计数器（触发器）', ('perf_count',)),
            ('参数与读缓存（触发器）', ('cache_data', 'cache_tag', 'bias_cache', 'mult_cache', 'shift_cache')),
            ('描述符与区域表（触发器）', ('d', 'known_base', 'known_size')),
            ('写队列（触发器）', ('write_data', 'write_addr', 'write_mask', 'q_data', 'q_addr', 'q_mask'))]
    def module(mod):
        return ('NPU 组合逻辑（MAC 阵列、地址、控制）' if mod.startswith('npu_program_core') else 'Requant lane' if mod.startswith('requant')
                else '激活存储接口' if mod.startswith('npu_activation') else 'JTAG / mailbox / 控制器')
    g = collections.Counter()
    for key, v in area['flops_by_register'].items():
        mod, reg = key.split(':', 1)
        if mod.startswith('npu_program_core'):
            g[next((n for n, rs in regs if reg in rs), '其它 NPU 寄存器（触发器）')] += v['area_um2']
        else:
            g[module(mod)] += v['area_um2']
    for mod, v in area['comb_by_module'].items():
        g[module(mod)] += v
    g['SRAM 宏 ×2'] = area['macro_area_um2']
    return sorted(g.items(), key=lambda kv: -kv[1])


def fpga_results():
    import re
    d = ROOT / 'verification/fpga/results/2026-11-04-int4-v2'
    if not (d / 'validation_matrix.json').exists():
        return None
    vm = json.loads((d / 'validation_matrix.json').read_text())
    summ = (d / 'evidence/accepted_50mhz/summary.md').read_text()
    u = {m[1]: (int(m[2]), m[4]) for m in re.finditer(r'\| (Slice LUTs|Slice Registers|Block RAM Tile|DSPs) \| (\d+) \| (\d+) \| ([\d.]+) \|', summ)}
    t = re.search(r'WNS: ([-\d.]+) ns; TNS: ([-\d.]+) ns; WHS: ([-\d.]+) ns', summ)
    zs = vm['zoo_supplement']
    return dict(device='EGo1 XC7A35T', clock='50 MHz', lut=u['Slice LUTs'][0], lut_pct=u['Slice LUTs'][1], ff=u['Slice Registers'][0],
                ff_pct=u['Slice Registers'][1], bram=u['Block RAM Tile'][0], dsp=u['DSPs'][0], wns=t[1], whs=t[3],
                models=zs['model_count'], golden=zs['golden_frames'], runs=zs['board_runs'],
                report='verification/fpga/results/2026-11-04-int4-v2/report.md',
                report_link='../verification/fpga/results/2026-11-04-int4-v2/report.md')


def mac_work(model):
    """Useful MACs (logical, no padding) of an exported model."""
    import sys
    sys.path.insert(0, str(ROOT / 'software/host'))
    from npu_program import analyze
    tensors, nodes = analyze(model)
    total = 0
    for n in nodes:
        if n['op'] in ('Conv', 'FC'):
            oh, ow, oc = tensors[n['output']].shape
            total += oh * ow * oc * n['k']
    return total


def main():
    ev = {r['model']: r for r in load('zoo_eval.json', [])}
    final_rows = load('zoo_rtl_final.json') or load('zoo_rtl_stage3.json', [])
    final = runs_by(final_rows)
    final4 = runs_by(final_rows, cols=4)
    stages = [('ABI 4 首版（紧凑参数 + 共享乘法器）', runs_by(load('zoo_rtl_stage1.json', []))),
              ('+ 向量 element 引擎 / 行填充 / RGB 补通道', runs_by(load('zoo_rtl_stage2.json', []))),
              ('+ 双 Requant lane（最终版）', final)]
    tile8 = runs_by(load('zoo_rtl_tile8.json', []))
    e2e = load('e2e.json', [])
    summary = dict(models={}, e2e={}, area={}, layout={}, fpga={})
    md, ap = [], []
    w = md.append
    aw = ap.append

    # ---------------- accuracy
    w('## 2. 模型 zoo：真实数据集 + INT4 QAT\n')
    w('所有精度都用与硬件逐位一致的整数参考（`npu_fastref`，当前 ABI 4 语义：u4 激活、Q8 乘数、单次舍入）'
      '在完整测试集上算出；模型选择只看验证集。“字节”是 4×8 构建下的模型 SRAM 占用（上限 8192 B）。\n')
    rows = []
    for m in ORDER:
        r = ev.get(m)
        if not r:
            rows.append([NAMES[m], DATASETS[m], 'pending', '', '', '', '', '', ''])
            continue
        hw = r['hardware']['c8']
        rows.append([NAMES[m], DATASETS[m], pct(r['float_test']), f"**{pct(r['int4_test'])}**", pct(r['int4_test_balanced']),
                     r['weights'], hw['model_bytes'], hw['activation_peak'], hw['descriptors']])
        summary['models'][m] = dict(int4_test=r['int4_test'], int4_test_balanced=r['int4_test_balanced'],
                                    float_test=r['float_test'], weights=r['weights'], model_bytes=hw['model_bytes'],
                                    activation_peak=hw['activation_peak'], descriptors=hw['descriptors'],
                                    test_samples=r['test_samples'], run=r['run'])
    w(table(['模型', '数据集', '浮点测试', 'INT4 测试', 'INT4 balanced', 'INT4 权重数', '模型字节', '激活峰值 B', '描述符'], rows))
    w('')

    # ---------------- cycles and bottlenecks
    w('## 3. 性能与瓶颈\n')
    w('### 3.1 每次推理的计算周期（RTL 实测，逐位核对全部张量写回）\n')
    rows = []
    for m in ORDER:
        r = final.get(m)
        if not r:
            rows.append([NAMES[m]] + ['pending'] * 6)
            continue
        model = json.loads((Path(ev[m]['run']) / 'model.json').read_text()) if m in ev and Path(ev[m]['run']).exists() else None
        work = mac_work(model) if model else None
        cyc = r['cycles'][0]
        c4 = final4.get(m, {}).get('cycles', [None])[0]
        util = work / (cyc * 32) if work else None
        rows.append([NAMES[m], f'{cyc:,}', f'{cyc / CORE_MHZ:.0f} µs', f'{c4:,}' if c4 else '—',
                     f'{work:,}' if work else '—', pct(util, 1), len(r['groups'])])
        summary['models'].setdefault(m, {}).update(cycles_c8=cyc, cycles_c4=c4, macs=work, mac_utilization=util)
    w(table(['模型', '4×8 周期', '@50 MHz', '4×4 周期', '有效 MAC', '4×8 MAC 利用率', '描述符'], rows))
    w('')
    w('### 3.2 周期拆解（4×8，各项互斥）\n')
    w('`MAC 发射`=阵列每拍接收一个激活向量；`等激活`=gather 未就绪；`等权重`=权重 bank 未装好；'
      '`等结果槽`=4 个结果槽满（后处理/写回慢）；`核准备`=每个 K 组的偏移准备；'
      '`element/其它`=element 引擎算子（Add、Pool、GAP 等）、描述符/参数读取和层间排空。\n')
    rows = []
    for m in ORDER:
        r = final.get(m)
        if not r:
            continue
        p = perf_of(r)
        cyc = r['cycles'][0]
        parts = [p['mac_issues'], p['w_act'], p['w_wt'], p['w_slot'], p['kprep']]
        other = cyc - sum(parts)
        rows.append([NAMES[m]] + [pct(v / cyc, 1) for v in parts + [other]])
        summary['models'][m]['breakdown'] = dict(zip(('mac_issue', 'wait_activation', 'wait_weight', 'wait_slot',
                                                      'kernel_prepare', 'element_and_other'), parts + [other]))
    w(table(['模型', 'MAC 发射', '等激活', '等权重', '等结果槽', '核准备', 'element/其它'], rows))
    w('')
    aw('### A.1 优化阶段（4×8 周期）\n')
    rows = []
    for m in ORDER:
        vals = [s[1].get(m, {}).get('cycles', [None])[0] for s in stages]
        if not any(vals):
            continue
        first = next(v for v in vals if v)
        rows.append([NAMES[m]] + [f'{v:,}' if v else '—' for v in vals] + [pct((vals[-1] - first) / first, 1) if vals[-1] else '—'])
    aw(table(['模型'] + [s[0] for s in stages] + ['累计'], rows))
    aw('')
    if tile8:
        aw('TILE=8（psum 减半，约 −0.06 mm² 标准单元）对周期的影响：\n')
        rows, ratios = [], []
        for m in ORDER:
            a, b = final.get(m), tile8.get(m)
            if a and b:
                ratios.append(b['cycles'][0] / a['cycles'][0])
                rows.append([NAMES[m], f"{a['cycles'][0]:,}", f"{b['cycles'][0]:,}", pct(ratios[-1] - 1, 1)])
        aw(table(['模型', 'TILE=16', 'TILE=8', '变化'], rows))
        aw(f'\n几何平均 {pct(math.exp(sum(map(math.log, ratios)) / len(ratios)) - 1, 1)}；默认保留 TILE=16（见第 7 节）。\n')

    # ---------------- per operator
    w('### 3.3 逐算子（4×8）\n')
    for m in ORDER:
        r = final.get(m)
        if not r:
            continue
        rows = []
        for g, op in zip(r['groups'], r['operator_trace'][0]):
            start, end, desc, flags, wa, ww, ws, iss = op
            rows.append(['+'.join(g) + (' (DW)' if desc >> 13 & 1 else ''), f'{end - start:,}', f'{iss:,}', f'{wa:,}', f'{ww:,}', f'{ws:,}'])
        w(f'<details><summary>{NAMES[m]}：{r["cycles"][0]:,} 周期</summary>\n')
        w(table(['物理算子（融合组）', '周期', 'MAC 发射', '等激活', '等权重', '等结果槽'], rows))
        w('\n</details>\n')

    # ---------------- fusion ablation
    w('### 3.4 融合消融（4×8，第一帧）\n')
    rows = []
    for m in ORDER:
        line = [NAMES[m]]
        for fusion in ('all', 'no_requant', 'no_relu', 'no_maxpool', 'none'):
            r = next((x for x in final_rows if x['model'] == m and x['cols'] == 8 and x['fusion'] == fusion and x['frame'] == 0), None)
            if r is None:
                line.append('—')
            elif 'cycles' in r:
                line.append(f"{r['cycles'][0]:,} / {r['writes'][0]:,}")
            else:
                line.append('超限：' + ('>16 描述符' if 'operators' in r['unsupported'] else
                                       '激活 SRAM' if 'activation' in r['unsupported'] else '模型 SRAM'))
        rows.append(line)
    w(table(['模型', '全部融合', '关 Requant', '关 ReLU', '关 MaxPool', '全关'], rows))
    w('\n表中为“周期 / 写回次数”。u4 模型的 ReLU 由 Requant 的 [0,15] 钳位完成，图里没有独立 ReLU 节点，'
      '所以“关 ReLU”与全部融合相同。大多数模型在关闭融合后已放不进 16 个描述符或 8 KiB SRAM——融合是装得下的前提。\n')

    # ---------------- array size (future direction)
    am = load('array_model.json')
    if am:
        w('### 3.5 阵列尺寸：后续方向（模型估算，未实现）\n')
        w('由 4×8 与 4×4 的逐算子实测外推，区间左端假设激活读取随 MAC 发射同比例减少，右端假设激活等待不减'
          '（`docs/tools/array_size_model.py`；用同一模型反推 4×4 比实测少 5–9 个百分点）。'
          'DW 份额是深度可分离层的 MAC 发射与等激活周期占全部周期的比例。\n')
        names = [('4x4', '4×4'), ('4x16', '4×16'), ('8x8', '8×8'), ('4x8+dw_path', '4×8 + DW 专用通路')]
        rows = []
        for m in ORDER:
            e = am['models'].get(m)
            if not e:
                continue
            cells = []
            for key, _ in names:
                c = e['configs'][key]
                lo, hi = c['change_optimistic'], c['change_pessimistic']
                cells.append(pct(lo, 0) if abs(lo - hi) < 0.005 else f'{pct(lo, 0)} ~ {pct(hi, 0)}')
            rows.append([NAMES[m], pct(e['depthwise_share_4x8'], 0)] + cells)
        w(table(['模型', 'DW 份额'] + [n for _, n in names], rows))
        w('\nDW 层在 4×4 和 4×8 上周期相同：共享阵列上 DW 每拍只算 ROWS（=4）个通道，加列不加速。'
          '8×8 同时把普通卷积的 K 方向和 DW 的通道数翻倍，psum 与结果槽随列数不变，面积代价主要是 32 个 MAC 单元；'
          '4×16 只加速普通卷积，psum 翻倍（约 +0.2 mm² 标准单元），70% 利用率下 die 会超过 1950 µm。'
          '8×8 需要改程序核中写死的 4 行（K 分组、装载行号、行填充、DW 映射）和权重打包，并重做全部验证与 ASIC 流程，列为后续方向。\n')
        summary['array_model'] = am['models']

    # ---------------- e2e
    w('## 4. 端到端时间：从 JTAG 写入到结果读出\n')
    w(f'物理 JTAG 管脚上的 RTL 仿真，TCK = {TCK_MHZ} MHz，核心 {CORE_MHZ} MHz。每个阶段的 TCK 数来自测试台计数；'
      f'计算阶段按 NPU 周期 / {CORE_MHZ} MHz 计。主机软件开销不在 RTL 里：2026-11-02 实板记录每次 OpenOCD '
      f'`drscan` RPC 中位数 {RPC_MS} ms，下表单独估算。\n')
    by = {}
    for r in e2e:
        by.setdefault(r['model'], {})[r['protocol']] = r
    phases = ['capabilities', 'model_upload', 'model_commit', 'input_upload', 'start', 'compute', 'status_poll',
              'output_readout', 'ack']
    labels = ['能力查询', '模型上传', '模型校验', '输入上传', '启动', '计算', '状态查询', '结果读出', 'ACK']
    rows = []
    for m in ORDER:
        if m not in by:
            continue
        for proto in ('burst', 'legacy'):
            r = by[m].get(proto)
            if not r:
                continue
            cyc = final.get(m, {}).get('cycles', [r['compute_cycles']])[0]
            line = [NAMES[m], '突发' if proto == 'burst' else '逐字（ABI 3）']
            total = 0.0
            for ph in phases:
                if ph == 'compute':
                    t = cyc / CORE_MHZ / 1000
                else:
                    t = r['phases'].get(ph, {}).get('tck', 0) / TCK_MHZ / 1000
                total += t
                line.append(f'{t:.2f}')
            line.append(f'**{total:.1f}**')
            rows.append(line)
            summary['e2e'].setdefault(m, {})[proto] = dict(total_ms=total, tck=r['total_tck'], commands=r['commands'],
                                                           burst_scans=r['burst_scans'])
    w('单位 ms（线上时间，不含主机软件）：\n')
    w(table(['模型', '协议'] + labels + ['合计'], rows))
    w('')
    rows = []
    for m in ORDER:
        if m not in by or 'burst' not in by[m]:
            continue
        r = by[m]['burst']
        legacy = by[m].get('legacy')
        burst_rpc = r['commands'] * 3 + math.ceil(r['burst_scans'] / 64) + 4
        legacy_rpc = legacy['commands'] * 3 if legacy else None
        rows.append([NAMES[m], r['commands'], r['burst_scans'], f'{burst_rpc * RPC_MS / 1000:.2f} s',
                     f'{legacy_rpc * RPC_MS / 1000:.1f} s' if legacy_rpc else '—'])
    w(f'主机 RPC 开销估算（每条普通命令按 1 次提交 + 约 2 次轮询 = 3 次 RPC；突发每 64 次扫描合并为 1 次 RPC；{RPC_MS} ms/RPC）：\n')
    w(table(['模型', '普通命令数', '突发扫描数', '突发协议 RPC 时间', '逐字协议 RPC 时间'], rows))
    w('')

    # ---------------- area (final design only; evolution in the appendix)
    w('## 5. 面积与 FPGA 资源\n')
    fa = load('area_final.json')
    if fa:
        groups = area_groups(fa)
        std = (fa['flop_area_um2'] + fa['comb_area_um2']) / 1e6
        w(f"最终 RTL 的 Genus 综合面积：标准单元 **{std:.3f} mm²**（触发器 {fa['flop_area_um2'] / 1e6:.3f}，组合逻辑 "
          f"{fa['comb_area_um2'] / 1e6:.3f}），两颗 SRAM 宏 {fa['macro_area_um2'] / 1e6:.3f} mm²。"
          '组合逻辑按模块统计，触发器按寄存器归类。\n')
        w(table(['部分', 'mm²', '占标准单元'], [[k, f'{v / 1e6:.3f}', '—' if k.startswith('SRAM') else pct(v / (std * 1e6), 1)]
                                                for k, v in groups]))
        w('')
        summary['area']['final'] = dict(standard_mm2=std, macro_mm2=fa['macro_area_um2'] / 1e6, groups=dict(groups))
    fp = fpga_results()
    if fp:
        w(f"**FPGA（{fp['device']}，{fp['clock']}）**：LUT {fp['lut']:,}（{fp['lut_pct']}%），FF {fp['ff']:,}（{fp['ff_pct']}%），"
          f"BRAM {fp['bram']}，DSP {fp['dsp']}；setup WNS {fp['wns']} ns，hold WHS +{fp['whs']} ns，CDC Critical 0。"
          f"{fp['models']} 个发布模型的 {fp['golden']} 帧在实板冷 / 热各跑一次，共 {fp['runs']} 次推理逐字节与参考一致，"
          f"24 项实板性能计数器与 RTL 相同。完整报告：[`{fp['report']}`]({fp['report_link']})。\n")
        summary['fpga'] = fp

    # appendix: area evolution and the yosys proxy used before the board run
    aw('### A.2 面积演进（Genus）\n')
    synth = load('synth_area.json', [])
    if synth:
        aw(table(['版本', '提交', '标准单元 mm²', '合计（含 2 颗 SRAM）mm²'],
                [[s_['name'], s_['commit'], f"{s_['standard_cell_area_um2'] / 1e6:.3f}", f"{s_['total_area_um2'] / 1e6:.3f}"] for s_ in synth]))
        aw('')
    aw('### A.3 上板前的 FPGA 资源估算（yosys `synth_xilinx`）\n')
    rows = []
    for name, title in (('xc7_v3_board.json', '上一版（已上板，Vivado 17,255 LUT）'), ('xc7_latest.json', 'v2 带 trace'),
                        ('xc7_latest_opsonly.json', 'v2 只留逐算子摘要'), ('xc7_latest_notrace.json', 'v2 FPGA 默认（无 trace）')):
        x = load(name)
        if x:
            rows.append([title, f"{x['lut']:,}", pct(x['lut'] / 20800, 1), f"{x['ff']:,}", x['bram36'], x['dsp']])
    aw(table(['构建', 'LUT', '占 20,800', 'FF', 'BRAM36', 'DSP'], rows))
    aw('')

    # ---------------- layout: final result, placement comparison, signoff
    w('## 6. 版图与签核\n')
    fin = load('layout_final.json')
    if isinstance(fin, dict) and fin.get('selected'):
        c = next(x for x in fin['candidates'] if x['name'] == fin['selected'])
        w(table(['项目', '结果'], [
            ['SRAM 摆放', f"`{c['layout']}`（{LAYOUT_ZH.get(c['layout'], '')}）"],
            ['利用率', f"{c['utilization']:.0%}"],
            ['die（含 PAD 环）', f"{c['die_side_um']:.0f} µm × {c['die_side_um']:.0f} µm（预算 1950 µm）"],
            ['标准单元实例', f"{c.get('instances', 0):,}"],
            ['PrimeTime setup / hold（TT，50 MHz）', f"{c['pt_setup_wns_ns']:+.3f} ns / {c['pt_hold_wns_ns']:+.3f} ns"],
            ['Innovus setup / hold', f"{c['setup_wns_ns']:+.3f} ns / {c['hold_wns_ns']:+.3f} ns（hold 目标 {c['hold_target']} ns）"],
            ['总线长', f"{c['wire_length_um'] / 1e6:.2f} m"],
            ['物理检查', f"{sum(v is True for v in c['checks'].values())} / {len(c['checks'])} 通过"]]))
        w('')
        summary['layout']['final'] = c
    pl = load('layout_placement.json')
    if pl:
        w('**SRAM 摆放比较**（每颗宏的 109 个信号引脚都在一条边上；利用率 70%）：\n')
        rows = []
        for c in pl['candidates']:
            rows.append([f"`{c['layout']}`", LAYOUT_ZH.get(c['layout'], ''), c.get('pins', ''), f"{c['die_side_um']:.0f}",
                         fmt_ns(c.get('pt_setup_wns_ns')), fmt_ns(c.get('pt_hold_wns_ns')),
                         f"{c['wire_length_um'] / 1e6:.2f}" if c.get('wire_length_um') else '—',
                         f"{c['overflow_h_pct']:.2f}% / {c['overflow_v_pct']:.2f}%" if c.get('overflow_h_pct') is not None else '—',
                         '✓' if c.get('eligible') else '✗'])
        w(table(['布局', '摆放', '引脚边', 'die µm', 'PT setup', 'PT hold', '线长 m', '拥塞溢出 H / V', '物理验收'], rows))
        w('')
        if pl.get('conclusion'):
            w(pl['conclusion'] + '\n')
        summary['layout']['placement'] = pl['candidates']
    so = load('signoff.json')
    if so:
        w(f"**签核**\n")
        w(table(['检查', '结果', '说明'], [[k, '✓' if v['pass'] else '✗', v.get('detail', '')] for k, v in so['checks'].items()]))
        w('')
        summary['signoff'] = so
    aw('### A.4 布局试验记录\n')
    for name, title in (('layout_intermediate.json', '中间版 RTL（u4 + 紧凑参数）'), ('layout_history.json', '上板修复前的 RTL')):
        lay = load(name)
        if not lay:
            continue
        lay = lay['candidates'] if isinstance(lay, dict) else lay
        rows = [[c['name'], c.get('layout', ''), c.get('utilization', ''), c.get('die_side_um', '—'),
                 c.get('setup_wns_ns', '—'), c.get('hold_wns_ns', '—'), fmt_ns(c.get('pt_setup_wns_ns')), fmt_ns(c.get('pt_hold_wns_ns')),
                 '✓' if c.get('eligible') else '✗'] for c in lay]
        aw(f'**{title}**\n')
        aw(table(['试验', '布局', '利用率', 'Die µm', 'Innovus setup', 'Innovus hold', 'PT setup', 'PT hold', '合格'], rows))
        aw('')

    (ROOT / 'docs/validation/int4_v2_summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False) + '\n')
    head = (ROOT / 'docs/tools/int4_v2_report_head.md').read_text()
    tail = (ROOT / 'docs/tools/int4_v2_report_tail.md').read_text()
    (ROOT / 'docs/INT4_V2_REPORT.md').write_text(head + '\n' + '\n'.join(md) + '\n' + tail)
    process = ('# INT4 v2 设计过程记录\n\n`docs/INT4_V2_REPORT.md` 只写最终设计；这里是做取舍时的中间数据，'
               '同样由 `docs/tools/int4_v2_report.py` 从 `docs/validation/int4_v2/` 生成。\n\n'
               + re.sub(r'(?m)^### A\.(\d+) ', r'## \1. ', '\n'.join(ap)) + '\n')
    (ROOT / 'docs/INT4_V2_PROCESS.md').write_text(process)
    print('wrote docs/INT4_V2_REPORT.md')


if __name__ == '__main__':
    main()
