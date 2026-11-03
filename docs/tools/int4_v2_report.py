#!/usr/bin/env python3
"""Build docs/INT4_V2_REPORT.md and docs/validation/int4_v2_summary.json from
the result files collected in docs/validation/int4_v2/.

Every number in the report comes from one of those JSON files; the files keep
their source commit or run directory. Missing inputs are reported as pending.
"""
from __future__ import annotations
import json
import math
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
              ('+ 双 Requant lane（冻结版）', final)]
    tile8 = runs_by(load('zoo_rtl_tile8.json', []))
    e2e = load('e2e.json', [])
    summary = dict(models={}, e2e={}, area={}, layout={}, fpga={})
    md = []
    w = md.append

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
    w('### 3.3 优化前后（4×8 周期）\n')
    rows = []
    for m in ORDER:
        vals = [s[1].get(m, {}).get('cycles', [None])[0] for s in stages]
        if not any(vals):
            continue
        first = next(v for v in vals if v)
        rows.append([NAMES[m]] + [f'{v:,}' if v else '—' for v in vals] + [pct((vals[-1] - first) / first, 1) if vals[-1] else '—'])
    w(table(['模型'] + [s[0] for s in stages] + ['累计'], rows))
    w('')
    if tile8:
        w('TILE=8（psum 减半，约 −0.06 mm² 标准单元）对周期的影响：\n')
        rows, ratios = [], []
        for m in ORDER:
            a, b = final.get(m), tile8.get(m)
            if a and b:
                ratios.append(b['cycles'][0] / a['cycles'][0])
                rows.append([NAMES[m], f"{a['cycles'][0]:,}", f"{b['cycles'][0]:,}", pct(ratios[-1] - 1, 1)])
        w(table(['模型', 'TILE=16', 'TILE=8', '变化'], rows))
        w(f'\n几何平均 {pct(math.exp(sum(map(math.log, ratios)) / len(ratios)) - 1, 1)}；默认保留 TILE=16（见第 7 节）。\n')

    # ---------------- per operator
    w('### 3.4 逐算子（4×8，冻结版）\n')
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
    w('### 3.5 融合消融（4×8，第一帧）\n')
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

    # ---------------- area
    w('## 5. 面积\n')
    synth = load('synth_area.json', [])
    if synth:
        rows = [[s['name'], s['commit'], f"{s['standard_cell_area_um2'] / 1e6:.3f}", f"{s['total_area_um2'] / 1e6:.3f}"] for s in synth]
        w(table(['版本', '提交', '标准单元 mm²', '合计（含 2 颗 SRAM）mm²'], rows))
        w('')
        summary['area']['synthesis'] = synth
    for name, title in (('area_abi4_pre_u4.json', 'ABI 4 首版'), ('area_opt1.json', '共享乘法器后'), ('area_final.json', '冻结版')):
        a = load(name)
        if not a:
            continue
        top = list(a['flops_by_register'].items())[:12]
        w(f'**{title}**：触发器 {a["flop_area_um2"] / 1e6:.3f} mm²，组合逻辑 {a["comb_area_um2"] / 1e6:.3f} mm²。最大的寄存器：\n')
        w(table(['寄存器', 'FF', 'µm²'], [[k.split(':')[-1], v['count'], f"{v['area_um2']:,.0f}"] for k, v in top]))
        w('')
        summary['area'][name] = dict(flops=a['flop_area_um2'], comb=a['comb_area_um2'])

    # ---------------- FPGA proxy
    w('### FPGA 余量（yosys `synth_xilinx` 估算，XC7A35T）\n')
    rows = []
    for name, title in (('xc7_v3_board.json', '上一版（已上板，Vivado 17,255 LUT）'), ('xc7_latest.json', 'v2 带 trace'),
                        ('xc7_latest_opsonly.json', 'v2 只留逐算子摘要'), ('xc7_latest_notrace.json', 'v2 FPGA 默认（无 trace）')):
        x = load(name)
        if x:
            rows.append([title, f"{x['lut']:,}", pct(x['lut'] / 20800, 1), f"{x['ff']:,}", x['bram36'], x['dsp']])
            summary['fpga'][name] = dict(lut=x['lut'], ff=x['ff'])
    w(table(['构建', 'LUT', '占 20,800', 'FF', 'BRAM36', 'DSP'], rows))
    w('')

    # ---------------- layout
    w('## 6. 布局：SRAM 摆放与端口朝向\n')
    for name, title in (('layout_intermediate.json', '中间版 RTL（u4 + 紧凑参数）'), ('layout_final.json', '冻结版 RTL')):
        lay = load(name)
        if not lay:
            continue
        if isinstance(lay, dict):
            lay = lay['candidates']
        rows = []
        for c in lay:
            chk = c.get('checks', {})
            rows.append([c['name'], c.get('layout', ''), c.get('utilization', ''), c.get('die_side_um', '—'),
                         c.get('setup_wns_ns', '—'), c.get('hold_wns_ns', '—'),
                         c.get('pt_setup_wns_ns', '—'), c.get('pt_hold_wns_ns', '—'),
                         f"{c['wire_length_um'] / 1e6:.2f}" if c.get('wire_length_um') else '—',
                         sum(1 for v in chk.values() if v is True), len(chk), '✓' if c.get('eligible') else '✗'])
        w(f'**{title}**\n')
        w(table(['试验', '布局', '利用率', 'Die µm', 'Innovus setup', 'Innovus hold', 'PT setup', 'PT hold', '线长 m', '通过检查', '检查数', '合格'], rows))
        w('')
        summary['layout'][name] = lay
    fin = load('layout_final.json')
    if isinstance(fin, dict) and fin.get('selected'):
        c = next(x for x in fin['candidates'] if x['name'] == fin['selected'])
        w(f"选定 **{c['name']}**（`{c['layout']}`，利用率 {c['utilization']:.0%}，Innovus hold 目标 {c['hold_target']} ns）："
          f"die {c['die_side_um']} µm，PrimeTime setup {c['pt_setup_wns_ns']:+.3f} ns / hold {c['pt_hold_wns_ns']:+.3f} ns。"
          '同为 u70，引脚朝向逻辑的两种布局都比 `west_stack` 小一档 die；默认 hold 目标下二者在 PrimeTime 上 hold 为负，'
          '因此用提高 hold 目标的重跑版本签核。\n')
    so = load('signoff.json')
    if so:
        w(f"**签核（{so['candidate']}）**\n")
        w(table(['检查', '结果', '说明'], [[k, '✓' if v['pass'] else '✗', v.get('detail', '')] for k, v in so['checks'].items()]))
        w('')
        summary['signoff'] = so

    (ROOT / 'docs/validation/int4_v2_summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False) + '\n')
    head = (ROOT / 'docs/tools/int4_v2_report_head.md').read_text()
    tail = (ROOT / 'docs/tools/int4_v2_report_tail.md').read_text()
    (ROOT / 'docs/INT4_V2_REPORT.md').write_text(head + '\n' + '\n'.join(md) + '\n' + tail)
    print('wrote docs/INT4_V2_REPORT.md')


if __name__ == '__main__':
    main()
