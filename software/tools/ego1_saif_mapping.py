#!/usr/bin/env python3
"""Map observed SAIF bits to proven net aliases; diagnose remaining physical nets.

No activity is synthesized for LUT/carry/DSP/RAM implementation-only nodes.
AXI layouts follow hardware/soc/rtl/include/axi/typedef.svh, LSB first. The
profile is applied only to the main crossbar with its declared widths/dimensions;
all aliases must exist in the checkpoint inventory. Other configurations retain
the original SAIF and report their unhandled names.
"""
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from kws_vcd_to_saif import atom


def read_saif(path):
    stack, frames, nets = [], [], {}
    header=[]
    started=False
    for line in path.open():
        text=line.strip()
        if text.startswith('(INSTANCE '):
            started=True
            stack.append(re.sub(r'\\(.)',r'\1',text[len('(INSTANCE '):]))
            frames.append('instance')
        elif text=='(NET':
            frames.append('net')
        elif text==')' and frames:
            if frames.pop()=='instance':
                stack.pop()
        elif frames and frames[-1]=='net' and text.startswith('('):
            match=re.fullmatch(r'\((\S+) \(T0 (\d+)\) \(T1 (\d+)\) \(TX (\d+)\) \(TC (\d+)\) \(IG (\d+)\)\)',text)
            if not match:
                raise ValueError('Unsupported SAIF net row: '+text[:160])
            name=re.sub(r'\\(.)',r'\1',match[1])
            full='/'.join(stack[1:]+[name])
            if full in nets:
                raise ValueError('Duplicate SAIF path: '+full)
            nets[full]=tuple(map(int,match.groups()[1:]))
        elif not started:
            header.append(line)
    return nets,''.join(header)


def write_saif(path,nets,header):
    tree={'nets':{},'children':{}}
    for full,value in nets.items():
        *parts,name=full.split('/')
        node=tree
        for part in parts:
            node=node['children'].setdefault(part,{'nets':{},'children':{}})
        node['nets'][name]=value
    with path.open('w',newline='\n') as out:
        out.write(header)
        def emit(name,node,depth):
            pad=' '*depth
            out.write(pad+'(INSTANCE '+atom(name)+'\n')
            if node['nets']:
                out.write(pad+' (NET\n')
                for net,values in sorted(node['nets'].items()):
                    out.write(pad+'  ('+atom(net)+' '+' '.join('('+key+' '+str(value)+')' for key,value in zip(['T0','T1','TX','TC','IG'],values))+')\n')
                out.write(pad+' )\n')
            for child,value in sorted(node['children'].items()):
                emit(child,value,depth+1)
            out.write(pad+')\n')
        emit('fpga_top',tree,1)
        out.write(')\n')


def packed_fields(fields):
    """Ordered (field path, bit) entries, least significant bit first."""
    bits=[]
    for name,width in reversed(fields):
        prefix='['+name+']'
        if isinstance(width,list):
            bits.extend(prefix+suffix for suffix in packed_fields(width))
        else:
            bits.extend(prefix+('['+str(i)+']' if width>1 else '') for i in range(width))
    return bits


def axi_layout(id_width):
    aw=[('id',id_width),('addr',32),('len',8),('size',3),('burst',2),('lock',1),('cache',4),('prot',3),('qos',4),('region',4),('atop',6),('user',1)]
    ar=[x for x in aw if x[0]!='atop']
    w=[('data',32),('strb',4),('last',1),('user',1)]
    b=[('id',id_width),('resp',2),('user',1)]
    r=[('id',id_width),('data',32),('resp',2),('last',1),('user',1)]
    req=[('aw',aw),('aw_valid',1),('w',w),('w_valid',1),('b_ready',1),('ar',ar),('ar_valid',1),('r_ready',1)]
    resp=[('aw_ready',1),('ar_ready',1),('w_ready',1),('b_valid',1),('b',b),('r_valid',1),('r',r)]
    return packed_fields(req),packed_fields(resp)


def map_activity(source,inventory,output,repo):
    original,header=read_saif(source)
    rows=list(csv.DictReader(inventory.open(),delimiter='\t'))
    targets={row['name'] for row in rows}
    observed=dict(original)
    evidence=[]
    def add(target,source_name,rule):
        if target in targets and target not in observed:
            observed[target]=original[source_name]
            evidence.append(dict(target=target,source=source_name,rule=rule))
    groups=defaultdict(dict)
    for name in original:
        match=re.fullmatch(r'(.*)\[(\d+)\]',name)
        if match:
            groups[match[1]][int(match[2])]=name
    # CV32E40P packed declarations: mem[31:0][31:0], counters[31:0][63:0].
    for base,bits in list(groups.items()):
        if base.endswith('/register_file_i/mem') and len(bits)==1024 and set(bits)==set(range(1024)):
            for index,source_name in bits.items():
                word,bit=divmod(index,32)
                add(base+f'[{word}][{bit}]',source_name,'CV32E40P packed 32x32 register file')
                parent=base.rsplit('/',1)[0]
                add(parent+f'/gen_rf[{word}].mem[{word}][{bit}]',source_name,'CV32E40P generated register file alias')
        if base.endswith('/cs_registers_i/mhpmcounter_q') and len(bits)==2048:
            for index,source_name in bits.items():
                word,bit=divmod(index,64)
                add(base+f'[{word}][{bit}]',source_name,'CV32E40P packed 32x64 counters')
                parent=base.rsplit('/',1)[0]
                add(parent+f'/gen_mhpmcounter[{word}].gen_implemented.mhpmcounter_q[{word}][{bit}]',source_name,'CV32E40P generated counter alias')
    config=(repo/'hardware/soc/rtl/my_soc_top.sv').read_text(encoding='utf-8')
    nslv=int(re.search(r'NoSlvPorts:\s*(\d+)',config)[1])
    nmst=int(re.search(r'NoMstPorts:\s*(\d+)',config)[1])
    # Current profile: addr/data/user/slave ID = 32/32/1/2. Width guards below
    # reject changed packed layouts rather than guessing bit positions.
    root='u_soc/i_axi_xbar'
    req,resp=axi_layout(2)
    mst_req,mst_resp=axi_layout(2+(nslv-1).bit_length())
    layouts={root+'/slv_reqs':([nslv],req),root+'/slv_resps':([nslv],resp),
             root+'/mst_reqs':([nmst],mst_req),root+'/mst_resps':([nmst],mst_resp),
             root+'/i_xbar/slv_reqs':([nslv,nmst+1],req),root+'/i_xbar/slv_resps':([nslv,nmst+1],resp),
             root+'/i_xbar/mst_reqs':([nmst,nslv],req),root+'/i_xbar/mst_resps':([nmst,nslv],resp)}
    buses={}
    for body,name in re.findall(r'AXI_BUS\s*#\((.*?)\)\s*(slave|master)\[',config,re.S):
        buses[name]={key:int(value) for key,value in re.findall(r'\.(AXI_\w+_WIDTH)\s*\(\s*(\d+)\s*\)',body)}
    expected=lambda id_width:dict(AXI_ADDR_WIDTH=32,AXI_DATA_WIDTH=32,AXI_USER_WIDTH=1,AXI_ID_WIDTH=id_width)
    axi_profile_valid=buses.get('slave')==expected(2) and buses.get('master')==expected(2+(nslv-1).bit_length())
    if not axi_profile_valid:
        layouts={}
    for base,(dims,layout) in layouts.items():
        bits=groups.get(base,{})
        size=len(layout)
        for dim in dims: size*=dim
        if len(bits)!=size or set(bits)!=set(range(size)):
            continue
        for index,source_name in bits.items():
            word,bit=divmod(index,len(layout))
            indices=[]
            for dim in reversed(dims):
                word,i=divmod(word,dim)
                indices.insert(0,i)
            add(base+''.join(f'[{i}]' for i in indices)+layout[bit],source_name,'AXI packed struct and array flattening')
    aliases=defaultdict(list)
    for row in rows:
        aliases[row['canonical'] or row['name']].append(row['name'])
    conflicts=0
    for canonical,names in aliases.items():
        sources=[name for name in names if name in observed]
        values={observed[name] for name in sources}
        if len(values)>1:
            conflicts+=1
            continue
        if not sources:
            continue
        source_name=sources[0]
        for name in names:
            if name not in observed:
                observed[name]=observed[source_name]
                evidence.append(dict(target=name,source=source_name,rule='checkpoint PARENT net alias equivalence'))
    write_saif(output,observed,header)
    report=dict(source=str(source.resolve()),mapped=str(output.resolve()),source_bit_nets=len(original),
                added_aliases=len(observed)-len(original),axi_profile_valid=axi_profile_valid,rules=dict(Counter(x['rule'] for x in evidence)),
                conflicting_alias_groups_not_extended=conflicts,aliases=evidence,
                policy='Only observed bit activity and checkpoint-proven aliases; no default or invented activity for unmatched nodes')
    output.with_suffix('.mapping.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def diagnose(directory):
    directory=Path(directory)
    path=directory/'saif_unmatched.rpt'
    if not path.exists(): return None
    parts=path.read_text().split('--- Unmatched design nets ---')
    simulation=[line for line in parts[0].splitlines() if line and not line.startswith('---')]
    design=[line for line in parts[1].splitlines() if line and not line.startswith('---')] if len(parts)>1 else []
    rows={row['name']:row for row in csv.DictReader((directory/'design_nets.tsv').open(),delimiter='\t')}
    def category(name):
        refs=rows.get(name,{}).get('driver_refs','')
        if re.search(r'FD[RPCSE]+',refs): return 'flip_flop_output_or_replica'
        if 'DSP' in refs: return 'DSP_primitive_output'
        if 'RAM' in refs: return 'RAM_primitive_output'
        if 'CARRY' in refs: return 'carry_implementation_output'
        if 'LUT' in refs or 'MUXF' in refs: return 'LUT_mux_implementation_output'
        if refs in ('GND','VCC'): return 'constant'
        if 'BUF' in refs or 'MMCM' in refs: return 'buffer_or_MMCM'
        return 'other_or_primary_input'
    buckets=defaultdict(list)
    for name in design: buckets[category(name)].append(name)
    result=dict(unmatched_simulation_nets=len(simulation),unmatched_design_nets=len(design),
        design_categories={key:dict(count=len(names),examples=names[:8]) for key,names in sorted(buckets.items())},
        simulation_by_hierarchy=dict(Counter('/'.join(name.split('/')[:3]) for name in simulation).most_common(20)),
        unknown_state_warnings=len(re.findall(r'Power 33-288', (directory/'vivado.log').read_text(errors='replace'))),
        interpretation=['Simulation-only parameters, unused memory bits and eliminated hierarchy have no routed counterpart.',
                        'DSP/RAM/carry/LUT generated internal outputs need gate-level activity to match directly; RTL SAIF cannot supply them.',
                        'Driver categories do not prove every remaining net is synthesis-only; unmapped logical names can remain.',
                        'Remaining activity is estimated by Vivado propagation; direct matching coverage is reported separately and is not replaced by a propagated-coverage percentage.',
                        'X/Z dwell is preserved, including inactive datapath/debug state; it is not changed into zero activity.'])
    mapped=directory/'workload_mapped.saif'
    if mapped.exists():
        observed,_=read_saif(mapped)
        same=[name for name in design if name in observed]
        result['unmatched_design_names_present_in_saif']=len(same)
        result['same_name_with_any_unknown_dwell']=sum(observed[name][2]>0 for name in same)
        result['same_name_with_majority_unknown_dwell']=sum(observed[name][2]>.5*sum(observed[name][:3]) for name in same)
        result['same_name_examples']=[dict(name=name,unknown_fraction=observed[name][2]/sum(observed[name][:3])) for name in same[:8]]
    history=[]
    for log in sorted(directory.glob('vivado*.log')):
        counts=re.findall(r'Design nets matched\s*=\s*(\d+)\s+of\s+(\d+)',log.read_text(errors='replace'))
        if counts:
            a,b=map(int,counts[-1])
            history.append(dict(log=str(log.resolve()),matched_nets=a,design_nets=b,ratio=a/b))
    result['native_matching_history']=history
    audit=directory/'workload_mapped.mapping.json'
    if audit.exists():
        source=Path(json.loads(audit.read_text())['source'])
        vcd=source.with_suffix('.vcd')
        metadata=source.with_suffix('.json')
        if vcd.exists() and metadata.exists():
            prefix=json.loads(metadata.read_text())['source_hierarchy']
            scopes=[]
            kinds=Counter()
            with vcd.open() as stream:
                for line in stream:
                    if '$enddefinitions' in line: break
                    if '$scope' in line: scopes.append(line.split()[2])
                    elif '$upscope' in line: scopes.pop()
                    elif '$var' in line and ('/'.join(scopes)==prefix or '/'.join(scopes).startswith(prefix+'/')):
                        fields=line.split()
                        kinds[fields[1]]+=int(fields[2])
            result['source_vcd_declaration_bits_by_kind']=dict(kinds)
            result['source_vcd_header_evidence']=str(vcd.resolve())
    log_text=(directory/'vivado.log').read_text(errors='replace')
    result['power_warning_codes']=dict(Counter(re.findall(r'WARNING: \[(Power [^\]]+)\]',log_text)))
    result['clock_constraint_mismatch_warning']=bool(re.search(r'Power 33-334',log_text))
    result['low_coverage_reference']=0.8
    if history:
        current=next((x for x in history if Path(x['log']).name=='vivado.log'),history[-1])
        result['direct_coverage_below_reference']=current['ratio']<.8
    (directory/'saif_diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
