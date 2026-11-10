from pathlib import Path
import csv
import hashlib
import json

root=Path.cwd()
out=root/'output/npu-bottlenecks/20261108'
base=json.loads((root/'build/bottlenecks_20261108_checked/summary.json').read_text())
limit=json.loads((root/'build/bottlenecks_limits_20261108_final/summary.json').read_text())
assert base['sources_sha256']==limit['sources_sha256']
for s,h in base['sources_sha256'].items():
    assert hashlib.sha256((root/s).read_bytes()).hexdigest()==h
board=json.loads((root/'output/npu-performance/20261107/board_final/board_checkout_bounds_margin/report.json').read_text())
board_rows={r['model']:r for r in board['models']}
rows=[r for r in base['tests'] if r['tile']==16 and r['case'].startswith('packaged_')]
for r in rows:
    name=r['case'].removeprefix('packaged_').split('_c8_')[0]
    assert r['cycles']==[x['performance']['active_cycles'] for x in board_rows[name]['runs']]
for label,s in [('workloads',base),('limits',limit)]:
    (out/(label+'_summary.json')).write_text(json.dumps(s,indent=2)+'\n')
allrows=base['tests']+limit['tests']
with (out/'metrics.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['case','tile','fused',*allrows[0]['metrics']]);w.writeheader()
    w.writerows(dict(case=r['case'],tile=r['tile'],fused=r['fused'],**r['metrics']) for r in allrows)
lines=['# ABI 5.0 / 8×8 NPU 极限瓶颈测试（2026-11-08）','',
'完成 52 组常规模型/融合/tile 对照和 40 组极限测试，共 92 例、184 次执行；全部物化张量写回与逻辑参考一致。七模型 tile16 的两次周期与 11 月 7 日最终实板一致。没有修改生产 RTL。当前为 INT4 数据路径，不能套用旧 INT8 架构图。','',
'## 七模型的当前限制','',
'下表是占总推理周期的比例。发射、激活等待、权重等待、结果槽等待、K准备五项互斥；剩余周期包含描述符、独立算子、流水排空等。接口级响应等待等其他计数存在重叠，不能再次相加。槽等待说明下游结果处理/写回阻塞，尚不能单独归因到量化乘法器。','',
'| 模型 | 周期 | MAC发射% | 激活等待% | 权重等待% | 槽等待% | K准备% | 逻辑阵列利用率% |',
'|---|---:|---:|---:|---:|---:|---:|---:|']
for r in rows:
    p=r['metrics'];name=r['case'].removeprefix('packaged_').split('_c8_')[0]
    values=[100*p[k]/p['active_cycles'] for k in ('mac_issues','mac_wait_activation_cycles','mac_wait_weight_cycles','mac_wait_slot_cycles','kernel_prepare_cycles')]
    lines.append(f'| {name} | {p["active_cycles"]} | '+' | '.join(f'{v:.1f}' for v in values)+f' | {p["useful_array_util_pct"]:.1f} |')
lines+=['','逻辑利用率 = 模型逻辑 MAC / (64 × 总周期)，包含 padding 零运算；发射占比仅描述阵列输入，不代表所有 PE 均有效。七模型只测发布 golden 首帧两次，不是整个数据集。','',
'GTSRB-L 的首个 Conv/Requant/MaxPool 单组占 75,697 / 128,283 周期（59.0%），其中激活等待 51,457 周期。其输入通道为4、3×3卷积，是当前最值得单独优化的非8通道取数路径。Fashion 的第二物理组占24,594周期，激活等待4,804、权重等待3,536；没有统一的单一瓶颈覆盖所有网络。','',
'## 极限与性能拐点','',
'| 实验 | 测量结果 | 指向的限制 |','|---|---|---|',
'| 连续输出，K=8，空间点16→64→256→1024 | 194→448→1504→5728周期；1024点槽等待77.8% | 后处理/写回的持续吞吐 |',
'| 相同计算量65,536 MAC，K8×1024点 vs K256×32点 | 5728 vs1947周期，约2.94倍差；逻辑利用率17.9% vs52.6% | 工作映射与输出密度，不是MAC数决定性能 |',
'| 单点、K=1024、OC=8 | 2408周期，权重等待1921（79.8%），逻辑利用率5.3% | 权重加载缺少空间复用 |',
'| OC=8→9→16，K8、16空间点 | 194→343→340周期 | 8路输出通道边界与尾块浪费 |',
'| K=256、32点、OC8 | 1947周期，发射52.6%，激活等待19.2% | 连续计算场景的供数吞吐 |',
'| 17/64/128个ReLU，每个17元素 | 2210/11328/30848周期；每组114、116…368 | 串行元数据查找随描述符深度增长 |',
'| tile16→8，七模型 | 增加20.9%–53.4%周期 | 权重与K组准备复用 |',
'| 大张量ReLU，8192→16384元素 | 32832→65600周期 | 独立逐元素路径的持续吞吐 |','',
'描述符链三组结果精确满足 C(N)=N²+113N，单组耗时114+2×index。该拟合只用于这一固定17元素ReLU链；它揭示深链的线性扫描代价，不应外推所有模型。','',
'融合对照的算术语义相同、逐张量检查通过。极限 post q0/q1 分别输出INT32/INT4，其周期对照包含数值格式和写回字节数变化，不能解读为单独关闭量化器的因果收益。tile=8 是独立仿真实验，没有布线或上板资格。','',
'## 下一轮设计顺序','',
'1. **分离激活地址/取数前端与返回握手**：先针对GTSRB首层复现，优化4通道3×3 gather与缓存命中/非连续访问。保持ABI5、8×8和输出不变；以该组51,457等待周期下降为首要指标，同时要求其他六模型无回退。','2. **提高持续结果排出能力**：针对K8×1024点的77.8%槽等待，拆测量化接收、写队列credit与物理SRAM写接受率。优先检查打包/合并与轮转空隙，再评估双lane至四lane的面积代价；仅增加结果槽可能只延迟饱和，不能保证持续吞吐。','3. **优化深描述符链查找**：对运行阶段缓存已验证元数据或建立直接索引，消除每组随index增长的扫描；独立保留启动预验证安全性。用128链30848周期作为验收基线，并测试地址重用/越界/复位。','4. **保持tile16，改进通道映射**：避免把9通道等尾块代价误判成MAC数量不足；对实际模型评估rowpad/K打包，先保持训练参数和ABI语义不变。单点长K层重点考虑跨帧批处理或权重重用，但跨帧批处理需要主机调度设计，不能直接假设当前ABI已支持。','',
'## 50 MHz时序与实现约束','',
'采用11月7日最终布线报告：WNS +0.344ns、WHS +0.058ns、LUT18,961/20,800（91.16%）。最差路径从issue_p寄存器到激活存储held_data，数据延迟19.634ns，其中布线14.180ns（72.2%）、24级逻辑。因此优先减少激活地址/握手组合扇出和布线距离。当前资源余量不足以先大幅扩张缓存或复制整个数据路径。','',
'本轮mywin SSH仍超时，未进行新的综合、布线、CDC或上板。新性能方案应先过同样的功能与极限测试，再执行50MHz布线时序和CDC门禁，最后上板。本报告没有把仿真tile变体当作已签核设计。','',
'## 复现与证据','',
'```sh','python3 hardware/npu/scripts/profile_bottlenecks.py --output build/profile_standard --jobs 4','python3 hardware/npu/scripts/profile_bottlenecks.py --limits-only --tiles 16 --output build/profile_limits --jobs 4','```','',
'输出目录必须是新目录。SYNTHESIS/FPGA_MEM、8列、波形关闭、算子摘要打开；两次作业计数均检查。summary包含源码SHA、两次24项计数和逐算子摘要。原始命令、写回golden和日志在build/bottlenecks_20261108_checked与build/bottlenecks_limits_20261108_final。归档workloads_summary.json、limits_summary.json、metrics.csv；本目录summarize.py复核当前源码、此前实板周期并生成本报告。','',
'初版分析器曾错误读取被融合消除的张量，已修为逻辑参考计算；首次极限用例越过单维255限制，已改为等元素二维形状。失败目录保留为bottlenecks_20261108和bottlenecks_limits_20261108，不用于上述结果。']
(out/'README.md').write_text('\n'.join(lines)+'\n')
print('Report verified:',len(allrows),'cases;',sum(len(r['cycles']) for r in allrows),'executions')
