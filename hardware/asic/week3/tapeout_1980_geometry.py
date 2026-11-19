"""PDK-independent fixed tapeout geometry (dimensions in micrometres)."""
import math
from run_lab5_signoff import flow

CORE7T_SITE_W_UM = 0.560
CORE7T_SITE_H_UM = 3.920
SEALRING_BORDER_UM = 15.0
DEFAULT_SEALED_DIE_UM = 1980.0


def compute_fixed_die_geometry(sizes, *, target_sealed_um=1980.0,
                               layout='diagonal_facing', halo=26.0,
                               clearance_to_ref_um=0.200):
    if not math.isfinite(target_sealed_um) or clearance_to_ref_um < 0 or halo < 0:
        raise ValueError('Invalid geometry')
    ref = target_sealed_um-30
    die = flow.snap(ref-2*clearance_to_ref_um)
    cw = round(math.floor((die-300)/CORE7T_SITE_W_UM+1e-9)*CORE7T_SITE_W_UM, 3)
    ch = round(math.floor((die-300)/CORE7T_SITE_H_UM+1e-9)*CORE7T_SITE_H_UM, 3)
    mx, my = flow.snap((die-cw)/2), flow.snap((die-ch)/2)
    dw, dh = round(cw+2*mx, 3), round(ch+2*my, 3)
    if cw <= 0 or ch <= 0 or min(mx,my)<150 or max(dw,dh)>ref:
        raise ValueError('Die cannot accommodate core and margins')
    mw,mh = sizes['RA1SHD_2048x32M8']
    if layout in flow.ROTATED:
        mw,mh=mh,mw
    clear=max(halo,26)+20
    xlo,ylo=mx+clear,my+clear
    xhi,yhi=mx+cw-clear,my+ch-clear
    macros=[]
    for role,(x,y,o) in zip(flow.MACRO_ROLES, flow.macro_layout(layout,xlo,ylo,xhi,yhi,mw,mh,2*max(halo,26)+20)):
        if x<xlo or y<ylo or x+mw>xhi or y+mh>yhi:
            raise ValueError('Macros and halos do not fit')
        macros.append([role,flow.snap(x),flow.snap(y),mw,mh,o])
    corner=sizes['PCORNER'][0]
    pads=[]
    for side,signals in flow.SIGNALS.items():
        entries=[(n,'PDDW0204CDG',d) for n,d in signals]+[(n,c,'supply') for n,c in flow.SUPPLIES]
        length=dw if side in ('N','S') else dh
        step=(length-2*corner)/len(entries)
        for i,(name,cell,direction) in enumerate(entries):
            width,depth=sizes[cell]
            if step<width+1:
                raise ValueError('Overlapping pads')
            at=flow.snap(corner+(i+.5)*step-width/2)
            loc={'N':[at,dh-depth],'S':[at,0],'W':[0,at],'E':[dw-depth,at]}[side]
            pads.append(dict(name='i_pad_'+(name if direction!='supply' else side+'_'+name),port=name if direction!='supply' else None,cell=cell,direction=direction,side=side,loc=loc,orient={'N':'R180','S':'R0','W':'R270','E':'R90'}[side]))
    return dict(core_w_um=cw,core_h_um=ch,margin_x_um=mx,margin_y_um=my,die_w_um=dw,die_h_um=dh,die_side_um=dh,target_sealed_um=target_sealed_um,ref_inner_um=ref,macros=macros,pads=pads,corner_um=corner,halo_um=halo,bond_depth_um=sizes.get('PAD60LU_OBV',[60,73.16])[1])
