"""Packed INT4 model compiler and independent integer reference, ABI 5.0.

Pure Python, no framework/runtime dependency. Public input uses seven logical
operators (Conv incl. depthwise, FC, MaxPool, ReLU, Requant, AvgPool, Add).
Fusion only removes single-consumer, non-output intermediates, and each kind
of fusion can be switched off on its own.
"""
from __future__ import annotations
from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
import struct
import operator

# ABI 5.0: eight K rows, shared SRAM tail, up to 128 descriptors; packed signed INT4 tensors/weights, INT32 raw tensors and bias,
# Q15 multiplier + signed shift in one word, byte-addressed descriptors.
# 4.0 adds depthwise/rectangular convolution, general and average pooling,
# residual Add, a 1x2 fused pool and fused Requant/ReLU after pooling or Add.
ABI=0x50000
FORMAT_MAGIC=0x49340f80  # I4, Q15, eight rows; low nibble is array column count.
NUMERIC_FORMAT=0x0f200404
OPS={'Conv':1,'FC':2,'MaxPool':3,'ReLU':4,'Requant':5,'AvgPool':6,'Add':7}
Q,R,P,S32,D32,DW,P1D=1<<8,1<<9,1<<10,1<<11,1<<12,1<<13,1<<14
# u4: 4-bit tensors holding unsigned 0..15 (typically post-ReLU activations).
SU,SU2,DU=1<<15,1<<16,1<<17
NIBBLE=('i4','u4')
MAX_KERNEL=7
# Fusion kinds a head operator may absorb. A switch is either a bare kind
# ('requant', 'relu', 'maxpool') or one qualified by the head ('add.relu').
FUSE_KINDS=('requant','relu','maxpool')
FUSE_HEADS=('conv','dwconv','fc','maxpool','avgpool','add')

def wrap32(x):
    return (int(x)+2**31)%2**32-2**31

BIAS_BITS=18  # per-channel bias stored in the 32-bit parameter word

def q8(mult,shift):
    """The 8 significant multiplier bits the hardware keeps from a Q15 value:
    round to nearest; a carry to 256 renormalizes (or saturates at shift 31)."""
    m=(mult+64)>>7
    if m==256:
        if shift<31:m,shift=128,shift+1
        else:m=255
    return m,shift

def requant(x,mult,shift,lo=-8,hi=7):
    """Q15 multiplier rounded to Q8 (q8), saturating left shift for shift>0,
    one round-half-up of x*m/2^(8+max(-shift,0)), then clamp."""
    if not 0<=mult<2**15 or not -31<=shift<=31 or not -8<=lo<=hi<=15 or (lo<0 and hi>7):
        raise ValueError('invalid INT4/u4 Q15 parameters')
    m,shift=q8(mult,shift)
    x=max(-2**31,min(2**31-1,wrap32(x)*2**max(shift,0)))
    s=8+max(-shift,0)
    y=(x*m+2**(s-1))>>s
    return max(lo,min(hi,y))

def param_word(bias,mult,shift):
    """bias[17:0] | q8 multiplier[25:18] | shift[31:26]."""
    m,shift=q8(mult,shift) if mult else (0,0)
    return (bias&(2**BIAS_BITS-1))|(m<<18)|((shift&63)<<26)

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
    if dtype=='u4':
        if any(v<0 or v>15 for v in values):raise ValueError('value outside unsigned u4')
        return pack_i4([v-16 if v>7 else v for v in values])
    if dtype=='i32':return struct.pack('<'+'i'*len(values),*values)
    raise ValueError('tensor dtype must be i4 or i32')

def decode_tensor(payload,dtype,count):
    if dtype=='i4':return unpack_i4(payload,count)
    if dtype=='u4':return [v&15 for v in unpack_i4(payload,count)]
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

def pair(value,name):
    """An integer or [A,B] pair of positive integers."""
    v=(value,value) if isinstance(value,int) else tuple(value)
    if len(v)!=2:raise ValueError(f'{name} must be integer or [A,B]')
    return tuple(map(operator.index,v))

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
    if dtype not in ('i4','u4','i32'):raise ValueError('input dtype must be i4, u4 or i32')
    tensors={name:Tensor(shape,dtype)}
    nodes=[]
    for original in model['nodes']:
        n=dict(original);op=n['op'];src=n['input'];dst=n['output']
        if op not in OPS or src not in tensors or dst in tensors:
            raise ValueError('unknown operator/input or duplicate output')
        fields={'Conv':{'channels','weights','bias','kernel','stride','pads','groups','dilation','weight_zero_point'},
                'FC':{'channels','weights','bias','kernel','stride','pads'},
                'MaxPool':{'kernel','stride','pads'},'AvgPool':{'kernel','stride','pads'},'ReLU':set(),
                'Requant':{'multiplier','shift','clamp','dtype','input_zero_point','output_zero_point'},
                'Add':{'input2','weights'}}
        unknown=set(n)-({'op','input','output','metadata','name','layout','zero_point','fuse'}|fields[op])
        if unknown:raise ValueError(f'unknown {op} fields: {sorted(unknown)}')
        if n.get('layout','NHWC')!='NHWC' or any(n.get(key,0)!=0 for key in
            ('zero_point','weight_zero_point','input_zero_point','output_zero_point')):
            raise ValueError('only NHWC and zero-point-zero quantization are supported')
        if n.get('dilation',1) not in (1,[1,1],(1,1)):raise ValueError('dilated convolution is unsupported')
        t=tensors[src];h,w,c=t.shape
        n['inputs']=[src]
        if op in ('Conv','FC'):
            if t.dtype not in NIBBLE:raise ValueError('Conv/FC input must be i4/u4; insert Requant')
            oc=operator.index(n['channels'])
            kh,kw=pair(n.get('kernel',3 if op=='Conv' else 1),'kernel')
            sh,sw=pair(n.get('stride',1),'stride')
            pads=[operator.index(v) for v in n.get('pads',[0]*4)]
            groups=operator.index(n.get('groups',1))
            depthwise=groups!=1
            if depthwise and (op!='Conv' or groups!=c or oc!=c):
                raise ValueError('only depthwise grouping (groups=channels=input channels) is supported')
            if not (1<=kh<=MAX_KERNEL and 1<=kw<=MAX_KERNEL) or sh not in (1,2) or sw not in (1,2) or len(pads)!=4 or \
               any(p<0 for p in pads) or pads[0]>=kh or pads[2]>=kh or pads[1]>=kw or pads[3]>=kw:
                raise ValueError('unsupported kernel/stride/padding')
            if op=='FC':
                if (kh,kw)!=(1,1) or sh!=1 or sw!=1 or any(pads):raise ValueError('FC has no convolution geometry')
                h=w=1;c=math.prod(t.shape)
            oh=(h+pads[0]+pads[2]-kh)//sh+1
            ow=(w+pads[1]+pads[3]-kw)//sw+1
            k=kh*kw*(1 if depthwise else c)
            if not 1<=oc<=256 or k>1024 or not 1<=oh<=255 or not 1<=ow<=255:
                raise ValueError('Conv/FC shape exceeds capability')
            weights=flatten(n['weights']);bias=flatten(n.get('bias',[0]*oc))
            if len(weights)!=oc*k or len(bias)!=oc or any(v< -8 or v>7 for v in weights):
                raise ValueError('weights must be [OC,KH,KW,IC] (depthwise [C,KH,KW]), FC [OC,K], signed i4')
            if any(v< -2**(BIAS_BITS-1) or v>=2**(BIAS_BITS-1) for v in bias):
                raise ValueError(f'bias outside the {BIAS_BITS}-bit parameter field')
            n.update(kernel_h=kh,kernel_w=kw,stride_h=sh,stride_w=sw,pads=pads,channels=oc,weights=weights,bias=bias,k=k,
                     input_shape=(h,w,c),depthwise=depthwise)
            result=Tensor((oh,ow,oc),'i32')
        elif op in ('MaxPool','AvgPool'):
            kh,kw=pair(n.get('kernel',2),'kernel');sh,sw=pair(n.get('stride',[kh,kw]),'stride')
            if any(n.get('pads',[0]*4)):raise ValueError('pooling has no padding')
            if not (1<=kh<=255 and 1<=kw<=255 and 1<=sh<=255 and 1<=sw<=255):raise ValueError('pool window/stride')
            if h<kh or w<kw:raise ValueError('empty pool output')
            if op=='AvgPool' and t.dtype not in NIBBLE:raise ValueError('AvgPool sums i4/u4 inputs; insert Requant')
            n.update(kernel_h=kh,kernel_w=kw,stride_h=sh,stride_w=sw)
            # AvgPool yields the INT32 window sum; the 1/N scale belongs to a Requant.
            result=Tensor(((h-kh)//sh+1,(w-kw)//sw+1,c),'i32' if op=='AvgPool' else t.dtype)
        elif op=='Add':
            other=n.get('input2')
            if other not in tensors:raise ValueError('Add needs a known input2')
            if t.dtype not in NIBBLE or tensors[other].dtype not in NIBBLE or tensors[other].shape!=t.shape:
                raise ValueError('Add inputs must be i4/u4 tensors of one shape')
            wa,wb=map(operator.index,n.get('weights',[1,1]))
            if not (-128<=wa<=127 and -128<=wb<=127):raise ValueError('Add weights must be signed 8-bit')
            n.update(weights=[wa,wb]);n['inputs']=[src,other]
            result=Tensor(t.shape,'i32')
        elif op=='ReLU':result=Tensor(t.shape,t.dtype)
        else:
            mult=flatten(n['multiplier']);shift=flatten(n['shift'])
            if len(mult)==1:mult*=c
            if len(shift)==1:shift*=c
            out=n.get('dtype','i4')
            if out not in NIBBLE:raise ValueError('Requant output dtype must be i4 or u4')
            lo,hi=map(operator.index,n.get('clamp',[-8,7] if out=='i4' else [0,15]))
            limits=(-8,7) if out=='i4' else (0,15)
            if len(mult)!=c or len(shift)!=c or any(v<0 or v>=2**15 for v in mult) or any(v< -31 or v>31 for v in shift) or not limits[0]<=lo<=hi<=limits[1]:
                raise ValueError('invalid Requant parameters')
            n.update(multiplier=mult,shift=shift,clamp=[lo,hi])
            result=Tensor(t.shape,out)
        if result.shape[2]>256:raise ValueError('output channels exceed 256')
        tensors[dst]=result;nodes.append(n)
    output=model.get('output',nodes[-1]['output'] if nodes else '')
    if not nodes or output!=nodes[-1]['output']:raise ValueError('output must be final node')
    return tensors,nodes

def reference(model,values):
    """Execute logical nodes, without consulting fused descriptors or addresses."""
    tensors,nodes=analyze(model);values=[operator.index(v) for v in values]
    lo,hi={'i32':(-2**31,2**31),'i4':(-8,8),'u4':(0,16)}[tensors[model['input']['name']].dtype]
    if len(values)!=math.prod(tensors[model['input']['name']].shape) or any(v<lo or v>=hi for v in values):
        raise ValueError('input length/range mismatch')
    data={model['input']['name']:values}
    for n in nodes:
        op=n['op'];a=data[n['input']];t=tensors[n['input']];h,w,c=t.shape;out=[]
        if op in ('Conv','FC'):
            h,w,c=n['input_shape'];oh,ow,oc=tensors[n['output']].shape;kh,kw=n['kernel_h'],n['kernel_w']
            for y in range(oh):
                for x in range(ow):
                    for o in range(oc):
                        acc=n['bias'][o]
                        for ky in range(kh):
                            for kx in range(kw):
                                iy=y*n['stride_h']+ky-n['pads'][0];ix=x*n['stride_w']+kx-n['pads'][1]
                                if 0<=iy<h and 0<=ix<w:
                                    if n['depthwise']:
                                        acc+=a[(iy*w+ix)*c+o]*n['weights'][o*n['k']+ky*kw+kx]
                                    else:
                                        for i in range(c):
                                            acc+=a[(iy*w+ix)*c+i]*n['weights'][o*n['k']+(ky*kw+kx)*c+i]
                        out.append(wrap32(acc))
        elif op in ('MaxPool','AvgPool'):
            oh,ow,_=tensors[n['output']].shape;kh,kw=n['kernel_h'],n['kernel_w']
            for y in range(oh):
                for x in range(ow):
                    for i in range(c):
                        window=[a[((y*n['stride_h']+dy)*w+x*n['stride_w']+dx)*c+i] for dy in range(kh) for dx in range(kw)]
                        out.append(max(window) if op=='MaxPool' else wrap32(sum(window)))
        elif op=='Add':
            b=data[n['input2']];wa,wb=n['weights']
            out=[wrap32(wa*u+wb*v) for u,v in zip(a,b)]
        elif op=='ReLU':out=[max(0,v) for v in a]
        else:
            out=[requant(v,n['multiplier'][i%c],n['shift'][i%c],*n['clamp']) for i,v in enumerate(a)]
        data[n['output']]=out
    return data

def head_kind(node):
    if node['op']=='Conv':return 'dwconv' if node['depthwise'] else 'conv'
    return node['op'].lower()

def fuse_policy(fuse):
    """Normalize the fusion switch to a predicate on (head kind, fused kind)."""
    if fuse is True:return lambda head,kind:True
    if fuse is False or fuse is None:return lambda head,kind:False
    allowed=set(fuse)
    for item in allowed:
        head,_,kind=item.rpartition('.')
        if kind not in FUSE_KINDS or (head and head not in FUSE_HEADS):raise ValueError(f'unknown fusion switch {item!r}')
    return lambda head,kind:kind in allowed or f'{head}.{kind}' in allowed

def compile_model(model,*,fuse=True,cols=8,act_bytes=8192,model_bytes=8192,max_ops=128):
    """fuse: True, False, or an iterable of switches such as {'requant','relu','conv.maxpool'}.
    A node with "fuse": false is never absorbed into a preceding operator."""
    tensors,nodes=analyze(model)
    if cols!=8:raise ValueError('ABI 5 requires 8 array columns')
    if not 1<=max_ops<=128:raise ValueError('max_ops must be in 1..128')
    if act_bytes!=8192 or model_bytes!=8192:raise ValueError('ABI 5 requires two 8192-byte SRAMs')
    allow=fuse_policy(fuse)
    uses={name:0 for name in tensors}
    for n in nodes:
        for name in n['inputs']:uses[name]+=1
    groups=[];i=0
    while i<len(nodes):
        g=[nodes[i]];i+=1;head=head_kind(g[0])
        if g[0]['op'] in ('Conv','FC','MaxPool','AvgPool','Add'):
            seen=[]
            while i<len(nodes) and nodes[i]['input']==g[-1]['output'] and uses[g[-1]['output']]==1 and nodes[i].get('fuse',True):
                candidate=nodes[i]['op']
                # Whitelist of monotone same-channel post chains. Never combine
                # two Requants, never move ReLU before a wrapping MAC bias.
                pool=candidate=='MaxPool' and (nodes[i]['kernel_h'],nodes[i]['kernel_w'],nodes[i]['stride_h'],nodes[i]['stride_w']) in ((2,2,2,2),(1,2,1,2))
                if candidate=='Requant' and not seen and allow(head,'requant'):pass
                elif candidate=='ReLU' and 'ReLU' not in seen and allow(head,'relu') and \
                     ('Requant' not in seen or nodes[i-1].get('clamp',[-8,7])[1]>=0):pass
                elif pool and 'MaxPool' not in seen and g[0]['op']=='Conv' and allow(head,'maxpool'):pass
                else:break
                if candidate=='Requant' and 'MaxPool' in seen:break
                g.append(nodes[i]);seen.append(candidate);i+=1
        groups.append(g)
    if len(groups)>max_ops:raise ValueError('too many physical operators')
    for g in groups:
        if g[0]['op']=='Conv' and g[0]['depthwise'] and g[0]['channels']%cols:
            raise ValueError(f'depthwise channels must be a multiple of {cols} array columns')
    # First-fit, whole-tensor lifetime allocation. A destination is allocated
    # before releasing its sources, so general in-place hazards cannot occur.
    allocation={name:(0,tensor.size) for name,tensor in tensors.items()}
    image=bytearray(len(groups)*64);records=[]
    def append(payload,alignment=4):
        while len(image)%alignment:image.append(0)
        at=len(image);image.extend(payload);return at
    for g in groups:
        n=g[0];op=n['op'];tin=tensors[n['input']];tout=tensors[g[-1]['output']]
        h,w,c=n.get('input_shape',tin.shape);oc=n.get('channels',c)
        flags=(S32 if tin.dtype=='i32' else 0)|(D32 if tout.dtype=='i32' else 0)
        flags|=(SU if tin.dtype=='u4' else 0)|(DU if tout.dtype=='u4' else 0)
        if op=='Add' and tensors[n['input2']].dtype=='u4':flags|=SU2
        q=next((v for v in g if v['op']=='Requant'),None)
        if op!='Requant':
            if q:flags|=Q
            if any(v['op']=='ReLU' for v in g[1:]):flags|=R
            pool=next((v for v in g[1:] if v['op']=='MaxPool'),None)
            if pool:flags|=P|(P1D if pool['kernel_h']==1 else 0)
        if op=='Conv' and n['depthwise']:flags|=DW
        weight_base=param_base=0
        if op in ('Conv','FC') and n['depthwise']:
            # One row of COLS lane weights per tap; the COLS/8 K groups of the
            # tap reuse it and the array expands it to a block diagonal.
            packed=[];taps=n['kernel_h']*n['kernel_w']
            for o_base in range(0,oc,cols):
                for tap in range(taps):packed.extend(n['weights'][(o_base+lane)*taps+tap] for lane in range(cols))
            weight_base=append(pack_i4(packed),4)
        elif op in ('Conv','FC'):
            packed=[];k=n['k'];kw=n['kernel_w']
            # Single-channel kernels up to eight taps wide: one padded eight-row K group per
            # kernel row (one contiguous read each); see rowpad in the core.
            row_pad=n['input_shape'][2]==1 and kw<=8 and n['kernel_h']>1
            for o_base in range(0,oc,cols):
                width=4 if cols==8 and oc-o_base<=4 else cols
                for ki in range(n['kernel_h']*8 if row_pad else align(k,8)):
                    for lane in range(width):
                        o=o_base+lane
                        logical=(ki//8)*kw+ki%8 if row_pad else ki
                        valid=ki%8<kw if row_pad else ki<k
                        packed.append(n['weights'][o*k+logical] if o<oc and valid else 0)
            weight_base=append(pack_i4(packed),4)
        if op in ('Conv','FC','Requant') or q:
            if op=='Requant':q=n
            params=[param_word(n.get('bias',[0]*oc)[ch] if op in ('Conv','FC') else 0,
                               q['multiplier'][ch] if q else 0,q['shift'][ch] if q else 0) for ch in range(oc)]
            param_base=append(struct.pack('<'+'I'*len(params),*params))
        if op in ('Conv','FC','MaxPool','AvgPool'):
            geometry=n['kernel_h']|(n['kernel_w']<<8)|(n['stride_h']<<16)|(n['stride_w']<<24)
        else:geometry=0
        if op=='MaxPool' and (n['kernel_h'],n['kernel_w'],n['stride_h'],n['stride_w'])==(2,2,2,2):
            geometry=0 # canonical 2x2/stride-2 standalone pool keeps the ABI 3 encoding
        pads=n.get('pads',[0]*4) if op in ('Conv','FC') else [0]*4
        clamp=q['clamp'] if q else [0,0]
        words=[OPS[op]|flags,allocation[n['input']][0],allocation[g[-1]['output']][0],
               h|(w<<16),c|(oc<<16),geometry,
               sum(v<<(8*i) for i,v in enumerate(pads)),weight_base,param_base,
               (clamp[0]&31)|((clamp[1]&31)<<5),tout.shape[0]|(tout.shape[1]<<16),
               allocation[n['input2']][0] if op=='Add' else 0,
               ((n['weights'][0]&255)|((n['weights'][1]&255)<<8)) if op=='Add' else 0,0,0,0]
        words[15]=FORMAT_MAGIC|cols
        records.append(words)
    while len(image)%4:image.append(0)
    if len(image)>model_bytes:raise ValueError(f'model needs {len(image)} bytes, capacity {model_bytes}')
    if tensors[model['input']['name']].size>act_bytes+model_bytes-len(image):raise ValueError('input exceeds usable shared SRAM capacity')
    live={model['input']['name']:(0,tensors[model['input']['name']].size), '__model__':(act_bytes+model_bytes-len(image),len(image))}
    allocation={k:v for k,v in live.items() if k!='__model__'};peak=align(tensors[model['input']['name']].size)
    last_use={name:-1 for name in tensors}
    for idx,g in enumerate(groups):
        for name in g[0]['inputs']:last_use[name]=idx
    for idx,g in enumerate(groups):
        name=g[-1]['output'];size=tensors[name].size;at=0
        for base,length in sorted(live.values()):
            if at+size<=base:break
            at=align(max(at,base+length))
        # Exact in-place elementwise operations: ReLU, and Add onto an operand
        # of the same byte size whose last use is this Add.
        if g[0]['op']=='ReLU' and last_use[g[0]['input']]==idx:
            at=allocation[g[0]['input']][0]
        elif g[0]['op']=='Add':
            for operand in g[0]['inputs']:
                if last_use[operand]==idx and tensors[operand].size==size and operand!=model['input']['name']:
                    at=allocation[operand][0];break
        if at+size>act_bytes+model_bytes-len(image):raise ValueError(f'activation capacity exceeded: {name} needs [{at},{at+size}), usable {act_bytes+model_bytes-len(image)} bytes after model reservation; consider fusion or a smaller live tensor set')
        allocation[name]=(at,size);live[name]=(at,size);peak=max(peak,at+size)
        for old in list(live):
            if old not in (name,'__model__') and last_use.get(old,-1)<=idx:del live[old]
    for words,g in zip(records,groups):
        words[1]=allocation[g[0]['input']][0]
        words[2]=allocation[g[-1]['output']][0]
        if g[0]['op']=='Add':words[11]=allocation[g[0]['input2']][0]
    for i,words in enumerate(records):struct.pack_into('<16I',image,i*64,*words)
    out_name=groups[-1][-1]['output']
    manifest=dict(rows=8,activation_bank_bytes=act_bytes,shared_model_tail=True,abi=ABI,numeric_format=NUMERIC_FORMAT,packing='low_nibble_first',cols=cols,model_bytes=len(image),op_count=len(groups),
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
    ap.add_argument('--fuse',help='comma-separated fusion switches, e.g. requant,relu,conv.maxpool (default: all)')
    ap.add_argument('--act-bytes',type=int,default=8192)
    args=ap.parse_args();model=from_tinycnn_npz(args.model) if args.model.suffix=='.npz' else json.loads(args.model.read_text(encoding='utf-8'))
    fuse=False if args.no_fuse else (args.fuse.split(',') if args.fuse else True)
    pkg=compile_model(model,fuse=fuse,cols=args.cols,act_bytes=args.act_bytes);pkg.save(args.output)
    print(json.dumps(pkg.manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
