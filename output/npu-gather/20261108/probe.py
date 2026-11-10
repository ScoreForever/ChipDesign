from pathlib import Path
import json,sys
root=Path.cwd();sys.path.insert(0,str(root/'hardware/npu/scripts'))
import run_program_regression as r
out=root/sys.argv[1];out.mkdir(parents=True,exist_ok=False)
sources=(root/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines()
image=out/'probe.vvp'
r.run(['iverilog','-g2012','-s','tb_npu_program','-DSYNTHESIS','-DCHIPDESIGN_FPGA_MEM','-DNPU_ENABLE_WAVE_TRACE=0','-o',image,*sources,'hardware/soc/sim/tb/tb_npu_program.sv'],out/'compile.log')
model=dict(input=dict(name='x',shape=[8,8,4],dtype='i4'),nodes=[
 dict(op='Conv',input='x',output='a',channels=8,kernel=3,pads=[1]*4,weights=[i%15-7 for i in range(36*8)],bias=[0]*8),
 dict(op='Requant',input='a',output='y',multiplier=[16384]*8,shift=[-5]*8)])
row=r.simulate(out,image,'ic4k3',model,[i%16-8 for i in range(256)],8,True,False,True)
(out/'summary.json').write_text(json.dumps(row,indent=2)+'\n')
print(row['cycles'], 'gather_wait',row['performance'][0][13],flush=True)
if len(sys.argv)>2:
 assert max(row['cycles'])<=int(sys.argv[2]),'activation gather performance budget exceeded'
