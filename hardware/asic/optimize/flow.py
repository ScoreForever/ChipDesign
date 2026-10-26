#!/usr/bin/env python3
"""Reproducible No-CPU full-chip experiments; run on the course Linux node."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SIGNALS = {'N': [('clk_i', 'input'), ('rst_ni', 'input'), ('irq_o', 'output')],
           'S': [('tck_i', 'input'), ('tms_i', 'input'), ('td_i', 'input'), ('td_o', 'output')], 'W': [], 'E': []}
# Two RA1SHD_2048x32M8 instances: the model store and the activation store.
MACROS = 2
MACRO_ROLES = ('model', 'activation')
LAYOUTS = ('west_stack', 'south_row', 'diagonal')
# Final-project area budget: die (including the PAD ring) at most 2 mm x 2 mm.
MAX_DIE_UM = 2000
# Complete Innovus runs allowed per experiment directory.
BUDGET = 10
UTILIZATIONS = (.55, .6, .65, .7, .75)
SUPPLIES = [('vss_1','PVSS1CDG'), ('vss_2','PVSS1CDG'), ('vssio','PVSS2CDG'),
            ('vdd_1','PVDD1CDG'), ('vdd_2','PVDD1CDG'), ('vddio','PVDD2CDG'), ('poc','PVDD2POC')]

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tcl(value):
    value = str(value)
    if any(c in value for c in '{}\n\r'):
        raise ValueError('unsupported Tcl path/value')
    return '{' + value + '}'


def libraries(pdk=Path('/lec/pdk/tsmc180'), course=Path('/lec/lab4/soc_cv32e40p_template_pnr-stu')):
    std = pdk/'tcb018gbwp7t_290a/TSMCHOME/digital'
    io = pdk/'tpd018nv_280a/TSMCHOME/digital'
    bond = pdk/'tpb018v_180a/TSMCHOME/digital'
    macro = course/'src/macro/RA1SHD_2048x32M8'
    return dict(std_lib=std/'Front_End/timing_power_noise/NLDM/tcb018gbwp7t_270a/tcb018gbwp7ttc.lib',
        io_lib=io/'Front_End/timing_power_noise/NLDM/tpd018nv_280a/tpd018nvtc.lib',
        macro_lib=macro/'RA1SHD_2048x32M8_tt_1p8_25C_syn.lib',
        std_lef=std/'Back_End/lef/tcb018gbwp7t_270a/lef/tcb018gbwp7t_6lm.lef',
        io_lef=io/'Back_End/lef/tpd018nv_280a/mt_2/6lm/lef/tpd018nv_6lm.lef',
        io_ant_lef=io/'Back_End/lef/tpd018nv_280a/mt_2/6lm/lef/antenna_6lm.lef',
        bond_lef=bond/'Back_End/lef/tpb018v_180a/cup/6lm/lef/tpb018v_6lm.lef',
        macro_lef=macro/'RA1SHD_2048x32M8.vclef', macro_ant_lef=macro/'RA1SHD_2048x32M8_ant.lef',
        captable=std/'Back_End/lef/tcb018gbwp7t_270a/techfiles/captable/t018lo_1p6m_typical.captable',
        gds_map=std/'Back_End/lef/tcb018gbwp7t_270a/techfiles/gds2.map',
        std_gds=std/'Back_End/gds/tcb018gbwp7t_270a/tcb018gbwp7t.gds',
        io_gds=io/'Back_End/gds/tpd018nv_280a/mt_2/6lm/tpd018nv.gds',
        bond_gds=bond/'Back_End/gds/tpb018v_180a/cup/6lm/tpb018v.gds', macro_gds=macro/'RA1SHD_2048x32M8.gds',
        std_verilog=std/'Front_End/verilog/tcb018gbwp7t_270a/tcb018gbwp7t.v',
        io_verilog=io/'Front_End/verilog/tpd018nv_260a/tpd018nv.v', macro_verilog=macro/'RA1SHD_2048x32M8.v')


def lef_sizes(paths):
    sizes = {}
    for path in paths:
        data = Path(path).read_text(errors='replace')
        for match in re.finditer(r'(?m)^[ \t]*MACRO\s+(\S+)(.*?)(?=^[ \t]*END\s+\1[ \t]*$)', data, re.S | re.M):
            size = re.search(r'\bSIZE\s+([\d.]+)\s+BY\s+([\d.]+)', match[2])
            if size:
                sizes[match[1]] = [float(size[1]), float(size[2])]
    return sizes


def snap(v):
    return round(round(v/.005)*.005,3)


def macro_layout(layout, xlo, ylo, xhi, yhi, mw, mh, gap):
    """Lower-left corners of the model and activation SRAMs inside [xlo,xhi]x[ylo,yhi]."""
    if layout=='west_stack':return [(xlo,ylo),(xlo,ylo+mh+gap)]
    if layout=='south_row':return [(xlo,ylo),(xlo+mw+gap,ylo)]
    if layout=='diagonal':return [(xlo,ylo),(xhi-mw,yhi-mh)]
    raise ValueError('unknown macro layout: '+str(layout))


def geometry(std_area, sizes, utilization=.6, layout='west_stack', halo=26):
    if not 0 < utilization < 1 or std_area <= 0 or halo < 0 or layout not in LAYOUTS:
        raise ValueError('invalid geometry inputs')
    mw, mh = sizes['RA1SHD_2048x32M8']
    margin = 150
    # Ring width/offset, macro halo and routing channels remain clear, both
    # to the core edge and between the two macros.
    clear = max(halo, 26) + 20
    gap = 2*max(halo, 26) + 20
    span = {'west_stack':(mw,2*mh+gap),'south_row':(2*mw+gap,mh),'diagonal':(2*mw+gap,2*mh+gap)}[layout]
    core = math.ceil(math.sqrt(std_area/utilization + MACROS*mw*mh)/20)*20
    core = max(core, math.ceil((max(span)+2*clear)/20)*20)
    corner = sizes['PCORNER'][0]
    pad_width, pad_depth = sizes['PDDW0204CDG']
    # Enough room for every side's electrical pads and corners, with 10um gaps.
    minimum = max((len(SUPPLIES)+len(v))*(pad_width+10) + 2*corner for v in SIGNALS.values())
    core = max(core, math.ceil((minimum-2*margin)/20)*20)
    die = core+2*margin
    xlo,ylo=margin+clear,margin+clear
    xhi,yhi=margin+core-clear,margin+core-clear
    macros=[]
    for role,(x,y) in zip(MACRO_ROLES,macro_layout(layout,xlo,ylo,xhi,yhi,mw,mh,gap)):
        if x<xlo-1e-6 or y<ylo-1e-6 or x+mw>xhi+1e-6 or y+mh>yhi+1e-6:
            raise ValueError('macros and halos do not fit')
        macros.append([role,snap(x),snap(y),mw,mh])
    pads=[]
    for side,signals in SIGNALS.items():
        entries=[(p,'PDDW0204CDG',direction) for p,direction in signals]+[(p,c,'supply') for p,c in SUPPLIES]
        step=(die-2*corner)/len(entries)
        for i,(name,cell,direction) in enumerate(entries):
            width,depth=sizes[cell]
            at=round(round((corner+(i+.5)*step-width/2)/.005)*.005,3)
            if step < width+1:
                raise ValueError('overlapping pads')
            if side=='N': loc=[at,die-depth];orient='R180'
            elif side=='S': loc=[at,0];orient='R0'
            elif side=='W': loc=[0,at];orient='R270'
            else: loc=[die-depth,at];orient='R90'
            pads.append(dict(name='i_pad_'+(name if direction!='supply' else side+'_'+name),
                port=name if direction!='supply' else None, cell=cell,direction=direction,side=side,loc=loc,orient=orient))
    return dict(core_side_um=core,die_side_um=die,margin_um=margin,macros=macros,layout=layout,halo_um=halo,pads=pads,corner_um=corner,bond_depth_um=sizes.get('PAD60LU_OBV',[60,73.16])[1])


def rtl_fingerprint():
    listing=ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f'
    paths=[listing,ROOT/'hardware/soc/synth/scripts/soc_constraints.tcl']
    paths += [ROOT/line.strip() for line in listing.read_text().splitlines() if line.strip() and not line.startswith('+')]
    return {str(p.relative_to(ROOT)):digest(p) for p in paths}


def fingerprint():
    paths = [ROOT/line.strip() for line in (ROOT/'hardware/soc/sim/filelists/chipdesign_nocpu.f').read_text().splitlines() if line.strip() and not line.startswith('+')]
    paths += sorted(HERE.glob('*.tcl')) + sorted(HERE.glob('*.sv')) + sorted(HERE.glob('*.py'))
    paths += [ROOT/p for p in ('hardware/soc/sim/filelists/chipdesign_nocpu.f','hardware/soc/synth/scripts/soc_constraints.tcl','hardware/asic/flow/config/user_define.tcl','hardware/asic/flow/config/global_define.tcl','hardware/asic/flow/pnr/scripts/init_pnr_standard.tcl','hardware/npu/scripts/run_program_regression.py','software/host/npu_program.py','software/host/nocpu_jtag.py','hardware/npu/tools/tinycnn8_golden.py')]
    return {str(p.relative_to(ROOT)):digest(p) for p in paths}


def preflight(output):
    libs=libraries()
    missing=[str(v) for v in libs.values() if not v.is_file()]
    tools={x:shutil.which(x) for x in ('genus','innovus','xrun')}
    result=dict(libraries={k:str(v) for k,v in libs.items()},missing=missing,tools=tools,sources_sha256=fingerprint(),
                library_sha256={k:digest(v) for k,v in libs.items() if v.is_file()},license_checked=False)
    if (output/'preflight.json').exists():
        previous=json.loads((output/'preflight.json').read_text())
        if previous.get('library_sha256')!=result['library_sha256']:
            raise RuntimeError('library inputs changed; use a new output directory')
    save(output/'preflight.json',result)
    if missing or not all(tools.values()):
        raise RuntimeError('preflight failed; see preflight.json')
    return libs


def process_token(pid):
    path=Path('/proc')/str(pid)/'stat'
    try:return path.read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError):return None


def finish_tool(directory, marker, state):
    receipt=directory/'tool_exit.json'
    if not receipt.exists():
        state.update(status='failed',reason='missing independent tool exit receipt')
    else:
        record=json.loads(receipt.read_text())
        expected=hashlib.sha256(json.dumps(state['command']).encode()).hexdigest()
        data=(directory/'console.log').read_text(errors='replace')
        errors=re.findall(r'(?mi)^(?:Error\s*:|\*\*ERROR:|\*E,|FATAL:|.*\*E,).*$',data)
        diagnostics=[e for e in errors if 'IMPSP-9099' in e and Path(state['command'][0]).name=='innovus']
        fatal=[e for e in errors if e not in diagnostics]
        completed=bool(re.search(r'(?m)^'+re.escape(marker)+r'(?:\s|$)',data))
        state.update(exit_code=record['exit_code'],marker=completed,errors=errors,unresolved_scan_diagnostics=diagnostics)
        state['status']='complete' if record['exit_code']==0 and record['command_sha256']==expected and completed and not fatal else 'failed'
        state['finished']=record['finished']
    state.setdefault('finished',time.time());save(directory/'status.json',state)
    return state


def recover_tool(directory, marker):
    state=json.loads((directory/'status.json').read_text())
    if state['status']!='running':return state
    # A missing receipt can never be replaced with a guessed zero exit code.
    deadline=state['started']+state.get('timeout_seconds',14400)
    while not (directory/'tool_exit.json').exists():
        token=process_token(state.get('pid',-1))
        if not token or token!=state.get('process_token'):
            state.update(status='failed',reason='interrupted tool has no verified exit receipt',finished=time.time())
            save(directory/'status.json',state);return state
        if time.time()>deadline:
            import signal
            os.killpg(state['pid'],signal.SIGTERM)
            state.update(status='failed',reason='recovered tool exceeded original timeout',finished=time.time())
            save(directory/'status.json',state);return state
        time.sleep(2)
    return finish_tool(directory,marker,state)


def launch(command, directory, marker, timeout=14400):
    command=list(map(str,command));directory.mkdir(parents=True,exist_ok=True)
    if (directory/'status.json').exists():raise RuntimeError('refusing to overwrite previous run: '+str(directory))
    state=dict(command=command,started=time.time(),status='running',timeout_seconds=timeout)
    save(directory/'status.json',state)
    try:
        with (directory/'console.log').open('w') as log:
            worker=[sys.executable,str(HERE/'tool_worker.py'),json.dumps(command),str(directory/'tool_exit.json')]
            proc=subprocess.Popen(worker,cwd=directory,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            state.update(pid=proc.pid,process_token=process_token(proc.pid));save(directory/'status.json',state)
            try:proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=30)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                raise TimeoutError('tool timeout')
        return finish_tool(directory,marker,state)
    except Exception as exc:
        state.update(status='failed',reason=str(exc),finished=time.time());save(directory/'status.json',state)
        return state


def validate_reports(directory):
    """Fail closed; unknown report formats are not evidence of success."""
    def read(name):
        path=directory/name
        return path.read_text(errors='replace') if path.is_file() else ''
    drc=lambda text: bool(re.search(r'No DRC violations were found',text,re.I))
    antenna=bool(re.search(r'No Violations Found',read('antenna.rpt'),re.I))
    regular=read('connectivity.rpt')
    # Abutting native IO supply rails are checked separately from routed nets.
    infos=re.findall(r'Net (\S+): no routing',regular)
    regular_ok='Found no problems or warnings' in regular or (len(infos)==2 and set(infos)=={'VDDPST','VSSPST'} and '2 total info(s)' in regular and '2 Problem(s) (IMPVFC-98)' in regular)
    place=read('placement.rpt')
    unplaced=re.search(r'Number of Unplaced Instances\s*=\s*(\d+)',place)
    # Bond openings intentionally overlay electrical PAD cells; all other
    # legal placement failure classes must be absent. Actual rectangles below
    # independently check electrical PAD/corner/filler overlap and die bounds.
    lists=re.findall(r'set (pchk\w+) \[list',place)
    place_ok=bool(unplaced and int(unplaced[1])==0 and all(x=='pchkIoOverlapList' for x in lists))
    pg='Found no problems or warnings' in read('pg_connectivity.rpt')
    constraints=read('constraints.rpt')
    drv=bool(re.search(r'No (?:constraint )?violations|max_transition|max_capacitance|max_fanout|Setup Check|Hold Check',constraints,re.I)) and 'No constrained paths' not in constraints and not re.search(r'VIOLATED|max_(?:capacitance|transition|fanout).*-[0-9]',constraints,re.I)
    ports=read('ports.txt').split()
    ports_ok=set(ports)=={'clk_i','rst_ni','tck_i','tms_i','td_i','td_o','irq_o'} and len(ports)==7
    connections=read('io_connectivity.tsv').splitlines()
    ports_ok &= len(connections)==7
    seen=set()
    for line in connections:
        row=line.split()
        if len(row)!=5:ports_ok=False;continue
        port,external,pad_net,core_net,direction=row;seen.add(port)
        ports_ok &= external==pad_net and core_net!=external and core_net!='0x0' and direction==('output' if port in ('td_o','irq_o') else 'input')
    ports_ok &= seen==set(ports)
    geo=read('io_geometry.tsv'); boxes=[];pad_ok=bool(geo)
    for line in geo.splitlines():
        name,cell,orientation,*coords=line.split()
        x0,y0,x1,y1,die=map(float,coords)
        pad_ok &= x0>=-.001 and y0>=-.001 and x1<=die+.001 and y1<=die+.001
        if cell!='PAD60LU_OBV':boxes.append((name,x0,y0,x1,y1))
    for i,a in enumerate(boxes):
        for b in boxes[i+1:]:
            if min(a[3],b[3])-max(a[1],b[1])>.001 and min(a[4],b[4])-max(a[2],b[2])>.001:
                pad_ok=False
    return dict(placement=place_ok,drc=drc(read('drc.rpt')),pg_drc=drc(read('pg_drc.rpt')),connectivity=regular_ok,pg_connectivity=pg,antenna=antenna,drv=drv,ports=ports_ok,pad_geometry=pad_ok)


def def_wire_length(path):
    text=Path(path).read_text()
    units=re.search(r'UNITS DISTANCE MICRONS (\d+)',text)
    if not units:raise ValueError('DEF units missing')
    nets=re.search(r'(?ms)^NETS \d+ ;(.*?)^END NETS',text)
    if not nets:raise ValueError('DEF NETS missing')
    length=0
    for section in re.split(r'\+\s*(?:ROUTED|FIXED|COVER)|\bNEW\b',nets[1])[1:]:
        previous=None
        for a,b in re.findall(r'\(\s*(-?\d+|\*)\s+(-?\d+|\*)(?:\s+-?\d+)?\s*\)',section.split(';')[0]):
            if previous is None and '*' in (a,b):raise ValueError('unresolved DEF wildcard')
            point=(previous[0] if a=='*' else int(a),previous[1] if b=='*' else int(b))
            if previous:length+=abs(point[0]-previous[0])+abs(point[1]-previous[1])
            previous=point
    if length<=0:raise ValueError('no routed wire geometry')
    return length/int(units[1])


def metrics(directory):
    directory=Path(directory)
    required=['metrics.json','checks.json','timing_setup.rpt','timing_hold.rpt','drc.rpt','pg_drc.rpt','connectivity.rpt','pg_connectivity.rpt','antenna.rpt','placement.rpt','io_geometry.tsv','io_connectivity.tsv','ports.txt','constraints.rpt','outputs/physical.v','outputs/simulation.v','outputs/routed.sdf.gz','outputs/routed.spef.gz','outputs/routed.def','outputs/chip.gds.gz','outputs/chip.lef','outputs/mapped.sdc']
    missing=[p for p in required if not (directory/p).is_file() or (directory/p).stat().st_size==0]
    state=json.loads((directory/'status.json').read_text()) if (directory/'status.json').exists() else {}
    result=dict(directory=str(directory),eligible=False,missing=missing,runtime_seconds=max(0,state.get('finished',0)-state.get('started',0)))
    if missing or state.get('status')!='complete': return result
    result.update(json.loads((directory/'metrics.json').read_text()))
    result['checks']=json.loads((directory/'checks.json').read_text())
    finite=all(isinstance(result.get(k),(int,float)) and math.isfinite(result[k]) for k in ('setup_wns_ns','hold_wns_ns','setup_tns_ns','hold_tns_ns','die_area_mm2','wire_length_um'))
    result['eligible']=finite and all(result['checks'].get(k) is True for k in ('placement','drc','pg_drc','connectivity','pg_connectivity','antenna','drv','ports','pad_geometry')) and result['setup_wns_ns']>=0 and result['hold_wns_ns']>=0 and result['setup_tns_ns']==0 and result['hold_tns_ns']==0 and result.get('macro_count')==MACROS
    result['within_area_budget']=finite and result['die_area_mm2']<=(MAX_DIE_UM/1000)**2+1e-9
    result['eligible']=result['eligible'] and result['within_area_budget']
    return result


def rank(rows):
    return sorted((r for r in rows if r.get('eligible')),key=lambda r:(r['die_area_mm2'],-r['setup_wns_ns'],r['wire_length_um'],r.get('runtime_seconds',0)))


def common_config(libs):
    return '\n'.join('set '+key+' '+tcl(value) for key,value in libs.items())+'\n'


def synth(output,libs):
    directory=output/'synthesis'
    directory.mkdir(parents=True,exist_ok=True)
    cfg=common_config(libs)+'set source_root '+tcl(ROOT)+'\nset threads 4\n'
    (directory/'config.tcl').write_text(cfg)
    save(directory/'rtl_sources.json',dict(sources_sha256=rtl_fingerprint(),origin='captured immediately before synthesis launch'))
    state=launch(['genus','-no_gui','-batch','-files',str(HERE/'synth.tcl')],directory,'PNR_OPT_SYNTHESIS_COMPLETE')
    if state['status']!='complete':raise RuntimeError('synthesis failed: '+str(directory))
    verify_clocks(directory/'mapped.sdc')
    save(directory/'handoff.json',{p:digest(directory/p) for p in ('mapped.v','mapped.sdc','area.json')})


def verify_clocks(path):
    text=Path(path).read_text().replace('\\\n',' ')
    unit=re.search(r'set_units[^\n]*-time\s+(\S+)',text)
    # Genus writes "1000ps"; accept any spelling that is exactly 1 ns so clock
    # numbers are used as written, and reject every other scale.
    scale=re.fullmatch(r'([\d.]*)(ps|ns|us)',unit[1]) if unit else None
    if not scale or float(scale[1] or 1)*{'ps':1e-3,'ns':1,'us':1e3}[scale[2]]!=1:
        raise RuntimeError('handoff must use a 1 ns time unit; do not silently reinterpret SDC')
    clocks={}
    for line in text.splitlines():
        if not line.lstrip().startswith('create_clock'):continue
        name=re.search(r'-name\s+["{]?(\w+)',line);period=re.search(r'-period\s+(["{]?)([\d.]+)',line)
        if name and period:clocks[name[1]]=float(period[2])
    if clocks.get('core_clk')!=20 or clocks.get('jtag_clk')!=1000:raise RuntimeError('unexpected clock periods: '+str(clocks))
    return clocks


def adopt_synthesis(output, libs, source):
    """Import a completed course synthesis, without remapping or changing it."""
    source=source.resolve()
    original=json.loads((source/'rtl_sources.json').read_text())
    if original.get('sources_sha256')!=rtl_fingerprint():raise RuntimeError('imported synthesis RTL/constraints do not match current checkout')
    log=(source/'console.log').read_text(errors='replace')
    if not re.search(r'(?m)^EDA_MAPPED_SYNTHESIS_COMPLETE$',log) or re.search(r'(?mi)^Error\s*:',log):
        raise RuntimeError('external synthesis has no clean completion evidence')
    if (source/'exit_code').read_text().strip()!='0':raise RuntimeError('synthesis exit code')
    verify_clocks(source/'my_soc_top.sdc')
    report=(source/'area.rpt').read_text()
    top=re.search(r'(?m)^my_soc_top\s+(\d+)\s+([\d.]+)',report)
    macro=re.search(r'\barea\s*:\s*([\d.]+)',libs['macro_lib'].read_text(errors='replace'))
    if not top or not macro:raise RuntimeError('unrecognized synthesis area report')
    count=len(re.findall(r'(?m)^\s*RA1SHD_2048x32M8\s',(source/'my_soc_top.v').read_text(errors='replace')))
    if count!=MACROS:raise RuntimeError(f'expected {MACROS} mapped SRAM macros, found {count}')
    total=float(top[2]);macro_area=MACROS*float(macro[1])
    if total<=macro_area:raise RuntimeError('invalid imported area')
    directory=output/'synthesis'
    if directory.exists():raise RuntimeError('refusing to replace synthesis handoff')
    directory.mkdir()
    for old,new in [('my_soc_top.v','mapped.v'),('my_soc_top.sdc','mapped.sdc'),('area.rpt','area.rpt'),('timing.rpt','timing.rpt'),('console.log','console.log'),('run.tcl','original_driver.tcl'),('run.sh','original_driver.sh')]:
        shutil.copyfile(source/old,directory/new)
    save(directory/'area.json',dict(total_area_um2=total,standard_cell_area_um2=total-macro_area,macro_area_um2=macro_area,macro_count=count))
    verify_clocks(directory/'mapped.sdc')
    save(directory/'handoff.json',{p:digest(directory/p) for p in ('mapped.v','mapped.sdc','area.json')})
    save(directory/'import.json',dict(source=str(source),completed_marker='EDA_MAPPED_SYNTHESIS_COMPLETE',sources_sha256=fingerprint(),library_sha256={k:digest(v) for k,v in libs.items()},note='Imported completed mapped netlist; no new mapping or HDL rewrite'))


def snapshot_driver(directory):
    driver=directory/'driver';driver.mkdir()
    for path in HERE.iterdir():
        if path.suffix in ('.py','.tcl','.sv'):shutil.copyfile(path,driver/path.name)
    legacy=driver/'legacy';legacy.mkdir()
    for rel in ('config/user_define.tcl','config/global_define.tcl','pnr/scripts/init_pnr_standard.tcl'):
        shutil.copyfile(ROOT/'hardware/asic/flow'/rel,legacy/Path(rel).name)
    save(driver/'sha256.json',{str(p.relative_to(driver)):digest(p) for p in driver.rglob('*') if p.is_file()})
    return driver


def candidate(output,libs,name,util,layout,congestion=False,halo=26):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name) or halo<26:raise ValueError('invalid candidate name or halo below 26um')
    directory=output/'candidates'/name
    if (output/'sources.json').exists() and json.loads((output/'sources.json').read_text())!=fingerprint():
        raise RuntimeError('inputs changed between candidates')
    if (directory/'status.json').exists():
        saved=json.loads((directory/'experiment.json').read_text())
        if (saved['utilization'],saved.get('layout'),saved['congestion'],saved['geometry']['halo_um'])!=(util,layout,bool(congestion),halo):
            raise RuntimeError('candidate parameters differ from preserved run')
        state=json.loads((directory/'status.json').read_text())
        if state.get('status')=='running':state=recover_tool(directory,'PNR_OPT_PHYSICAL_COMPLETE')
        if state.get('status')=='complete':return finalize_candidate(directory,state)
        return metrics(directory)
    if len(list((output/'candidates').glob('*/status.json')))>=BUDGET:raise RuntimeError(f'{BUDGET}-candidate PNR budget exhausted')
    handoff=json.loads((output/'synthesis/handoff.json').read_text())
    if any(digest(output/'synthesis'/p)!=sha for p,sha in handoff.items()):raise RuntimeError('frozen synthesis handoff changed')
    area=json.loads((output/'synthesis/area.json').read_text())
    sizes=lef_sizes([libs[k] for k in ('macro_lef','io_lef','bond_lef')])
    geo=geometry(area['standard_cell_area_um2'],sizes,util,layout,halo)
    directory.mkdir(parents=True,exist_ok=True)
    save(directory/'experiment.json',dict(utilization=util,layout=layout,congestion=congestion,geometry=geo,handoff=handoff,sources_sha256=fingerprint()))
    if geo['die_side_um']>MAX_DIE_UM:
        # Over the area budget before any tool runs: record it, spend no Innovus time.
        result=dict(directory=str(directory),eligible=False,within_area_budget=False,die_side_um=geo['die_side_um'],
                    reason=f"die {geo['die_side_um']}um exceeds {MAX_DIE_UM}um budget")
        save(directory/'result.json',result);return result
    cfg=common_config(libs)+f'set threads 4\nset congestion {int(congestion)}\nset source_root '+tcl(ROOT)+'\nset netlist '+tcl(output/'synthesis/mapped.v')+'\nset sdc '+tcl(output/'synthesis/mapped.sdc')+'\n'
    for key in ('core_side_um','die_side_um','margin_um','corner_um','halo_um','bond_depth_um'):
        cfg+='set '+key+' '+str(geo[key])+'\n'
    cfg+='set macro_boxes {\n'+''.join(' {'+' '.join(map(str,m))+'}\n' for m in geo['macros'])+'}\nset pads {\n'
    for pad in geo['pads']:
        cfg+=' {'+' '.join([pad['name'],pad['cell'],pad['port'] or '-',pad['direction'],pad['side'],*map(str,pad['loc']),pad['orient']])+'}\n'
    cfg+='}\n'
    (directory/'config.tcl').write_text(cfg)
    driver=snapshot_driver(directory)
    state=launch(['innovus','-no_gui','-files',str(driver/'fullchip.tcl')],directory,'PNR_OPT_PHYSICAL_COMPLETE')
    return finalize_candidate(directory,state)


def finalize_candidate(directory,state):
    if state['status']=='complete':
        save(directory/'checks.json',validate_reports(directory))
        raw=json.loads((directory/'metrics.json').read_text());raw['wire_length_um']=def_wire_length(directory/'outputs/routed.def');save(directory/'metrics.json',raw)
        save(directory/'artifact_sha256.json',{str(p.relative_to(directory)):digest(p) for p in (directory/'outputs').iterdir() if p.is_file()})
    result=metrics(directory);result['runtime_seconds']=state['finished']-state['started'];save(directory/'result.json',result)
    return result


def summarize(output):
    rows=[metrics(p.parent) for p in sorted((output/'candidates').glob('*/status.json'))]
    ranked=rank([r for r in rows if Path(r['directory']).name!='repeat'])
    selected=json.loads((output/'selection.json').read_text())['directory'] if (output/'selection.json').exists() else None
    best=next((r for r in ranked if r['directory']==selected),ranked[0] if ranked else None)
    repeat=next((r for r in rows if Path(r['directory']).name=='repeat'),None)
    def sdf_pass(point):
        path=Path(point['directory'])/'sdf/result.json' if point else None
        if not path or not path.is_file():return False
        data=json.loads(path.read_text());hashes=data.get('sources_sha256',{})
        return bool(data.get('passed') and hashes and all(Path(p).is_file() and digest(p)==sha for p,sha in hashes.items()))
    chosen=json.loads((output/'baseline.json').read_text())['directory'] if (output/'baseline.json').exists() else None
    baseline=next((r for r in rows if r['directory']==chosen),None)
    release=bool(best and repeat and repeat['eligible'] and baseline and baseline['eligible'] and sdf_pass(best) and sdf_pass(baseline))
    frozen=json.loads((output/'sources.json').read_text()) if (output/'sources.json').exists() else None
    result=dict(candidates=rows,best=best,repeat=repeat,release_eligible=release,validated_area_improvement_percent=(100*(baseline['die_area_mm2']-best['die_area_mm2'])/baseline['die_area_mm2'] if release else None),limitation='TT only; vectorless power; no foundry signoff',sources_sha256=frozen)
    save(output/'summary.json',result)
    lines=['# Full-chip PNR experiments','','| Run | Valid physical result | Die mm² | Setup ns | Hold ns | Wire m |','|---|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append('| {} | {} | {} | {} | {} | {} |'.format(Path(r['directory']).name,r['eligible'],r.get('die_area_mm2','—'),r.get('setup_wns_ns','—'),r.get('hold_wns_ns','—'),r.get('wire_length_um',0)/1e6))
    lines+=['','Physical eligibility does not establish SDF or multi-corner signoff.']
    (output/'summary.md').write_text('\n'.join(lines)+'\n')
    return result


def search(output,libs):
    frozen=output/'sources.json'
    if frozen.exists() and json.loads(frozen.read_text())!=fingerprint(): raise RuntimeError('source/flow changed; use a new experiment directory')
    save(frozen,fingerprint())
    if not (output/'synthesis/handoff.json').exists():synth(output,libs)
    rows=[]
    # Densest first-layout point that closes; stop once a density fails, since
    # higher densities only get harder. Over-budget points cost no tool time.
    for util in UTILIZATIONS:
        row=candidate(output,libs,f'u{int(round(util*100))}',util,'west_stack')
        rows.append(row)
        ran=row.get('within_area_budget') is not False
        if ran and not row.get('eligible') and rank(rows):break
    best=rank(rows)
    if not best:summarize(output);raise RuntimeError('no physically valid candidate within the 2x2 mm budget')
    exp=json.loads((Path(best[0]['directory'])/'experiment.json').read_text());util=exp['utilization']
    for layout in ('south_row','diagonal'):rows.append(candidate(output,libs,f'{layout}_u{int(round(util*100))}',util,layout))
    best=rank(rows)[0];exp=json.loads((Path(best['directory'])/'experiment.json').read_text())
    rows.append(candidate(output,libs,'congestion',exp['utilization'],exp['layout'],True))
    best=rank(rows)[0];exp=json.loads((Path(best['directory'])/'experiment.json').read_text())
    save(output/'selection.json',dict(directory=best['directory'],experiment=exp))
    repeat=candidate(output,libs,'repeat',exp['utilization'],exp['layout'],exp['congestion'])
    summarize(output)
    if not repeat.get('eligible'):raise RuntimeError('independent repeat failed')
    if json.loads(frozen.read_text())!=fingerprint():raise RuntimeError('inputs changed before SDF acceptance')
    baseline=Path(rank([r for r in rows if Path(r['directory']).name.startswith('u')])[0]['directory'])
    save(output/'baseline.json',dict(directory=str(baseline)))
    for point in dict.fromkeys((baseline,Path(best['directory']))):
        if not (point/'sdf/result.json').exists():
            subprocess.run([sys.executable,str(HERE/'postlayout.py'),'--candidate',str(point)],check=True)
        if not json.loads((point/'sdf/result.json').read_text()).get('passed'):
            raise RuntimeError('SDF acceptance failed: '+str(point))
    final=summarize(output)
    if not final['release_eligible']:raise RuntimeError('final release gates are not satisfied')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['preflight','synth','adopt','baseline','trial','search','resume','summary'])
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--import-dir',type=Path)
    ap.add_argument('--name')
    ap.add_argument('--utilization',type=float,default=.6)
    ap.add_argument('--layout',choices=LAYOUTS,default='west_stack')
    ap.add_argument('--halo',type=float,default=26)
    ap.add_argument('--congestion',action='store_true')
    args=ap.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if args.action=='trial' and (not args.name or not re.fullmatch(r'[A-Za-z0-9_-]+',args.name) or not 0<args.utilization<1 or args.halo<26):
        ap.error('trial requires a simple name, utilization in (0,1) and halo >=26um')
    if args.action=='summary':summarize(output);return
    import fcntl
    lock=(output/'controller.lock').open('a')
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('another controller is already running for this experiment')
    libs=preflight(output)
    if args.action=='preflight':return
    frozen=output/'sources.json'
    if frozen.exists() and json.loads(frozen.read_text())!=fingerprint():raise RuntimeError('inputs changed; use a new experiment directory')
    if not frozen.exists():save(frozen,fingerprint())
    if args.action=='adopt':
        if not args.import_dir:ap.error('adopt requires --import-dir')
        adopt_synthesis(output,libs,args.import_dir)
    elif args.action=='synth':synth(output,libs)
    elif args.action=='trial':
        if not args.name:ap.error('trial requires --name')
        frozen=output/'sources.json'
        if not frozen.exists():save(frozen,fingerprint())
        if not (output/'synthesis/handoff.json').exists():synth(output,libs)
        result=candidate(output,libs,args.name,args.utilization,args.layout,args.congestion,args.halo)
        summarize(output)
        if not result.get('eligible'):raise RuntimeError('trial physical acceptance failed')
    elif args.action=='baseline':
        if not (output/'synthesis/handoff.json').exists():synth(output,libs)
        result=candidate(output,libs,'u60',.6,'west_stack');save(output/'baseline.json',dict(directory=result['directory']));summarize(output)
        if not result.get('eligible'):raise RuntimeError('baseline physical acceptance failed')
    else:search(output,libs)

if __name__=='__main__':main()
