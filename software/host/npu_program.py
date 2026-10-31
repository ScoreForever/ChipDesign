"""Packed INT4 model compiler and independent integer reference, ABI 3.0.

Pure Python, no framework/runtime dependency. Public input uses five logical
operators. Fusion only removes single-consumer, non-output intermediates.
"""
from __future__ import annotations
from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
import struct
import operator

# ABI 3.0: packed signed INT4 tensors/weights, INT32 raw tensors and bias,
# Q15 multiplier + signed shift in one word, byte-addressed descriptors.
ABI=0x30000
FORMAT_MAGIC=0x49340f40  # I4, Q15, four rows; low nibble is array column count.
NUMERIC_FORMAT=0x0f200404
OPS={'Conv':1,'FC':2,'MaxPool':3,'ReLU':4,'Requant':5}
Q,R,P,S32,D32=1<<8,1<<9,1<<10,1<<11,1<<12

def wrap32(x):
    return (int(x)+2**31)%2**32-2**31

def requant(x,mult,shift,lo=-8,hi=7):
    """Double-rounding Q15: saturating left shift, SRDHM, rounding POT."""
    if not 0<=mult<2**15 or not -31<=shift<=31 or not -8<=lo<=hi<=7:
        raise ValueError('invalid INT4 Q15 parameters')
    x=max(-2**31,min(2**31-1,wrap32(x)*2**max(shift,0)))
    y=max(-2**31,min(2**31-1,(x*mult+2**14)//2**15))
    if shift<0:
        e=-shift
        base=y//2**e
        remainder=y-base*2**e
        y=base+int(remainder>((2**e-1)//2+int(y<0)))
    return max(lo,min(hi,y))

def pack_i4(values):
    """Low nibble first; odd high padding nibble is canonical zero."""
    values=[operator.index(v) for v in values]
    if any(v < -8 or v > 7 for v in values):raise ValueError('value outside signed INT4')
    return bytes((values[i]&15)|((values[i+1]&15)<<4 if i+1<len(values) else 0) for i in range(0,len(values),2))

def unpack_i4(payload,count):
    if count<0 or len(payload)!=(count+1)//2:raise ValueError('packed INT4 length')
    if count%2 and payload[-1]&0xf0:raise ValueError('nonzero INT4 padding nibble')
    return [((payload[i//2]>>(4*(i%2))&15)^8)-8 for i in range(count)]

def encode_tensor(values,dtype):
    values=[operator.index(v) for v in values]
    if dtype=='i4':return pack_i4(values)
    if dtype=='i32':return struct.pack('<'+'i'*len(values),*values)
    raise ValueError('tensor dtype must be i4 or i32')

def decode_tensor(payload,dtype,count):
    if dtype=='i4':return unpack_i4(payload,count)
    if dtype=='i32' and len(payload)==count*4:return list(struct.unpack('<'+'i'*count,payload))
    raise ValueError('invalid tensor dtype/length')

def quantize_int8_model(model):
    """Explicit uniform /16 PTQ import, not a lossless ABI conversion or QAT.

    INT8 inputs/activations and weights use 16 old units per new unit. Bias
    uses 256 old accumulator units. Q31 scales are recoded to Q15 with the
    corresponding scale ratio. Dataset accuracy must be measured separately.
    """
    import copy
    if model['input'].get('dtype','i8')!='i8':raise ValueError('PTQ import requires an INT8 source model')
    result=copy.deepcopy(model)
    result['input']['dtype']='i4'
    units={result['input']['name']:16}
    for node in result['nodes']:
        source_units=units[node['input']]
        if node['op'] in ('Conv','FC'):
            weights=flatten(node['weights'])
            if any(v< -128 or v>127 for v in weights):raise ValueError('PTQ source weights outside INT8')
            node['weights']=[max(-8,min(7,round(v/16))) for v in weights]
            units[node['output']]=source_units*16
            node['bias']=[wrap32(round(v/units[node['output']])) for v in flatten(node.get('bias',[0]*node['channels']))]
        elif node['op']=='Requant':
            multipliers=[];shifts=[]
            mm=flatten(node['multiplier']);ss=flatten(node['shift']);count=max(len(mm),len(ss))
            if len(mm)==1:mm*=count
            if len(ss)==1:ss*=count
            if len(mm)!=len(ss):raise ValueError('PTQ parameter lengths')
            for m,shift in zip(mm,ss):
                if not 0<=m<2**31 or not -31<=shift<=31:raise ValueError('invalid source Q31 parameters')
                scale=m/2**31 * 2**shift * source_units/16
                fraction,exponent=math.frexp(scale)
                q=round(fraction*2**15)
                if q==2**15:q//=2;exponent+=1
                if not -31<=exponent<=31:raise ValueError('PTQ scale outside Q15 shift range')
                multipliers.append(q);shifts.append(exponent)
            lo,hi=node.get('clamp',[-128,127]);lo=max(-8,math.ceil(lo/16));hi=min(7,math.floor(hi/16))
            if lo>hi:raise ValueError('source clamp needs INT4 recalibration')
            node.update(multiplier=multipliers,shift=shifts,clamp=[lo,hi]);units[node['output']]=16
        else:units[node['output']]=source_units
    result.setdefault('metadata',{})['int4_conversion']=dict(method='uniform_ptq_div16',accuracy_validated=False,
        input_old_units=16,weight_old_units=16,tensor_old_units=units)
    if isinstance(result['metadata'].get('input_scale'),(float,int)):result['metadata']['input_scale']*=16
    result['metadata'].pop('fc_cmp_mult',None);result['metadata'].pop('fc_cmp_shift',None)
    return result

def flatten(values):
    if isinstance(values,(list,tuple)):
        return [x for v in values for x in flatten(v)]
    return [operator.index(values)]

def align(x,n=4):return (x+n-1)//n*n

@dataclass
class Tensor:
    shape:tuple[int,int,int]
    dtype:str
    @property
    def size(self):return math.prod(self.shape)*4 if self.dtype=='i32' else (math.prod(self.shape)+1)//2

@dataclass
class Package:
    image:bytes
    manifest:dict
    def save(self,directory):
        p=Path(directory);p.mkdir(parents=True,exist_ok=True)
        (p/'model.bin').write_bytes(self.image)
        (p/'manifest.json').write_text(json.dumps(self.manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    @classmethod
    def load(cls,directory):
        p=Path(directory)
        pkg=cls((p/'model.bin').read_bytes(),json.loads((p/'manifest.json').read_text(encoding='utf-8')))
        if pkg.manifest['abi']!=ABI or pkg.manifest.get('numeric_format')!=NUMERIC_FORMAT or \
           pkg.manifest.get('packing')!='low_nibble_first' or len(pkg.image)!=pkg.manifest['model_bytes']:
            raise ValueError('invalid package ABI/length')
        return pkg

def analyze(model):
    unknown=set(model)-{'input','nodes','output','metadata'}
    if unknown:raise ValueError(f'unknown model fields: {sorted(unknown)}')
    inp=model['input']
    unknown=set(inp)-{'name','shape','dtype','layout','zero_point','batch'}
    if unknown:raise ValueError(f'unknown input fields: {sorted(unknown)}')
    if inp.get('layout','NHWC')!='NHWC' or inp.get('zero_point',0)!=0 or inp.get('batch',1)!=1:
        raise ValueError('requires NHWC, zero_point=0 and batch=1')
    name=model['input']['name'];shape=tuple(operator.index(v) for v in model['input']['shape'])
    if len(shape)!=3 or min(shape)<=0 or shape[0]>255 or shape[1]>255 or shape[2]>1024:
        raise ValueError('input must be positive HWC within hardware limits')
    dtype=model['input'].get('dtype','i4')
    if dtype not in ('i4','i32'):raise ValueError('input dtype must be i4 or i32')
    tensors={name:Tensor(shape,dtype)}
    nodes=[]
    for original in model['nodes']:
        n=dict(original);op=n['op'];src=n['input'];dst=n['output']
        if op not in OPS or src not in tensors or dst in tensors:
            raise ValueError('unknown operator/input or duplicate output')
        fields={'Conv':{'channels','weights','bias','kernel','stride','pads','groups','dilation','weight_zero_point'},
                'FC':{'channels','weights','bias','kernel','stride','pads'},
                'MaxPool':{'kernel','stride','pads'},'ReLU':set(),
                'Requant':{'multiplier','shift','clamp','input_zero_point','output_zero_point'}}
        unknown=set(n)-({'op','input','output','metadata','name','layout','zero_point'}|fields[op])
        if unknown:raise ValueError(f'unknown {op} fields: {sorted(unknown)}')
        if n.get('layout','NHWC')!='NHWC' or any(n.get(key,0)!=0 for key in
            ('zero_point','weight_zero_point','input_zero_point','output_zero_point')):
            raise ValueError('only NHWC and zero-point-zero quantization are supported')
        if n.get('groups',1)!=1 or n.get('dilation',1) not in (1,[1,1],(1,1)):
            raise ValueError('grouped/dilated convolution is unsupported')
        t=tensors[src];h,w,c=t.shape
        if op in ('Conv','FC'):
            if t.dtype!='i4':raise ValueError('Conv/FC input must be i4; insert Requant')
            oc=operator.index(n['channels']);kernel=operator.index(n.get('kernel',3 if op=='Conv' else 1))
            stride=n.get('stride',1);pads=[operator.index(v) for v in n.get('pads',[0]*4)]
            steps=(stride,stride) if isinstance(stride,int) else tuple(stride)
            if len(steps)!=2:raise ValueError('stride must be integer or [SH,SW]')
            sh,sw=map(operator.index,steps)
            if kernel not in (1,3) or sh not in (1,2) or sw not in (1,2) or len(pads)!=4 or any(p<0 or p>=kernel for p in pads):
                raise ValueError('unsupported kernel/stride/padding')
            if op=='FC':
                if kernel!=1 or sh!=1 or sw!=1 or any(pads):raise ValueError('FC has no convolution geometry')
                h=w=1;c=math.prod(t.shape)
            oh=(h+pads[0]+pads[2]-kernel)//sh+1
            ow=(w+pads[1]+pads[3]-kernel)//sw+1
            k=kernel*kernel*c
            if not 1<=oc<=256 or k>1024 or not 1<=oh<=255 or not 1<=ow<=255:
                raise ValueError('Conv/FC shape exceeds capability')
            weights=flatten(n['weights']);bias=flatten(n.get('bias',[0]*oc))
            if len(weights)!=oc*k or len(bias)!=oc or any(v< -8 or v>7 for v in weights):
                raise ValueError('weights must be [OC,KH,KW,IC], FC [OC,K], signed i4')
            if any(v< -2**31 or v>=2**31 for v in bias):raise ValueError('bias outside i32')
            n.update(kernel=kernel,stride_h=sh,stride_w=sw,pads=pads,channels=oc,weights=weights,bias=bias,k=k,input_shape=(h,w,c))
            result=Tensor((oh,ow,oc),'i32')
        elif op=='MaxPool':
            if n.get('kernel',2)!=2 or n.get('stride',2)!=2 or n.get('pads',[0]*4)!=[0]*4:
                raise ValueError('MaxPool supports 2x2/stride2/no padding')
            if h<2 or w<2:raise ValueError('empty MaxPool output')
            result=Tensor((h//2,w//2,c),t.dtype)
        elif op=='ReLU':result=Tensor(t.shape,t.dtype)
        else:
            mult=flatten(n['multiplier']);shift=flatten(n['shift'])
            if len(mult)==1:mult*=c
            if len(shift)==1:shift*=c
            lo,hi=map(operator.index,n.get('clamp',[-8,7]))
            if len(mult)!=c or len(shift)!=c or any(v<0 or v>=2**15 for v in mult) or any(v< -31 or v>31 for v in shift) or not -8<=lo<=hi<=7:
                raise ValueError('invalid Requant parameters')
            n.update(multiplier=mult,shift=shift,clamp=[lo,hi])
            result=Tensor(t.shape,'i4')
        if result.shape[2]>256:raise ValueError('output channels exceed 256')
        tensors[dst]=result;nodes.append(n)
    output=model.get('output',nodes[-1]['output'] if nodes else '')
    if not nodes or output!=nodes[-1]['output']:raise ValueError('output must be final node')
    return tensors,nodes

def reference(model,values):
    """Execute logical nodes, without consulting fused descriptors or addresses."""
    tensors,nodes=analyze(model);values=[operator.index(v) for v in values]
    limit=2**31 if tensors[model['input']['name']].dtype=='i32' else 8
    if len(values)!=math.prod(tensors[model['input']['name']].shape) or any(v< -limit or v>=limit for v in values):
        raise ValueError('input length/range mismatch')
    data={model['input']['name']:values}
    for n in nodes:
        op=n['op'];a=data[n['input']];t=tensors[n['input']];h,w,c=t.shape;out=[]
        if op in ('Conv','FC'):
            h,w,c=n['input_shape'];oh,ow,oc=tensors[n['output']].shape
            for y in range(oh):
                for x in range(ow):
                    for o in range(oc):
                        acc=n['bias'][o]
                        for ky in range(n['kernel']):
                            for kx in range(n['kernel']):
                                iy=y*n['stride_h']+ky-n['pads'][0];ix=x*n['stride_w']+kx-n['pads'][1]
                                if 0<=iy<h and 0<=ix<w:
                                    for i in range(c):
                                        wi=o*n['k']+(ky*n['kernel']+kx)*c+i
                                        acc+=a[(iy*w+ix)*c+i]*n['weights'][wi]
                        out.append(wrap32(acc))
        elif op=='MaxPool':
            for y in range(h//2):
                for x in range(w//2):
                    for i in range(c):out.append(max(a[((2*y+dy)*w+2*x+dx)*c+i] for dy in range(2) for dx in range(2)))
        elif op=='ReLU':out=[max(0,v) for v in a]
        else:
            out=[requant(v,n['multiplier'][i%c],n['shift'][i%c],*n['clamp']) for i,v in enumerate(a)]
        data[n['output']]=out
    return data

def compile_model(model,*,fuse=True,cols=8,act_bytes=8192,model_bytes=8192,max_ops=16):
    tensors,nodes=analyze(model)
    if cols not in (4,8):raise ValueError('array columns must be 4 or 8')
    uses={name:0 for name in tensors}
    for n in nodes:uses[n['input']]+=1
    groups=[];i=0
    while i<len(nodes):
        g=[nodes[i]];i+=1
        if fuse and g[0]['op'] in ('Conv','FC'):
            seen=[]
            while i<len(nodes) and nodes[i]['input']==g[-1]['output'] and uses[g[-1]['output']]==1:
                candidate=nodes[i]['op']
                # Whitelist of monotone same-channel post chains. Never combine
                # two Requants, never move ReLU before a wrapping MAC bias.
                if candidate=='Requant' and not seen:pass
                elif candidate=='ReLU' and 'ReLU' not in seen and ('Requant' not in seen or nodes[i-1].get('clamp',[-8,7])[1]>=0):pass
                elif candidate=='MaxPool' and 'MaxPool' not in seen and g[0]['op']=='Conv':pass
                else:break
                if candidate=='Requant' and 'MaxPool' in seen:break
                g.append(nodes[i]);seen.append(candidate);i+=1
        groups.append(g)
    if len(groups)>max_ops:raise ValueError('too many physical operators')
    # First-fit, whole-tensor lifetime allocation. A destination is allocated
    # before releasing its source, so general in-place hazards cannot occur.
    live={model['input']['name']:(0,tensors[model['input']['name']].size)}
    allocation=dict(live);peak=align(tensors[model['input']['name']].size)
    last_use={name:-1 for name in tensors}
    for idx,g in enumerate(groups):last_use[g[0]['input']]=idx
    for idx,g in enumerate(groups):
        name=g[-1]['output'];size=tensors[name].size;at=0
        for base,length in sorted(live.values()):
            if at+size<=base:break
            at=align(max(at,base+length))
        if g[0]['op']=='ReLU' and last_use[g[0]['input']]==idx:
            at=allocation[g[0]['input']][0] # explicitly supported exact in-place point operation
        if at+size>act_bytes:raise ValueError(f'activation capacity exceeded: {name} needs [{at},{at+size}), capacity {act_bytes}; consider fusion/larger build')
        allocation[name]=(at,size);live[name]=(at,size);peak=max(peak,at+size)
        for old in list(live):
            if old!=name and last_use.get(old,-1)<=idx:del live[old]
    image=bytearray(len(groups)*64);records=[]
    def append(payload,alignment=4):
        while len(image)%alignment:image.append(0)
        at=len(image);image.extend(payload);return at
    for g in groups:
        n=g[0];op=n['op'];tin=tensors[n['input']];tout=tensors[g[-1]['output']]
        h,w,c=n.get('input_shape',tin.shape);oc=n.get('channels',c)
        flags=(S32 if tin.dtype=='i32' else 0)|(D32 if tout.dtype=='i32' else 0)
        q=next((v for v in g if v['op']=='Requant'),None)
        if op in ('Conv','FC'):
            if q:flags|=Q
            if any(v['op']=='ReLU' for v in g[1:]):flags|=R
            if any(v['op']=='MaxPool' for v in g[1:]):flags|=P
        weight_base=param_base=0
        if op in ('Conv','FC'):
            packed=[];k=n['k']
            for o_base in range(0,oc,cols):
                width=4 if cols==8 and oc-o_base<=4 else cols
                for ki in range(align(k,4)):
                    for lane in range(width):
                        o=o_base+lane
                        row_pad=n['kernel']==3 and n['input_shape'][2]==1
                        logical=(ki//4)*3+ki%4 if row_pad else ki
                        valid=ki%4<3 if row_pad else ki<k
                        packed.append(n['weights'][o*k+logical] if o<oc and valid else 0)
            weight_base=append(pack_i4(packed),4)
        if op in ('Conv','FC','Requant'):
            if op=='Requant':q=n
            params=[]
            for ch in range(oc):
                params.extend([n.get('bias',[0]*oc)[ch],(q['multiplier'][ch]|((q['shift'][ch]&63)<<16)) if q else 0])
            param_base=append(struct.pack('<'+'I'*len(params),*(x&0xffffffff for x in params)))
        kernel=n.get('kernel',0) if op in ('Conv','FC') else 0
        sh=n.get('stride_h',0);sw=n.get('stride_w',0)
        pads=n.get('pads',[0]*4) if op in ('Conv','FC') else [0]*4
        clamp=q['clamp'] if q else [0,0]
        words=[OPS[op]|flags,allocation[n['input']][0],allocation[g[-1]['output']][0],
               h|(w<<16),c|(oc<<16),kernel|(kernel<<8)|(sh<<16)|(sw<<24),
               sum(v<<(8*i) for i,v in enumerate(pads)),weight_base,param_base,
               (clamp[0]&15)|((clamp[1]&15)<<4),tout.shape[0]|(tout.shape[1]<<16),0,0,0,0,0]
        words[15]=FORMAT_MAGIC|cols
        records.append(words)
    while len(image)%4:image.append(0)
    if len(image)>model_bytes:raise ValueError(f'model needs {len(image)} bytes, capacity {model_bytes}')
    for i,words in enumerate(records):struct.pack_into('<16I',image,i*64,*words)
    out_name=groups[-1][-1]['output']
    manifest=dict(abi=ABI,numeric_format=NUMERIC_FORMAT,packing='low_nibble_first',cols=cols,model_bytes=len(image),op_count=len(groups),
                  input_elements=math.prod(tensors[model['input']['name']].shape),
                  input_base=0,input_bytes=tensors[model['input']['name']].size,input_dtype=tensors[model['input']['name']].dtype,
                  output_base=allocation[out_name][0],output_bytes=tensors[out_name].size,
                  output_elements=math.prod(tensors[out_name].shape),
                  output_dtype=tensors[out_name].dtype,output_shape=tensors[out_name].shape,
                  activation_peak=peak,groups=[[n['op'] for n in g] for g in groups],
                  tensors={name:dict(base=base,bytes=size,shape=tensors[name].shape,dtype=tensors[name].dtype)
                           for name,(base,size) in allocation.items()},metadata=model.get('metadata',{}))
    return Package(bytes(image),manifest)

def from_tinycnn_npz(path):
    """Import an INT8 training export through explicit, lossy /16 PTQ to INT4.

    This supplies an executable package; evaluate dataset accuracy separately.
    """
    import numpy as np
    with np.load(path,allow_pickle=False) as z:
        classes=int(z['num_classes']);nodes=[];src='input'
        for idx,c in ((1,1),(2,8)):
            packed=z[f'conv{idx}_w']
            if packed.shape!=(9*c,8):raise ValueError('unexpected packed training weights')
            weights=packed.T.reshape(8,3,3,c).tolist()
            nodes.extend([
                dict(op='Conv',input=src,output=f'conv{idx}',channels=8,kernel=3,pads=[1]*4,
                     weights=weights,bias=z[f'conv{idx}_bias'].tolist()),
                dict(op='Requant',input=f'conv{idx}',output=f'q{idx}',
                     multiplier=z[f'conv{idx}_mult'].tolist(),shift=z[f'conv{idx}_shift'].tolist()),
                dict(op='ReLU',input=f'q{idx}',output=f'relu{idx}'),
                dict(op='MaxPool',input=f'relu{idx}',output=f'pool{idx}')])
            src=f'pool{idx}'
        fc=z['fc_w']
        if fc.shape[0]!=160 or fc.shape[1]<classes:raise ValueError('unexpected FC training weights')
        nodes.append(dict(op='FC',input=src,output='logits',channels=classes,
                          weights=fc[:,:classes].T.tolist(),bias=z['fc_bias'][:classes].tolist()))
        metadata={key:z[key].tolist() for key in ('labels','fc_cmp_mult','fc_cmp_shift','input_scale') if key in z}
        return quantize_int8_model(dict(input=dict(name='input',shape=[20,16,1]),nodes=nodes,metadata=metadata))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('model',type=Path);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--no-fuse',action='store_true');ap.add_argument('--cols',type=int,default=8)
    ap.add_argument('--act-bytes',type=int,default=8192)
    args=ap.parse_args();model=from_tinycnn_npz(args.model) if args.model.suffix=='.npz' else json.loads(args.model.read_text(encoding='utf-8'))
    pkg=compile_model(model,fuse=not args.no_fuse,cols=args.cols,act_bytes=args.act_bytes);pkg.save(args.output)
    print(json.dumps(pkg.manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
