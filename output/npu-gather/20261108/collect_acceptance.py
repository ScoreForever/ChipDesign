from pathlib import Path
import tarfile
root=Path(__file__).resolve().parent
build=root/'checkout_forward/build/gather_forward_50mhz'
required=['summary.md','provenance.json','route_timing_summary.rpt','route_setup_paths.rpt',
          'route_hold_paths.rpt','route_cdc.rpt','route_npu_jtag_cdc.tsv','route_utilization.rpt',
          'fpga_top.bit','programmed_board_properties.txt']
assert all((build/n).is_file() for n in required)
with tarfile.open(root/'acceptance_forward.tar.gz','w:gz') as t:
    names=set(required)
    names.update(p.name for p in build.glob('route*.rpt'))
    names.update(p.name for p in build.glob('route*.tsv'))
    for n in sorted(names):t.add(build/n,arcname='vivado_forward/'+n)
    for p in (root/'cdc_forward').iterdir():
        if p.suffix in ('.log','.json'):t.add(p,arcname='cdc_forward/'+p.name)
    for n in ('program_gate.json','program_forward.log','program_forward.jou'):
        t.add(root/n,arcname=n)
print('Collected final same-build timing, CDC, bitstream and programming evidence')
