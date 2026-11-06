"""Batched integer reference for INT4 models (PyTorch, CPU or GPU).

Bit-exact with npu_program.reference(): MAC/pool sums run in float64, which is
exact for these ranges (|value| < 2**53), and Requant reproduces the hardware
rounding with int64 arithmetic. Used to score whole test sets.
"""
from __future__ import annotations
import torch
import torch.nn.functional as F
from npu_program import analyze

I32_MIN,I32_MAX=-2**31,2**31-1


def wrap32(x):
    return torch.remainder(x+2**31,2**32)-2**31


def requant(x,mult,shift,lo,hi):
    """x: int64 [..., C]; Q15 mult/shift: int64 [C]. Matches npu_program.requant."""
    m=(mult+64)//128
    renorm=(m==256)&(shift<31)
    m=torch.where(renorm,torch.full_like(m,128),torch.clamp(m,max=255))
    shift=torch.where(renorm,shift+1,shift)
    x=wrap32(x)
    x=torch.clamp(x*torch.pow(2,torch.clamp(shift,min=0)),I32_MIN,I32_MAX)
    s=8+torch.clamp(-shift,min=0)
    y=torch.div(x*m+torch.pow(2,s-1),torch.pow(2,s),rounding_mode='floor')
    return torch.clamp(y,lo,hi)


def run(model,frames,device='cpu',keep=False):
    """frames: int tensor [N,H,W,C]. Returns the output tensor [N,...] (int64),
    or every tensor when keep=True."""
    tensors,nodes=analyze(model)
    x=torch.as_tensor(frames,dtype=torch.int64,device=device)
    data={model['input']['name']:x}
    for n in nodes:
        a=data[n['input']];op=n['op']
        if op in ('Conv','FC'):
            h,w,c=n['input_shape']
            a=a.reshape(a.shape[0],h,w,c)
            oc=n['channels'];kh,kw=n['kernel_h'],n['kernel_w']
            inp=a.permute(0,3,1,2).double()
            inp=F.pad(inp,(n['pads'][1],n['pads'][3],n['pads'][0],n['pads'][2]))
            wt=torch.tensor(n['weights'],dtype=torch.float64,device=device)
            if n['depthwise']:
                wt=wt.reshape(oc,1,kh,kw);groups=oc
            else:
                wt=wt.reshape(oc,kh,kw,c).permute(0,3,1,2);groups=1
            out=F.conv2d(inp,wt,stride=(n['stride_h'],n['stride_w']),groups=groups)
            out=torch.round(out).long()+torch.tensor(n['bias'],dtype=torch.int64,device=device).view(1,-1,1,1)
            data[n['output']]=wrap32(out.permute(0,2,3,1))
        elif op in ('MaxPool','AvgPool'):
            inp=a.permute(0,3,1,2).double()
            k=(n['kernel_h'],n['kernel_w']);s=(n['stride_h'],n['stride_w'])
            out=F.max_pool2d(inp,k,s) if op=='MaxPool' else F.avg_pool2d(inp,k,s,divisor_override=1)
            data[n['output']]=wrap32(torch.round(out).long().permute(0,2,3,1))
        elif op=='Add':
            wa,wb=n['weights']
            data[n['output']]=wrap32(wa*a+wb*data[n['input2']])
        elif op=='ReLU':
            data[n['output']]=torch.clamp(a,min=0)
        else:
            c=a.shape[-1]
            mult=torch.tensor(n['multiplier'],dtype=torch.int64,device=device)
            shift=torch.tensor(n['shift'],dtype=torch.int64,device=device)
            data[n['output']]=requant(a,mult,shift,*n['clamp'])
    return data if keep else data[nodes[-1]['output']]
