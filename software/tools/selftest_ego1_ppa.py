#!/usr/bin/env python3
"""Exercise activity conversion, report parsing and frequency-search control flow."""
import importlib.util
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import tempfile
from unittest.mock import patch

from kws_vcd_to_saif import convert, window
from summarize_ego1_ppa import power_report, saif_matching, write_summary, summarize_point

from ego1_saif_mapping import read_saif, write_saif, map_activity, axi_layout

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('ego1_flow',ROOT/'hardware/fpga/scripts/run_ego1_ppa.py')
flow=importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


def main():
    (ROOT/'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ppa_selftest_',dir=ROOT/'build') as tmp:
        out=Path(tmp)
        log=out/'simulation.log'
        log.write_text('SAIF_BEGIN time_ns=20 cycle=1\nSAIF_END time_ns=60 cycle=3 cycles=2\nKWS_PASS count=2\n')
        vcd=out/'trace.vcd'
        vcd.write_text('''$timescale 1 ns $end
$scope module kws_saif_tb $end
$scope module workload $end
$scope module dut $end
$var wire 1 ! a $end
$var wire 1 ! alias $end
$var wire 2 # bus [1:0] $end
$scope begin g_core $end
$scope module core $end
$scope begin gen[0] $end
$var wire 1 & unknown $end
$upscope $end
$upscope $end
$upscope $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b00 #
x&
#20
0!
b10 #
#30
1!
b11 #
1&
#40
0!
1!
b01 #
#60
0!
#80
1!
''')
        saif=out/'trace.saif'
        convert(vcd,log,saif)
        text=saif.read_text()
        assert '(INSTANCE fpga_top' in text and '(INSTANCE u_soc' in text
        assert '(a (T0 10000) (T1 30000) (TX 0) (TC 3)' in text, text
        assert '(alias (T0 10000) (T1 30000) (TX 0) (TC 3)' in text
        assert r'(bus\[1\] (T0 20000) (T1 20000) (TX 0) (TC 1)' in text
        assert r'(bus\[0\] (T0 10000) (T1 30000) (TX 0) (TC 1)' in text
        assert '(INSTANCE g_core.core' in text
        assert r'(gen\[0\].unknown (T0 0) (T1 30000) (TX 10000) (TC 0)' in text
        metadata=json.loads(saif.with_suffix('.json').read_text())
        assert metadata['cycles']==2 and metadata['bit_nets']==5
        scaled=out/'scaled.saif'
        flow.rescale_saif(saif,scaled,10,metadata)
        assert '(DURATION 20000)' in scaled.read_text()
        assert '(TC 3)' in scaled.read_text()
        # Missing or truncated window must be rejected, rather than silently counted.
        try:
            window('KWS_PASS count=2\n')
        except ValueError:
            pass
        else:
            raise AssertionError('Incomplete window accepted')
        report=out/'power.rpt'
        report.write_text('''| Total On-Chip Power (W) | 0.200 |
| Dynamic (W) | 0.150 |
| Device Static (W) | 0.050 |
| Confidence Level | Low |
2.2 By Hierarchy
----------------
| Name | Power (W) | Logic |
| fpga_top | 0.150 | 1 |
|   u_soc | 0.140 | 1 |
|     i_cpu | 0.050 | 1 |
|       child | 0.010 | 1 |
|     i_npu_subsystem | 0.060 | 1 |
|     i_axi_xbar | 0.010 | 1 |
|     i_mainmem | 0.015 | 1 |
|       i_mainmem | 0.015 | 1 |
|     i_bootram | 0.005 | 1 |
''')
        power=power_report(report)
        assert power['confidence']=='Low' and power['static_w']==.05
        assert power['components_dynamic_w']==dict(cpu=.05,npu=.06,crossbar=.01,storage=.02)
        microwatt=out/'microwatt.rpt'
        microwatt.write_text(report.read_text().replace('(W)', '(uW)'))
        assert power_report(microwatt)['static_w']==.05e-6
        assert abs(power_report(microwatt)['components_dynamic_w']['storage']-.02e-6)<1e-15
        (out/'saif_read.log').write_text('Successfully matched 80 out of 100 nets\n')
        assert saif_matching(out)['matched_ratio']==.8
        (out/'vivado.log').write_text('INFO: [Power 33-26] Design nets matched = 5 of 100\nINFO: [Power 33-26] Design nets matched = 90 of 34874\n')
        assert saif_matching(out)['design_nets']==34874
        assert saif_matching(out)['matched_nets']==90
        (out/'vivado.log').unlink()
        (out/'saif_read.log').write_text('No matching summary\n')
        assert saif_matching(out)['matched_ratio'] is None
        design=out/'design'
        (design/'power').mkdir(parents=True)
        (design/'summary.md').write_text('Result: PASS\n')
        (design/'route_metrics.tsv').write_text('requested_mhz\t50\nperiod_ns\t20\nwns_ns\t1\nwhs_ns\t.01\n')
        (design/'power/vectorless_power.rpt').write_text(report.read_text())
        (design/'power/saif_power.rpt').write_text(report.read_text())
        (design/'power/vivado.log').write_text('Design nets matched = 80 of 100\n')
        profile=out/'core.json'
        profile.write_text(json.dumps({'cases':[{'profile':{'classes':4,'total':2805}}]}))
        point=summarize_point(design,50,0,dict(cycles=4000),core_profile=profile)
        assert point['core_latency_us']==56.1
        assert point['activity_window_us']==80
        assert point['power']['saif']['energy_uj']==16
        assert point['power']['vectorless']['dynamic_energy_uj']==12
        result=write_summary(out/'summary',[point],1,True,'Test fixture')
        assert result['complete'] and result['fmax_mhz']==50
        # Packed layout indices come from AXI typedef declaration order, not
        # suffix similarity. Preserve unknown dwell and reject conflicting aliases.
        req,resp=axi_layout(2)
        assert len(req)==177 and len(resp)==48
        assert req[0]=='[r_ready]' and req[1]=='[ar_valid]'
        assert req[32]=='[ar][addr][0]' and req[176]=='[aw][id][1]'
        fixture={f'u_soc/i_axi_xbar/slv_reqs[{i}]':(i,40000-i,0,2,0) for i in range(708)}
        fixture.update({f'u_soc/i_cpu/core_i/id_stage_i/register_file_i/mem[{i}]':(10000,20000,10000,i%2,0) for i in range(1024)})
        fixture['u_soc/conflict_a']=(40000,0,0,0,0)
        fixture['u_soc/conflict_b']=(0,40000,0,0,0)
        source=out/'packed.saif'
        write_saif(source,fixture,text.split('(INSTANCE fpga_top')[0])
        inventory=out/'nets.tsv'
        target='u_soc/i_axi_xbar/slv_reqs[3][ar][addr][0]'
        cpu='u_soc/i_cpu/core_i/id_stage_i/register_file_i/gen_rf[11].mem[11][0]'
        inventory.write_text('name\tcanonical\tdriver_refs\tsequential\n'+
            target+'\t'+target+'\tFDRE\t1\n'+
            cpu+'\t'+cpu+'\tFDRE\t1\n'+
            'u_soc/alias\t'+target+'\tFDRE\t1\n'+
            'u_soc/conflict_a\tu_soc/conflict_a\tFDRE\t1\n'+
            'u_soc/conflict_b\tu_soc/conflict_a\tFDRE\t1\n'+
            'u_soc/conflict_new\tu_soc/conflict_a\tFDRE\t1\n'+
            'u_soc/unknown_lut\tu_soc/unknown_lut\tLUT6\t0\n')
        mapped=out/'mapped.saif'
        audit=map_activity(source,inventory,mapped,ROOT)
        observed,_=read_saif(mapped)
        assert all(observed[name]==value for name,value in fixture.items())
        assert observed[target]==fixture['u_soc/i_axi_xbar/slv_reqs[563]']
        assert observed[cpu]==fixture['u_soc/i_cpu/core_i/id_stage_i/register_file_i/mem[352]']
        assert observed['u_soc/alias']==observed[target]
        assert 'u_soc/conflict_new' not in observed and audit['conflicting_alias_groups_not_extended']==1
        assert 'u_soc/unknown_lut' not in observed
        assert observed[cpu][2]==10000
        # Check the actual scan search loop via a controlled implementation runner.
        class Args:
            resolution_mhz=1
            step_mhz=10
            max_mhz=80
            vivado_root=out
            jobs=4
            core_profile=None
        def fake_point(directory,requested,code,*unused,**kwargs):
            passed=requested<=73
            return dict(directory=str(directory),requested_mhz=requested,actual_mhz=requested,
                status='pass' if passed else 'timing_fail',timing={},power={})
        manifest=dict(points=[],converged=False,reason='',resolution_mhz=1)
        with patch.object(flow,'launch',return_value=0),patch.object(flow,'summarize_point',side_effect=fake_point),patch.object(flow,'write_summary'):
            flow.scan(Args(),out,manifest)
        assert manifest['converged'],manifest
        assert '[73, 74]' in manifest['reason'],manifest
        failing=dict(points=[],converged=False,reason='',resolution_mhz=1)
        def failure(directory,requested,code,*unused,**kwargs):
            return dict(directory=str(directory),requested_mhz=requested,status='tool_fail',timing=None,power={})
        with patch.object(flow,'launch',return_value=1),patch.object(flow,'summarize_point',side_effect=failure),patch.object(flow,'write_summary'):
            flow.scan(Args(),out,failing)
        assert not failing['converged'] and len(failing['points'])==1
    print('PASS activity counts, aliases, X dwell, window clipping, SAIF rescaling, report parsing, energy units, packed-bit mapping, proven aliases, conflict rejection, Fmax search and tool-failure handling')


if __name__=='__main__':
    main()
