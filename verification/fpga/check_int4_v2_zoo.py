"""Cross-check all board counters with RTL and the published first-frame baselines."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'software/host'))
from nocpu_jtag import PERF_NAMES


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rtl',type=Path,required=True)
    p.add_argument('--production',type=Path,required=True)
    p.add_argument('--board',type=Path,required=True)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    rtl=json.loads((args.rtl/'summary.json').read_text())
    prod=json.loads((args.production/'summary.json').read_text())
    board=json.loads((args.board/'report.json').read_text())
    assert rtl['passed'] and prod['passed'] and board['passed']
    assert len(rtl['tests'])==len(prod['tests'])==137
    assert len(rtl['host_rejections'])==17
    index={(r['model'],r['frame']):r for r in rtl['tests']
           if r['cols']==8 and r['fusion']=='all' and not r['jtag']}
    assert len(index)==112 and len(board['models'])==7
    rows=[];frames=0
    for model in board['models']:
        assert len(model['frames'])==16
        correct=0;cycles=[];writes=[];mac=[]
        for frame in model['frames']:
            expected=(args.board/model['model']/f"frame{frame['index']:02d}"/'expected.bin').read_bytes()
            r=index[model['model'],frame['index']]
            assert len(frame['runs'])==2
            for repeat,run in enumerate(frame['runs']):
                actual=(args.board/model['model']/f"frame{frame['index']:02d}"/f'actual_{repeat}.bin').read_bytes()
                assert run['matches'] and actual==expected
                values=[run['performance'][key] for key in PERF_NAMES]
                assert values==r['performance'][repeat],(model['model'],frame['index'],repeat,
                    [(key,a,b) for key,a,b in zip(PERF_NAMES,values,r['performance'][repeat]) if a!=b])
            frames+=1;correct+=frame['runs'][0]['label_match']
            cycles.extend(run['performance']['active_cycles'] for run in frame['runs'])
            writes.extend(run['performance']['writes'] for run in frame['runs'])
            mac.extend(run['performance']['mac_issues'] for run in frame['runs'])
        rows.append(dict(model=model['model'],frames=16,board_runs=32,sampled_label_correct=correct,
                         cycles_min=min(cycles),cycles_max=max(cycles),
                         core_us_min=min(cycles)/50,core_us_max=max(cycles)/50,
                         writes=sorted(set(writes)),mac_issues=sorted(set(mac)),
                         model_bytes=model['model_bytes'],op_count=model['op_count'],activation_peak=model['activation_peak']))
    old=json.loads((ROOT/'docs/validation/int4_v2/zoo_rtl_final.json').read_text())
    old_index={(r['model'],r['cols'],r['fusion']):r for r in old if r['frame']==0 and 'cycles' in r}
    checked=[]
    for r in rtl['tests']:
        if r['frame'] or r['jtag']:continue
        baseline=old_index[r['model'],r['cols'],r['fusion']]
        for field in ('cycles','writes','performance','operator_trace'):
            assert baseline[field]==r[field],(r['case'],field)
        checked.append(r['case'])
    assert len(checked)==25
    provenance=json.loads((args.build/'provenance.json').read_text())
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in provenance['source_sha256']}
    assert hashes==provenance['source_sha256']
    result=dict(passed=True,model_commit=rtl['model_commit'],rtl_cases=137,production_cases=137,
                rtl_repetitions_per_case=2,host_capacity_rejections=rtl['host_rejections'],
                published_first_frame_comparisons=checked,
                board_frames=frames,board_runs=frames*2,all_24_board_rtl_counters_equal=True,
                bitstream_sha256=board['bitstream_sha256'],implemented_source_sha256=hashes,models=rows,
                limitation='16 published sampled frames per model; not a full-dataset accuracy reevaluation; physical tests remain pending.')
    assert frames==112
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
