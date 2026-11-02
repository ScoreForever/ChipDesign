from pathlib import Path
import json, math, shutil
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
DATA=ROOT/'docs/validation/int8_int4_postlayout_20261102/comparison.json'
D=json.loads(DATA.read_text(encoding='utf-8'))
V=[D['versions'][n] for n in ('INT8','INT4')]
pdfmetrics.registerFont(TTFont('CN','C:/Windows/Fonts/simhei.ttf'))
W,H=595.28,841.89; M=40; CW=W-2*M
BLUE=colors.HexColor('#2059ab'); INK=colors.HexColor('#202735'); GRAY=colors.HexColor('#526078'); LIGHT=colors.HexColor('#f2f5fa'); LINE=colors.HexColor('#c9d2e2')
P=ParagraphStyle('body',fontName='CN',fontSize=9,leading=14,textColor=INK,wordWrap='CJK')
SM=ParagraphStyle('small',parent=P,fontSize=7.8,leading=11)
OUT=HERE/'NPU_INT8_INT4_后仿报告_20261102.pdf'
c=canvas.Canvas(str(OUT),pagesize=(W,H));c.setTitle('无 CPU 双 SRAM NPU：INT8 / INT4 后仿报告');c.setAuthor('ChipDesign')
page=0
def para(s,x,y,w=CW,style=P):
 p=Paragraph(s,style);_,h=p.wrap(w,1000);p.drawOn(c,x,H-y-h);return y+h
def text(s,y=0,x=M,w=CW,style=P):return para(s,x,y,w,style)
def section(s,y):
 c.setFillColor(BLUE);c.setFont('CN',12);c.drawString(M,H-y-13,s);c.setStrokeColor(BLUE);c.line(M,H-y-20,W-M,H-y-20);return y+29
def table(rows,widths,y):
 cells=[[Paragraph(str(z),SM) for z in r] for r in rows]
 t=Table(cells,colWidths=widths,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT),('GRID',(0,0),(-1,-1),.4,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));_,h=t.wrap(CW,1000)
 if y+h>785:raise ValueError(('overflow',page,y,h))
 t.drawOn(c,M,H-y-h);return y+h+12
def start(title,sub):
 global page
 page+=1;c.setFillColor(INK);c.setFont('CN',18);c.drawString(M,H-52,title);text(sub,64,style=SM)
def end():
 c.setStrokeColor(LINE);c.line(M,32,W-M,32);c.setFillColor(GRAY);c.setFont('CN',7);c.drawString(M,21,'ChipDesign | 2026-11-02 | TT 1.8 V 25 C | 冻结源码实测结果');c.drawRightString(W-M,21,f'{page} / 7');c.showPage()
def note(s,y):
 h=Paragraph(s,SM).wrap(CW-16,1000)[1]+14;c.setFillColor(colors.HexColor('#fff6e8'));c.rect(M,H-y-h,CW,h,fill=1,stroke=0);para(s,M+8,y+7,CW-16,SM);return y+h+12
def cards(items,y):
 gap=8;w=(CW-3*gap)/4
 for i,(n,label) in enumerate(items):
  x=M+i*(w+gap);c.setStrokeColor(LINE);c.roundRect(x,H-y-66,w,66,3,stroke=1,fill=0);c.setFillColor(BLUE);c.setFont('CN',16);c.drawString(x+8,H-y-24,n);para(label,x+8,y+34,w-16,SM)
 return y+80
def bars(title,values,labels,y,maxv=None,unit='mm2'):
 y=section(title,y);mx=maxv or max(values)*1.15
 for i,(val,label) in enumerate(zip(values,labels)):
  yy=y+i*28;c.setFillColor(INK);c.setFont('CN',8);c.drawString(M,H-yy-11,label);x=M+112;length=val/mx*(CW-190);c.setFillColor(BLUE if i%2==0 else colors.HexColor('#4a9689'));c.rect(x,H-yy-16,length,14,fill=1,stroke=0);c.setFillColor(INK);c.drawString(x+length+6,H-yy-11,f'{val:.4f} {unit}')
 return y+len(values)*28+10
def figure(path,x,y,w,h):
 from reportlab.lib.utils import ImageReader
 im=ImageReader(str(path));iw,ih=im.getSize();scale=min(w/iw,h/ih);dw,dh=iw*scale,ih*scale;c.drawImage(im,x+(w-dw)/2,H-y-dh,width=dw,height=dh)

start('无 CPU 双 SRAM NPU：INT8 / INT4 后仿报告','测试快照：exp/no_cpu @ fea96a2；no_cpu_INT4 @ 02185d8 + 兼容修正 | 2026-10-31 至 2026-11-02')
y=section('结论',96)
y=text('两版均完成 Genus 映射综合、含 PAD 环的完整 PnR、9 项 Innovus 检查与有效 SDF 最大延迟门级后仿。INT4 减少逻辑面积，但两颗 SRAM 的物理面积不变。',y)+12
y=cards([('2 / 2','课程级实现与 SDF<br/>门级后仿通过'),('-15.41%','INT4 综合标准单元<br/>面积相对 INT8'),('+0.612 ns','INT4 setup 余量<br/>hold +0.020 ns'),('70 / 64','INT8 / INT4<br/>JTAG 命令全通过')],y)
y=table([['指标','INT8','INT4'],['综合标准单元面积','1.315184 mm2','1.112578 mm2'],['双 SRAM 宏面积 / 数量','0.802887 mm2 / 2','0.802887 mm2 / 2'],['综合单元 + 宏总面积','2.118071 mm2','1.915465 mm2'],['Die（含 PAD 环）','1940.40 um 见方 / 3.765152 mm2','1920.24 um 见方 / 3.687322 mm2'],['Core 面积','2.690387 mm2','2.624659 mm2'],['Setup / Hold WNS','+0.457 / +0.020 ns','+0.612 / +0.020 ns'],['Setup / Hold TNS','0 / 0 ns','0 / 0 ns'],['Innovus DRC / 天线','0 / 0','0 / 0'],['SDF 结果 / JTAG 命令','PASS / 70 条','PASS / 64 条']],[160,177,178],y)
y=note('范围：仅 TT / 1.8 V / 25°C，核心 50 MHz、JTAG 1 MHz。没有执行独立 Calibre、PrimeTime、Formality 或多角 STA、IR/EM；不构成流片签核。每版一个受控布局点，没有重新搜索或独立重复。',y)
y=section('源码归属与时效',y)
text('报告仅覆盖上述冻结快照。2026-11-02 发布时，远端 exp/no_cpu 已前进至 175484e，新增 ASIC mailbox CDC 约束与门禁；该后续提交未在本轮重跑，不能沿用本报告判为通过。旧流程的 9 项检查不包含该新增 CDC 审计。',y)
end()

start('实现基线与执行过程','同一 4×8 阵列、双 SRAM 和课程工具链；数值格式与模型 ABI 不同')
y=section('两版实现边界',96)
y=table([['项目','INT8','INT4'],['激活 / 权重','signed INT8，1 元素 / byte','signed INT4，2 元素 / byte，低 nibble 在先'],['累积 / 量化','26-bit K 组累积；Q31','18-bit K 组累积；Q15'],['Bias / 原始张量','INT32','INT32，保留原有溢出语义'],['模型 ABI','2.1 / 0x00020001','3.0 / 0x00030000'],['存储','模型 8 KiB + 激活 8 KiB','容量与宏不变；激活可容纳 16384 个 INT4'],['调试配置','ASIC 关闭波形与逐算子摘要','同左；全局性能计数器保留'],['算子','Conv、FC、MaxPool、ReLU、Requant','同左，保留合法融合、背压和恢复']],[120,197,198],y)
y=section('运行链路与前测',y)
for i,label in enumerate(['冻结源码与输入哈希','真实 SRAM 宏 RTL / JTAG 前测','Genus → Innovus：放置 / CTS / 布线 / 物理验收','导出网表 / 寄生 / SDF → Xcelium 门级后仿']):
 c.setFillColor(LIGHT);c.rect(M,H-y-30,CW,28,fill=1,stroke=0);text(f'{i+1}.  {label}',y+6);y+=38
y=text('工具：Genus 19.17-s144_1、Innovus 21.16-s078_1、Xcelium 21.09-s013。前测通过物理 TAP 装载非零数据、执行两次推理并检查 IRQ 与复位恢复。零延迟 RTL 前测关闭模型时序检查；正式 SDF 后仿保留严格检查。',y)+15
y=section('INT4 两项修正：均保留原失败证据',y)
y=table([['问题','修正与范围'],['Xcelium 拒绝声明顺序','npu_activation_store.sv 中 sram_q 移到首次引用前；没有改变表达式或计算逻辑。'],['复位 ABI 预期残留旧值','tb_fullchip.sv：0x20001 → 0x30000，仅修测试台；64 条命令与复位恢复重跑通过。']],[145,370],y)
text('INT8 无本轮 RTL 补丁。INT4 原始源码包与两项修正的文件哈希分别归档；未把测试台错误当作硬件功能失败，也未修改黄金输出来迎合结果。',y,style=SM)
end()

start('面积、资源与物理归属','面积口径分开报告：综合库面积、布线后活动单元、Core 与 Die')
y=bars('综合标准单元面积（不含 SRAM）',[v['synthesis']['standard_cell_area_um2']/1e6 for v in V],['INT8','INT4'],96)
y=table([['资源 / 几何','INT8','INT4','变化'],['综合标准单元 + 宏','2.118071 mm2','1.915465 mm2','-9.57%'],['布线后活动标准单元','1.335700 mm2','1.130344 mm2','-15.37%'],['活动标准单元实例','55,340','49,730','-10.14%'],['SRAM 宏','0.802887 mm2','0.802887 mm2','不变'],['Core 面积','2.690387 mm2','2.624659 mm2','-2.44%'],['Die 面积','3.765152 mm2','3.687322 mm2','-2.07%'],['CTS/修复活动面积净增','0.008785 mm2','0.007244 mm2','非全部 CTS 单元面积']],[160,122,122,111],y)
y=section('综合层级（库面积；父子不可重复相加）',y)
y=table([['层级','INT8 面积 / 单元数','INT4 面积 / 单元数'],['my_soc_top（含宏）','2.118071 mm2 / 53,358','1.915465 mm2 / 48,149'],['i_control_i_npu','1.073808 mm2 / 40,786','0.853819 mm2 / 33,881'],['NPU / i_requant','0.063749 mm2 / 2,924','0.040289 mm2 / 2,034'],['NPU / i_array','综合已展平，未独立报告','0.085244 mm2 / 3,570'],['i_control_i_activation','综合已展平，未独立报告','0.580960 mm2 / 10,818（含宏）']],[160,177,178],y)
y=note('标准单元面积下降 15.41%，不代表 Die 同比缩小：两个宏尺寸及 halo 不变，西侧叠放形成几何下限。两版不是面积搜索的全局最优解，也不证明 INT4 分类精度或端到端性能提升。',y)
text('来源：synthesis/area.json、area.rpt；candidate/result.json、final_cells.tsv。活动标准单元排除 IO、filler、tap、decap、天线单元；CTS/修复净增以 pre-CTS 与最终活动面积差计算。命名分组及无法精确归属的残差保存于 area_groups.json。',y,style=SM)
end()

start('时序、拥塞与物理检查','约束：20 ns core / 1000 ns JTAG，2 ns I/O，0.2 ns uncertainty；不降低目标频率')
y=bars('布线后 Setup WNS',[.457,.612],['INT8','INT4'],96,maxv=.8,unit='ns')
y=table([['指标','INT8','INT4'],['Setup WNS / TNS','+0.457 / 0 ns','+0.612 / 0 ns'],['Hold WNS / TNS','+0.020 / 0 ns','+0.020 / 0 ns'],['走线中心线总长（不含特殊网）','3.7252 m','3.1604 m（-15.16%）'],['全局估计布线 usage H / V','21.3% / 23.8%','17.9% / 21.5%'],['全局 overflow H / V','0.36% / 0.20%','0.21% / 0.13%'],['布局点','u70 / west_stack / congestion','u70 / west_stack / congestion'],['宏 halo / 工艺布线层','26 um / METAL2-6','同左']],[195,160,160],y)
y=section('9 项物理验收全部通过',y)
y=table([['检查','INT8','INT4'],['Innovus 几何 DRC / process antenna','0 / 0','0 / 0'],['电源 DRC / 电源连通','PASS / PASS','PASS / PASS'],['信号连通 / DRV','PASS / PASS','PASS / PASS'],['放置合法 / PAD 几何 / 端口','PASS / PASS / PASS','PASS / PASS / PASS']],[255,130,130],y)
y=note('全局 overflow 是拥塞估计，不能等同于详细布线 DRC；usage 也不能当作标准单元利用率。布局脚本目标 70% 与扣除宏及 halo 后的工具密度不是同一分母。IO 原生拼接电源轨的既有信息按脚本单独处理。',y)
y=section('保留的诊断与约束边界',y)
text('Innovus 各出现两次 IMPSP-9099 扫描链诊断：INT8 16.54%、INT4 15.62% 的触发器未定义完整扫描链。工具正常退出且实现检查通过，但 DFT 未完成。上述时序只适用于本轮冻结约束；不声明新增 scoped CDC 门禁已通过，也不构成 CDC 协议或亚稳态证明。',y)
end()

for key,title in [('int8','INT8'),('int4','INT4')]:
 start(title+' 最终版图（Innovus 实际渲染）','从各自最终数据库副本读取；不复用旧版本截图，不保存或修改验收数据库')
 for j,(fn,caption) in enumerate([('1_placement','器件摆放：双 SRAM、西侧叠放、PAD 环'),('2_power','电源：Core / SRAM 环及条带'),('3_signal_routing','信号布线：不含金属填充'),('5_all','全部图层：包含最终金属填充')]):
  x=M+(j%2)*(CW/2+5);yy=105+(j//2)*305;figure(HERE/'assets'/key/(fn+'.png'),x,yy,252,265);para(caption,x,yy+268,250,SM)
 text('图示来源：report_shots_'+key+'；final.enc.dat 只读副本。PAD 打线开口与电气 PAD 的设计叠接不按普通单元重叠判错。截图仅辅助观察，数值验收来自同一最终数据库导出的报告。',737,style=SM)
 end()

start('SDF 判定、交付文件与证据索引','有效延迟标注的代表性门级测试；覆盖范围与告警逐项说明')
y=section('门级后仿实际结果',96)
y=table([['检查','INT8','INT4'],['编译期 SDF 标注 / 最终判定','存在 / PASS','存在 / PASS'],['物理 JTAG 命令','70 / 70','64 / 64'],['两次推理、结果比对、IRQ、复位','通过','通过'],['意外 SDF 告警 / 时序违例','0 / 0','0 / 0'],['已分类 ifnone 记录数','65,850','41,736'],['已分类 SRAM negedge PERIOD 记录','6','6'],['第 0 时刻输入 PAD 初始化冲突','5','5']],[265,125,125],y)
y=text('使用 xrun -sdf_cmd_file，在 elaboration 阶段标注 MAXIMUM 延迟，保留 +define+NTC、+define+RECREM、+neg_tchk 与完整告警日志。SRAM CLK→Q 只去掉不匹配的沿限定，保留延迟值。只对精确列举的 6 个异步 CDC 采样触发器关闭时序检查，其余保持开启。ifnone 默认弧与宏模型仅检查上升沿周期的差异沿用旧流程分类。告警条数是完整日志审计的记录数，不是独立失效点数。',y)+12
y=note('这不是完整 74 / 82 项 RTL 回归的逐项门级重跑；有限的两次推理和恢复测试不能替代穷尽验证。单一 TT 角测试不证明所有电压、温度与频率条件均通过。',y)
y=section('导出、复现与证据',y)
y=table([['内容','可追溯位置'],['导出已生成且非空','simulation.v、physical.v、mapped.sdc、routed.def、routed.sdf.gz、routed.spef.gz、chip.lef、chip.gds.gz'],['服务器根目录','/home/s2400012724/chip-fullstack-2026/<br/>postlayout_latest_20261031'],['INT8 / INT4 最终候选','int8_run/candidates/course<br/>int4_run_abi3/candidates/course'],['仓库公开证据','docs/validation/int8_int4_postlayout_20261102/<br/>comparison.json、final_evidence.tar.gz、两项 patch.json'],['复核入口','result.json、checks.json、sdf/result.json、artifact_sha256.json；synthesis/area.rpt；final_cells.tsv']],[150,365],y)
text('未执行：独立 Calibre DRC/LVS/PEX、PrimeTime、Formality、多角 STA、IR/EM、DFT/ATPG、封装/ESD 与流片签核。私有 PDK、宏模型、完整网表和数据库留在服务器，公开归档仅含结果报告、哈希、面积坐标与补丁记录。',y,style=SM)
end();c.save()
shutil.copy2(OUT,ROOT/'output/pdf'/OUT.name)
print(OUT)
