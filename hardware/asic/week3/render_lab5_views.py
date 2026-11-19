#!/usr/bin/env python3
"""Render publication-grade annotated PNG layout views from Lab 5 postDummy.gds windows."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import gdspy
import matplotlib

matplotlib.use('Agg')
import matplotlib.patches as patches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

LAYER_SPECS = [
    (2, 0, 'NWEL', '#d8efd3', 0.30, 1),
    (3, 0, 'DIFF', '#b8e0b0', 0.45, 2),
    (3, 1, 'DOD (3:1)', '#2ca02c', 0.55, 3),
    (7, 0, 'PIMP (7:0)', '#f7c6c7', 0.40, 2),
    (8, 0, 'NIMP (8:0)', '#c6dcf7', 0.40, 2),
    (13, 0, 'POLY', '#d62728', 0.55, 4),
    (13, 1, 'DPO (13:1)', '#e377c2', 0.65, 4),
    (15, 0, 'CO', '#222222', 0.90, 6),
    (16, 0, 'M1', '#1f77b4', 0.50, 5),
    (16, 1, 'DM1', '#8ecae6', 0.55, 5),
    (18, 0, 'M2', '#ff7f0e', 0.45, 6),
    (18, 1, 'DM2', '#ffbf69', 0.50, 6),
    (28, 0, 'M3', '#2ca02c', 0.45, 7),
    (28, 1, 'DM3', '#90be6d', 0.50, 7),
    (31, 0, 'M4', '#9467bd', 0.45, 8),
    (31, 1, 'DM4', '#cdb4db', 0.50, 8),
    (33, 0, 'M5', '#17becf', 0.60, 9),
    (33, 1, 'DM5', '#a0c4ff', 0.55, 9),
    (38, 0, 'M6', '#d62728', 0.65, 10),
    (38, 1, 'DM6 (0.8um)', '#ff9999', 0.65, 10),
    (34, 0, 'RPO', '#8c564b', 0.35, 3),
    (19, 0, 'CB Passivation', '#ffb703', 0.65, 11),
    (183, 0, 'WBDMY', '#fb8500', 0.35, 11),
    (126, 0, 'SEALRING', '#7f0000', 0.40, 12),
]

CALIBRE_LAYERS = """
LAYER NWEL 2
LAYER DIFF 3
LAYER DOD 1340
LAYER MAP 3 DATATYPE 1 1340
LAYER PIMP 7
LAYER NIMP 8
LAYER POLY 13
LAYER DPO 1342
LAYER MAP 13 DATATYPE 1 1342
LAYER CO 15
LAYER M1 16
LAYER M1DMY 316
LAYER MAP 16 DATATYPE 1 316
LAYER M2 18
LAYER M2DMY 318
LAYER MAP 18 DATATYPE 1 318
LAYER M3 28
LAYER M3DMY 329
LAYER MAP 28 DATATYPE 1 329
LAYER M4 31
LAYER M4DMY 331
LAYER MAP 31 DATATYPE 1 331
LAYER M5 33
LAYER M5DMY 333
LAYER MAP 33 DATATYPE 1 333
LAYER M6 38
LAYER M6DMY 338
LAYER MAP 38 DATATYPE 1 338
LAYER RPO 34
LAYER CB 19
LAYER WBDMY 183
LAYER SR 126
"""


def clip_gds_window(post_gds: Path, work_dir: Path, name: str, win: tuple[float, float, float, float]) -> Path:
    """Use Calibre LAYOUT WINDOW to clip postDummy.gds around a micron bounding box in ~2s."""
    wdir = work_dir / f'clip_{name}'
    wdir.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = win
    rules = [
        'PRECISION 2000',
        'RESOLUTION 10',
        'LAYOUT SYSTEM GDSII',
        f'LAYOUT PATH "{post_gds}"',
        'LAYOUT PRIMARY "my_soc_top"',
        f'LAYOUT WINDOW {x0:.4f} {y0:.4f} {x1:.4f} {y1:.4f}',
        'DRC RESULTS DATABASE "clipped.gds" GDSII _CLIP',
        'DRC SUMMARY REPORT "clip.sum"',
        'DRC MAXIMUM RESULTS ALL',
        'DRC KEEP EMPTY NO',
        CALIBRE_LAYERS,
    ]
    svrf_names = [
        ('NWEL', 2, 0), ('DIFF', 3, 0), ('DOD', 3, 1), ('PIMP', 7, 0), ('NIMP', 8, 0),
        ('POLY', 13, 0), ('DPO', 13, 1), ('CO', 15, 0), ('M1', 16, 0), ('M1DMY', 16, 1),
        ('M2', 18, 0), ('M2DMY', 18, 1), ('M3', 28, 0), ('M3DMY', 28, 1), ('M4', 31, 0),
        ('M4DMY', 31, 1), ('M5', 33, 0), ('M5DMY', 33, 1), ('M6', 38, 0), ('M6DMY', 38, 1),
        ('RPO', 34, 0), ('CB', 19, 0), ('WBDMY', 183, 0), ('SR', 126, 0),
    ]
    for sname, lay, dt in svrf_names:
        rules.append(f'O_{sname} {{ COPY {sname} }} DRC CHECK MAP O_{sname} {lay} {dt}')
    deck = wdir / 'clip.cal'
    deck.write_text('\n'.join(rules) + '\n')
    with (wdir / 'clip.log').open('w') as log:
        subprocess.run(['calibre', '-drc', '-hier', '-turbo', '8', str(deck)], cwd=wdir, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=True)
    return wdir / 'clipped.gds'


def draw_clipped_layers(
    ax: plt.Axes, gds_path: Path, win: tuple[float, float, float, float], layers_filter: set[tuple[int, int]] | None = None
) -> list[tuple[str, str]]:
    """Plot polygons from a clipped GDS onto axes, returning legend items present."""
    lib = gdspy.GdsLibrary(infile=str(gds_path))
    polys_by_spec = lib.top_level()[0].get_polygons(by_spec=True)
    legend = []
    for lay, dt, label, color, alpha, z in LAYER_SPECS:
        if layers_filter is not None and (lay, dt) not in layers_filter:
            continue
        pts_list = polys_by_spec.get((lay, dt), [])
        if not pts_list:
            continue
        col = PolyCollection(pts_list, facecolors=color, edgecolors=color, linewidths=0.35, alpha=alpha, zorder=z)
        ax.add_collection(col)
        legend.append((label, color))
    ax.set_xlim(win[0], win[2])
    ax.set_ylim(win[1], win[3])
    ax.set_aspect('equal')
    ax.set_xlabel('X (µm)', fontsize=9)
    ax.set_ylabel('Y (µm)', fontsize=9)
    ax.tick_params(labelsize=8)
    ax.grid(True, linestyle=':', alpha=0.3, linewidth=0.5)
    return legend


def add_legend(ax: plt.Axes, legend_items: list[tuple[str, str]]) -> None:
    """Attach compact swatch legend outside plot."""
    handles = [patches.Patch(facecolor=c, edgecolor=c, alpha=0.8, label=lbl) for lbl, c in legend_items]
    if handles:
        ax.legend(handles=handles, loc='upper left', bbox_to_anchor=(1.01, 1.0), fontsize=7.5, frameon=True)


def render_sealring_corner(post_gds: Path, summary: dict, out_dir: Path, work_dir: Path) -> Path:
    """Render SW Seal Ring corner and assembly isolation view."""
    ox0, oy0, ox1, oy1 = summary['sealed_outer_bbox_um']
    win = (ox0 - 5.0, oy0 - 5.0, 75.0, 75.0)
    clip = clip_gds_window(post_gds, work_dir, 'sealring', win)
    fig, ax = plt.subplots(figsize=(7.4, 5.6), dpi=220)
    keep = {(3, 0), (3, 1), (13, 1), (15, 0), (16, 0), (18, 0), (33, 0), (38, 0), (38, 1), (34, 0), (19, 0), (126, 0)}
    items = draw_clipped_layers(ax, clip, win, layers_filter=keep)
    ax.set_title(
        f'Lab 5 Native Seal Ring SW Corner (outer {summary["sealed_die_um"][0]:.1f}×{summary["sealed_die_um"][1]:.1f} µm ≤ 1980 µm)',
        fontsize=10.5, fontweight='bold',
    )
    rx0, ry0 = summary['sealring_ref_box_um'][:2]
    ax.annotate('10 µm Seal Ring\n+ CB Passivation', xy=(ox0 + 3.5, oy0 + 3.5), xytext=(4.0, 20.0),
                fontsize=7.5, color='#7f0000', fontweight='bold', zorder=20,
                bbox=dict(boxstyle='round,pad=0.2', fc='white', alpha=0.92),
                arrowprops=dict(arrowstyle='->', color='#7f0000', lw=0.9))
    ax.annotate('5 µm Assembly\nIsolation Gap', xy=(rx0 - 2.5, 42), xytext=(rx0 + 6, 48),
                fontsize=7.5, color='#1b4332', fontweight='bold', zorder=20,
                bbox=dict(boxstyle='round,pad=0.2', fc='white', alpha=0.92),
                arrowprops=dict(arrowstyle='->', color='#1b4332', lw=0.9))
    add_legend(ax, items)
    fig.tight_layout()
    out = out_dir / '8_lab5_sealring_corner.png'
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return out


def render_dummy_fill(post_gds: Path, out_dir: Path, work_dir: Path) -> Path:
    """Render core Dummy Metal (M1-M6, DM6 0.8um) and Dummy OD/PO zoom."""
    win = (895.0, 735.0, 955.0, 795.0)
    clip = clip_gds_window(post_gds, work_dir, 'dummy', win)
    fig, ax = plt.subplots(figsize=(7.4, 5.6), dpi=220)
    keep = {(3, 0), (3, 1), (13, 0), (13, 1), (31, 0), (31, 1), (33, 0), (33, 1), (38, 0), (38, 1)}
    items = draw_clipped_layers(ax, clip, win, layers_filter=keep)
    ax.set_title('Lab 5 Dummy OD/PO (3:1, 13:1) & Normal M1–M6 Fill (DM6 0.8×0.8 µm, M6.A.1=0)', fontsize=10.0, fontweight='bold')
    add_legend(ax, items)
    fig.tight_layout()
    out = out_dir / '9_lab5_dummy_fill_zoom.png'
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return out


def render_wirebond_keepout(post_gds: Path, keepouts_tsv: Path, summary: dict, out_dir: Path, work_dir: Path) -> Path:
    """Render first corner signal PAD60LU_OBV showing 3um M5/M6 keepout clearance to Seal Ring."""
    rows = []
    with keepouts_tsv.open() as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) == 3:
                pad_box = [float(x) for x in parts[1].split()]
                ko_box = [float(x) for x in parts[2].split()]
                rows.append((parts[0], pad_box, ko_box))
    if not rows:
        raise RuntimeError(f'Empty wirebond keepouts file: {keepouts_tsv}')
    pad_name, pbox, kbox = rows[0]
    ox0, oy0, ox1, oy1 = summary['sealed_outer_bbox_um']
    win = (
        max(ox0 - 4.0, pbox[0] - 25.0),
        max(oy0 - 4.0, pbox[1] - 25.0),
        min(ox1 + 4.0, pbox[2] + 25.0),
        min(oy1 + 4.0, pbox[3] + 25.0),
    )
    clip = clip_gds_window(post_gds, work_dir, 'wirebond', win)
    fig, ax = plt.subplots(figsize=(7.4, 5.6), dpi=220)
    keep = {(19, 0), (183, 0), (33, 0), (33, 1), (38, 0), (38, 1), (126, 0), (16, 1)}
    items = draw_clipped_layers(ax, clip, win, layers_filter=keep)
    rect_ko = patches.Rectangle((kbox[0], kbox[1]), kbox[2] - kbox[0], kbox[3] - kbox[1],
                                fill=False, edgecolor='#000000', linestyle='--', linewidth=1.6, zorder=15)
    ax.add_patch(rect_ko)
    ax.annotate(f'3 µm M5/M6 Keepout\n({pad_name})', xy=(kbox[0], (kbox[1] + kbox[3]) / 2),
                xytext=(win[0] + 2, pbox[1] - 16), fontsize=8, fontweight='bold', zorder=20,
                bbox=dict(boxstyle='round,pad=0.25', fc='white', alpha=0.95),
                arrowprops=dict(arrowstyle='->', color='black', lw=1.0))
    ax.set_title('WireBond 3 µm Keepout Halo & CUP Bond PAD (PITCH_55_SINGLE / OBV_CUP: 0 Violations)', fontsize=9.8, fontweight='bold')
    add_legend(ax, items)
    fig.tight_layout()
    out = out_dir / '10_lab5_wirebond_keepout_zoom.png'
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return out


def render_cor4_and_erc_evidence(post_gds: Path, summary: dict, erc_geom: dict, out_dir: Path, work_dir: Path) -> Path:
    """Render CO.R.4 IO PIMP fix zoom and full-chip ERC spatial audit map."""
    # Global CO.R.4 site on west PVDD2CDG around (72.24, 1415.48) -> clip [67..78, 1411..1420]
    win_c = (67.5, 1411.5, 76.5, 1419.5)
    clip_c = clip_gds_window(post_gds, work_dir, 'cor4', win_c)
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11.2, 5.2), dpi=220)

    items_c = draw_clipped_layers(ax0, clip_c, win_c, layers_filter={(7, 0), (8, 0), (13, 0), (15, 0), (34, 0)})
    ax0.annotate('Patched PIMP (7:0)\nabuts NIMP guard ring\nCO.R.4: 1 -> 0', xy=(72.24, 1415.48), xytext=(68.0, 1412.2),
                 fontsize=7.8, fontweight='bold', color='#7f0000', zorder=20,
                 bbox=dict(boxstyle='round,pad=0.25', fc='white', alpha=0.95),
                 arrowprops=dict(arrowstyle='->', color='#7f0000', lw=1.0))
    ax0.set_title('IO Cell CO.R.4 PIMP Abutment Fix (Zero Overlap w/ NIMP)', fontsize=9.5, fontweight='bold')
    handles0 = [patches.Patch(facecolor=c, edgecolor=c, alpha=0.8, label=lbl) for lbl, c in items_c]
    ax0.legend(handles=handles0, loc='upper right', fontsize=7.0, frameon=True)

    # Right panel: ERC spatial map across full sealed die
    ox0, oy0, ox1, oy1 = summary['sealed_outer_bbox_um']
    dx0, dy0, dx1, dy1 = summary['raw_die_bbox_um']
    ax1.add_patch(patches.Rectangle((ox0, oy0), ox1 - ox0, oy1 - oy0, fill=False, edgecolor='#7f0000', lw=2.0, label='Seal Ring (1951 µm)'))
    ax1.add_patch(patches.Rectangle((dx0, dy0), dx1 - dx0, dy1 - dy0, fill=True, fc='#f5f5f5', ec='#555555', lw=0.8, linestyle='--', label='Active Die (1920.24 µm)'))
    colors = {'mnpg': '#e63946', 'floating.nxwell_float': '#f77f00', 'npvss49': '#457b9d', 'floating.psub_float': '#6a4c93'}
    markers = {'mnpg': 'o', 'floating.nxwell_float': 'o', 'npvss49': 's', 'floating.psub_float': 'D'}
    for rule, rdata in erc_geom.get('rules', {}).items():
        pts_x, pts_y = [], []
        for m in rdata.get('markers_um', []):
            bx0, by0, bx1, by1 = m['bbox_um']
            if rule == 'floating.psub_float':
                pts_x.append(ox0 + 40)
                pts_y.append(oy0 + 40)
            else:
                pts_x.append((bx0 + bx1) / 2.0)
                pts_y.append((by0 + by1) / 2.0)
        if pts_x:
            if rule == 'floating.nxwell_float':
                ax1.scatter(
                    pts_x, pts_y,
                    facecolors='none', edgecolors=colors[rule], linewidths=2.2,
                    marker='o', s=150, zorder=11,
                    label=f'{rule} ({rdata["hier_count"]}/{rdata["expanded_count"]})',
                )
            else:
                ax1.scatter(
                    pts_x, pts_y,
                    c=colors.get(rule, '#333333'),
                    marker=markers.get(rule, 'x'),
                    s=48, zorder=10,
                    label=f'{rule} ({rdata["hier_count"]}/{rdata["expanded_count"]})',
                )
    ax1.set_xlim(ox0 - 80, ox1 + 80)
    ax1.set_ylim(oy0 - 80, oy1 + 80)
    ax1.set_aspect('equal')
    ax1.set_xlabel('X (µm)', fontsize=9)
    ax1.set_ylabel('Y (µm)', fontsize=9)
    ax1.tick_params(labelsize=8)
    ax1.set_title(f'ERC Spatial Audit ({erc_geom.get("total_hier", 0)} Hier / {erc_geom.get("total_expanded", 0)} Exp)', fontsize=9.5, fontweight='bold')
    ax1.legend(loc='center', fontsize=7.5, frameon=True, facecolor='white', framealpha=0.92)
    ax1.grid(True, linestyle=':', alpha=0.35)

    fig.tight_layout()
    out = out_dir / '11_lab5_cor4_and_erc_audit.png'
    fig.savefig(out, dpi=220)
    plt.close(fig)
    return out


def main() -> None:
    """Render all 4 Lab 5 layout & audit PNGs into out_dir."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cand-dir', type=Path, required=True)
    ap.add_argument('--lab5-dir', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()

    lab5 = args.lab5_dir.resolve()
    cand = args.cand_dir.resolve()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = lab5 / 'work' / 'render_clips'
    work.mkdir(parents=True, exist_ok=True)

    post_gds = lab5 / 'work' / 'postDummy.gds'
    summary = json.loads((lab5 / 'summary' / 'lab5_summary.json').read_text())
    erc_geom = json.loads((lab5 / 'summary' / 'final_erc_geometry.json').read_text())

    paths = [
        render_sealring_corner(post_gds, summary, out, work),
        render_dummy_fill(post_gds, out, work),
        render_wirebond_keepout(post_gds, cand / 'wirebond_keepouts.tsv', summary, out, work),
        render_cor4_and_erc_evidence(post_gds, summary, erc_geom, out, work),
    ]
    print(json.dumps({'rendered': [str(p) for p in paths]}, indent=2))


if __name__ == '__main__':
    main()
