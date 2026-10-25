"""Decode sampled on-chip waveforms and export VCD, CSV and operator JSON."""
import csv
import json
from pathlib import Path


def decode_sample(words):
    if len(words)!=4 or any(not isinstance(x,int) or not 0<=x<2**32 for x in words):
        raise ValueError('trace sample requires four uint32 words')
    cycle,signals,addresses,context=words
    names=('model_valid','model_ready','model_response','activation_valid','activation_ready',
           'activation_response','write_valid','mac_issue','wait_activation','wait_weight','wait_slot','kernel_prepare')
    row=dict(cycle=cycle,**{name:(signals>>i)&1 for i,name in enumerate(names)})
    row.update(slot_busy=(signals>>12)&15,slot_ready=(signals>>16)&15,
               weight_banks=(signals>>20)&3,inflight=(signals>>22)&63,
               gather_state=(signals>>28)&3,load_state=signals>>30,
               model_address=addresses&65535,activation_address=addresses>>16,
               write_address=context&65535,write_mask=(context>>16)&15,
               state=(context>>20)&15,pc=(context>>24)&15,opcode=(context>>28)&7,quantized=context>>31)
    return row


def export_trace(capture, output, period_ns=20):
    """Each row is a value sampled immediately before a system rising edge."""
    if capture.get('header',{}).get('depth')==0:
        raise ValueError('waveform trace is not enabled in this hardware build')
    if not isinstance(period_ns,int) or period_ns<2 or period_ns%2:
        raise ValueError('period_ns must be a positive even integer')
    vcd=Path(output)
    if vcd.suffix.lower()!='.vcd':raise ValueError('trace output must have .vcd suffix')
    vcd.parent.mkdir(parents=True,exist_ok=True)
    rows=[decode_sample(row['words']) for row in capture['samples']]
    if any(((b['cycle']-a['cycle'])&0xffffffff)!=1 for a,b in zip(rows,rows[1:])):
        raise ValueError('trace timestamps must be consecutive')
    widths={name:1 for name in ('clock','model_valid','model_ready','model_response','activation_valid',
        'activation_ready','activation_response','write_valid','mac_issue','wait_activation','wait_weight',
        'wait_slot','kernel_prepare','quantized')}
    widths.update(cycle=32,slot_busy=4,slot_ready=4,weight_banks=2,inflight=6,gather_state=2,load_state=2,
                  model_address=16,activation_address=16,write_address=16,write_mask=4,state=4,pc=4,opcode=3)
    identifiers={name:f's{i}' for i,name in enumerate(widths)}
    def value(name, val):
        if val is None:return f'x{identifiers[name]}' if widths[name]==1 else f'bx {identifiers[name]}'
        return f'{val}{identifiers[name]}' if widths[name]==1 else f'b{val:0{widths[name]}b} {identifiers[name]}'
    with vcd.open('w',encoding='ascii',newline='\n') as f:
        f.write('$timescale 1ns $end\n$scope module npu $end\n')
        for name,width in widths.items():f.write(f'$var wire {width} {identifiers[name]} {name} $end\n')
        f.write('$upscope $end\n$enddefinitions $end\n#0\n$dumpvars\n')
        f.write('\n'.join(value(name,None) for name in widths)+'\n$end\n')
        epoch=0;previous=None
        for row in rows:
            if previous is not None and row['cycle']<previous:epoch+=2**32
            time=(row['cycle']+epoch)*period_ns
            f.write(f'#{time}\n'+value('clock',1)+'\n')
            f.write('\n'.join(value(name,row[name]) for name in widths if name!='clock')+'\n')
            f.write(f'#{time+period_ns//2}\n'+value('clock',0)+'\n')
            previous=row['cycle']
        if rows:
            f.write(f'#{time+period_ns}\n$comment end of captured window $end\n')
            f.write('\n'.join(value(name,None) for name in widths if name!='clock')+'\n')
    csv_path=vcd.with_suffix('.csv');json_path=vcd.with_suffix('.json')
    columns=list(decode_sample([0]*4))
    with csv_path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(rows)
    document=dict(capture,period_ns=period_ns,sampling='immediately before each rising edge',
                  waveform=str(vcd),csv=str(csv_path))
    json_path.write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8')
    return dict(vcd=str(vcd),csv=str(csv_path),json=str(json_path),samples=len(rows))
