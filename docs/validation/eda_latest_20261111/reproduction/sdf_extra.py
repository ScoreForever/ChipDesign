"""Exercise FC and fused/unfused CNNs using the accepted SDF snapshot."""
from pathlib import Path
import json,re,sys,os
import shutil
from concurrent.futures import ThreadPoolExecutor
RUN=Path(os.environ['EDA_RUN'])
SRC=RUN/'src'
sys.path.insert(0,str(SRC/'hardware/asic/optimize'))
sys.path.insert(0,str(SRC/'hardware/npu/scripts'))
from flow import launch,save,digest,libraries
from postlayout import accept_time0_pad_conflicts,classify,ifnone_arcs,sram_period_ok
from run_program_regression import fixtures,Recorder,encoded
from npu_program import compile_model,reference,ABI
from nocpu_jtag import Npu

C=Path((RUN/'selected_candidate.path').read_text().strip())
P=C/'sdf'
parent=json.loads((P/'result.json').read_text())
if not parent.get('passed'):raise RuntimeError('original annotated SDF run must pass first')
for path,sha in parent['sources_sha256'].items():
    if digest(path)!=sha:raise RuntimeError('original SDF input changed: '+path)
libs=libraries()
models='\n'.join(Path(libs[k]).read_text(errors='replace') for k in ('std_verilog','io_verilog','macro_verilog'))
_,model,frame=next(x for x in fixtures() if x[0]=='cnn')
def run_case(fuse):
    out=C/('sdf_cnn_f'+str(int(fuse)))
    if out.exists():raise RuntimeError('preserve old run: '+str(out))
    out.mkdir()
    pkg=compile_model(model,cols=8,fuse=fuse)
    values=reference(model,frame)[model['nodes'][-1]['output']]
    expect=encoded(values,pkg.manifest['output_dtype'])
    rec=Recorder(pkg,expect);host=Npu(rec);host.load_model(pkg)
    for _ in range(2):assert host.infer(encoded(frame,model['input'].get('dtype','i4')))==expect
    rec.rows+=['09 0 0 0 0 0',f'01 0 0 0 ffffffff {ABI:08x}']
    (out/'commands.txt').write_text('\n'.join(rec.rows)+'\n')
    ops=[int(row.split()[0],16) for row in rec.rows]
    expected_commands=sum(op not in (0x80,0x81,0xf0,0xf1,0xf2) for op in ops)
    expected_bursts=sum(op in (0x80,0x81) for op in ops)
    inputs={str(p):digest(p) for p in (out/'commands.txt',P/'routed.sdf',C/'outputs/simulation.v',C/'driver/tb_fullchip.sv',P/'result.json')}
    save(out/'inputs_sha256.json',inputs)
    # Each simulator gets its own copy of the accepted snapshot/cache and locks.
    # The original annotation and all gate/SDF/testbench input hashes stay fixed.
    cache=out/'xcelium.d'
    shutil.copytree(P/'xcelium.d',cache,ignore=shutil.ignore_patterns('*.lock','.xmlib.lock'))
    save(out/'snapshot_copy.json',{'source':str(P/'xcelium.d'),'parent_input_hashes':parent['sources_sha256']})
    state=launch(['xrun','-64bit','-R','-xmlibdirname',str(cache),'-access','+rwc','+neg_tchk',
        '+SDF='+str(P/'routed.sdf'),'+TRACE='+str(out/'commands.txt'),'-l','simulation.log'],out,'PNR_OPT_JTAG_PASS',timeout=14400)
    state,accepted=accept_time0_pad_conflicts(state)
    logs='\n'.join(p.read_text(errors='replace') for p in out.glob('*.log'))
    clean,diags=classify(logs,ifnone_arcs(models),sram_period_ok(Path(libs['macro_verilog']).read_text(errors='replace')))
    match=re.search(r'PNR_OPT_JTAG_PASS commands=(\d+) bursts=(\d+)',logs)
    counts_match=bool(match and int(match[1])==expected_commands and int(match[2])==expected_bursts)
    result={'passed':bool(state.get('status')=='complete' and clean and counts_match),
        'fixture':'cnn','fuse':fuse,'inferences':2,'expected_payload_bytes':len(expect),
        'logical_output_values':len(values),
        'commands':int(match[1]) if match else None,'bursts':int(match[2]) if match else None,
        'expected_commands':expected_commands,'expected_bursts':expected_bursts,'trace_counts_match':counts_match,
        'diagnostics':diags,'accepted_time0_input_pad_conflicts':accepted,
        'parent_annotated_snapshot':str(P),'annotation_validation':parent['passed'],
        'sources_sha256':inputs}
    save(out/'result.json',result)
    return result
with ThreadPoolExecutor(max_workers=2) as pool:
    results=list(pool.map(run_case,(True,False)))
save(C/'sdf_extra_summary.json',{'passed':all(r['passed'] for r in results),'results':results})
print(json.dumps({str(r['fuse']):r['passed'] for r in results}))
