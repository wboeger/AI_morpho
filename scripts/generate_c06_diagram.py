"""
C06 Shaft angle diagram — real hook outlines, Procrustes-normalised.

Orientation: rotate so the Base endpoints chord is horizontal
  Base[-1] (Heel-side / proximal) → LEFT
  Base[0]  (Shaft-side / distal)  → RIGHT

Internal angle shown = C06 = angle between
  • the inward continuation of the Base midline (−v_base, pointing rightward through fork)
  • the Shaft midline (v_shaft, pointing toward Point)
"""
import os, sys, math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Arc
import matplotlib.patches as mpatches

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import create_app
from app.models import CharacterDefinition, CharacterValue, Structure, Specimen
from app.geometry import _central_axis, _midline_vector
from app.procrustes import scale_to_unit

OUT = os.path.join(os.path.dirname(__file__),
                   '..', 'app', 'static', 'diagrams', 'c06_shaft_angle.png')

COL_OUTLINE = '#90a4ae'
COL_SHAFT   = '#1565c0'
COL_BASE    = '#e65100'
COL_SHAFT_L = '#1e88e5'   # shaft midline
COL_BASE_L  = '#fb8c00'   # base midline
COL_ARC     = '#c62828'
COL_BASELINE= '#b0bec5'
COL_BG      = '#f8fafc'
COL_BADGE   = '#1a4e78'

# STATES is built at runtime from the database — see main block below.


# ── Procrustes normalisation ─────────────────────────────────────────────────

def base_endpoints(lm, bnd):
    bi = bnd['Base']
    return lm[bi[-1]], lm[bi[0]]   # (proximal/heel-side, distal/shaft-side)


def procrustes_orient(lm, bnd):
    """Centre, unit-scale, rotate so Base chord is horizontal (proximal left)."""
    lm = scale_to_unit(lm)
    proximal, distal = base_endpoints(lm, bnd)
    v   = distal - proximal
    ang = -math.atan2(v[1], v[0])
    c, s = math.cos(ang), math.sin(ang)
    lm   = (np.array([[c, -s], [s, c]]) @ lm.T).T
    # translate so proximal Base endpoint at origin
    proximal, _ = base_endpoints(lm, bnd)
    lm -= proximal
    # flip y so Shaft body arches upward
    si = bnd.get('Shaft', [])
    if si and lm[si].mean(axis=0)[1] < 0:
        lm[:, 1] *= -1
    return lm


# ── Helpers ──────────────────────────────────────────────────────────────────

def compute_fork_and_axes(lm, bnd):
    """
    Return (fork, v_shaft, v_base_chord).

    fork         = distal endpoint of Base (Base[0]), where Base meets Shaft
    v_shaft      = Shaft midline direction from fork (toward Point tip)
    v_base_chord = unit vector along the Base endpoints chord, pointing
                   outward from fork toward the proximal (Heel-side) end
                   i.e.  (lm[Base[-1]] − lm[Base[0]]) normalised
    """
    si, bi = bnd['Shaft'], bnd['Base']

    # Fork = distal Base endpoint (where Base joins Shaft)
    fork = lm[bi[0]].copy()

    # Base chord direction: from distal endpoint toward proximal endpoint
    chord = lm[bi[-1]] - lm[bi[0]]
    v_ba_chord = chord / np.linalg.norm(chord)

    # Shaft midline direction from the fork
    ax = _central_axis(lm, si, ref_point=fork)
    if len(ax) < 2:
        return fork, None, v_ba_chord
    if np.linalg.norm(ax[0] - fork) > np.linalg.norm(ax[-1] - fork):
        ax = ax[::-1]
    half = max(2, len(ax) // 2)
    v_sh = _midline_vector(ax[:half])
    v_sh = v_sh / np.linalg.norm(v_sh)

    return fork, v_sh, v_ba_chord


# ── Drawing ───────────────────────────────────────────────────────────────────

def draw_panel(ax, lm, bnd, species_name, raw_value,
               state_code, state_name, thresh_label):

    fork, v_sh, v_ba = compute_fork_and_axes(lm, bnd)
    proximal, distal = base_endpoints(lm, bnd)

    # ── Full outline (thin grey) ─────────────────────────────────────
    ax.plot(lm[:, 0], lm[:, 1],
            color=COL_OUTLINE, lw=1.2, zorder=1, solid_capstyle='round')

    # ── Base part (thick, orange) ────────────────────────────────────
    bi = bnd.get('Base', [])
    if bi:
        bp = lm[bi]
        ax.plot(bp[:, 0], bp[:, 1],
                color=COL_BASE, lw=3.5, solid_capstyle='round', zorder=2)

    # ── Shaft part (thick, blue) ─────────────────────────────────────
    si = bnd.get('Shaft', [])
    if si:
        sp = lm[si]
        ax.plot(sp[:, 0], sp[:, 1],
                color=COL_SHAFT, lw=3.5, solid_capstyle='round', zorder=2)

    # ── Base endpoints baseline (dashed grey) ────────────────────────
    ax.plot([proximal[0], distal[0]], [proximal[1], distal[1]],
            color=COL_BASELINE, lw=1.0, linestyle='--', zorder=3, alpha=0.7)
    for pt in (proximal, distal):
        ax.plot(*pt, 'D', color=COL_BASELINE, ms=3.5, zorder=4,
                markeredgecolor='#546e7a', markeredgewidth=0.5)

    if v_sh is None:
        return

    # ── Base chord line through fork ──────────────────────────────────
    # The chord passes through Base[0] (fork) toward Base[-1] (proximal/Heel).
    # Extend outward toward Heel AND inward (continuation through fork).
    reach_far  = 0.55
    reach_back = 0.30

    p_ba_out = fork + reach_far  * v_ba   # toward Heel (outward)
    p_ba_in  = fork - reach_back * v_ba   # inward continuation through fork

    ax.plot([p_ba_in[0], p_ba_out[0]], [p_ba_in[1], p_ba_out[1]],
            color=COL_BASE_L, lw=1.8, linestyle='-', zorder=5,
            solid_capstyle='round')
    # Arrowhead at the Heel end
    ax.annotate('', xy=p_ba_out, xytext=fork + (reach_far - 0.12) * v_ba,
                arrowprops=dict(arrowstyle='->', color=COL_BASE_L, lw=1.5),
                zorder=6)

    # ── Shaft midline: drawn after possible flip in angle-arc section ──
    # (placeholder — actual draw happens after v_sh is corrected below)

    # ── Internal angle arc ────────────────────────────────────────────
    # Ensure v_sh points into the upper half (interior of hook).
    # If it points downward the proximal Shaft midline exits the fork
    # backward; flip it and use 180°−raw_value as the display angle.
    display_angle = raw_value
    if v_sh[1] < 0:
        v_sh = -v_sh
        display_angle = 180.0 - raw_value

    # Draw Shaft arrow now that v_sh is finalised
    p_sh_tip = fork + reach_far * v_sh
    ax.annotate('', xy=p_sh_tip, xytext=fork,
                arrowprops=dict(arrowstyle='->', color=COL_SHAFT_L, lw=1.5),
                zorder=8)
    ax.plot([fork[0], p_sh_tip[0]], [fork[1], p_sh_tip[1]],
            color=COL_SHAFT_L, lw=1.8, zorder=8, solid_capstyle='round')

    v_ba_in = -v_ba   # inward continuation of Base chord through fork
    a_in = math.degrees(math.atan2(v_ba_in[1], v_ba_in[0]))
    a_sh = math.degrees(math.atan2(v_sh[1], v_sh[0]))

    ccw = (a_sh - a_in) % 360
    if ccw <= 180:
        theta1, theta2 = a_in, a_in + ccw
    else:
        theta1, theta2 = a_sh, a_sh + (360 - ccw)

    arc_r = 0.18
    arc = Arc(fork, 2 * arc_r, 2 * arc_r,
              angle=0, theta1=theta1, theta2=theta2,
              color=COL_ARC, lw=2.0, zorder=7)
    ax.add_patch(arc)

    mid_a = math.radians((theta1 + theta2) / 2)
    lx = fork[0] + (arc_r + 0.14) * math.cos(mid_a)
    ly = fork[1] + (arc_r + 0.14) * math.sin(mid_a)
    ax.text(lx, ly, f'{display_angle:.0f}°',
            ha='center', va='center', fontsize=9.5,
            color=COL_ARC, fontweight='bold', zorder=8)

    # Fork dot
    ax.plot(*fork, 'o', color=COL_ARC, ms=5.5, zorder=9,
            markeredgecolor='white', markeredgewidth=0.8)

    # ── Panel cosmetics ───────────────────────────────────────────────
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor('#dde3ea')
        sp.set_linewidth(0.9)
    ax.set_facecolor('white')

    ax.set_title(thresh_label, fontsize=11, color='#475569', pad=6)

    short = (species_name
             .replace('Gyrodactylus', 'G.')
             .replace('Gyrodactyloides', 'Gd.')
             .replace('Macrogyrodactylus', 'M.')
             .replace('Afrogyrodactylus', 'A.')
             .replace('Diechodactylus', 'D.')
             .replace('Ieredactylus', 'I.')
             .replace('Scleroductus', 'Sc.'))
    ax.text(0.5, 0.02,
            r'$\it{' + short.replace(' ', r'\ ') + r'}$',
            ha='center', va='bottom', transform=ax.transAxes,
            fontsize=8, color='#334155')

    ax.text(0.5, -0.11,
            f'State {state_code}:  {state_name}',
            ha='center', va='top', transform=ax.transAxes,
            fontsize=9.5, color='white', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.35', facecolor=COL_BADGE,
                      edgecolor='none'))


# ── Main ──────────────────────────────────────────────────────────────────────

app = create_app()

with app.app_context():
    c06 = CharacterDefinition.query.filter_by(code='C06').first()

    # Build state metadata from the DB definition
    def thresh_label(s):
        lo = s.get('threshold_min')
        hi = s.get('threshold_max')
        if lo is None:
            return f'< {hi:.0f}°'
        if hi is None:
            return f'> {lo:.0f}°'
        return f'{lo:.0f} – {hi:.0f}°'

    def state_midpoint(s, fallback_vals):
        lo = s.get('threshold_min')
        hi = s.get('threshold_max')
        if lo is None and hi is not None:
            return hi * 0.6
        if hi is None and lo is not None:
            return lo * 1.2
        return (lo + hi) / 2

    all_vals = (CharacterValue.query
                .filter_by(character_id=c06.id)
                .filter(CharacterValue.state != None)
                .all())
    by_state = {}
    for v in all_vals:
        by_state.setdefault(v.state, []).append(v)

    # Collapse all states after the first two into a single third panel
    # so the figure always shows exactly 3 panels.
    state_defs = c06.states_json
    panels = []

    # Panel 0 and 1: first two states as-is
    for s in state_defs[:2]:
        vlist = by_state.get(s['code'], [])
        if not vlist:
            continue
        ideal = state_midpoint(s, vlist)
        panels.append((s['code'], s['name'], thresh_label(s), vlist, ideal))

    # Panel 2: merge remaining states (codes 2, 3, …) into one panel
    remaining_codes = [s['code'] for s in state_defs[2:]]
    remaining_vals  = [v for c in remaining_codes for v in by_state.get(c, [])]
    if remaining_vals:
        lo_thresh = state_defs[2].get('threshold_min')
        label3    = f'> {lo_thresh:.0f}°' if lo_thresh is not None else '> ?'
        name3     = state_defs[-1]['name']
        raws3     = sorted(v.raw_value for v in remaining_vals if v.raw_value)
        ideal3    = raws3[len(raws3) // 2]   # median raw value
        panels.append((remaining_codes[0], name3, label3, remaining_vals, ideal3))

    specimens = []
    for code, name, label, vlist, ideal in panels:
        v      = min(vlist, key=lambda x: abs((x.raw_value or 999) - ideal))
        struct = Structure.query.get(v.structure_id)
        spec   = Specimen.query.get(struct.specimen_id)
        lm_raw = np.array(struct.landmarks_json, dtype=float)
        bnd    = struct.boundary_json or {}
        specimens.append((code, name, label, v.raw_value,
                          spec.species_name, lm_raw, bnd))

    n_panels = len(specimens)
    fig, axes = plt.subplots(1, n_panels, figsize=(n_panels * 3.6, 5.4))
    fig.patch.set_facecolor(COL_BG)

    pad = 0.20
    ax_list = axes if n_panels > 1 else [axes]
    for ax, (code, name, thresh, raw, species, lm_raw, bnd) in zip(ax_list, specimens):
        lm = procrustes_orient(lm_raw, bnd)
        xs, ys = lm[:, 0], lm[:, 1]
        ax.set_xlim(xs.min() - pad, xs.max() + pad)
        ax.set_ylim(ys.min() - pad, ys.max() + pad)
        draw_panel(ax, lm, bnd, species, raw, code, name, thresh)

    # Legend
    handles = [
        mpatches.Patch(color=COL_SHAFT,   label='Shaft landmarks'),
        mpatches.Patch(color=COL_BASE,    label='Base landmarks'),
        plt.Line2D([0],[0], color=COL_SHAFT_L, lw=1.6, label='Shaft midline direction'),
        plt.Line2D([0],[0], color=COL_BASE_L,  lw=1.6, label='Base endpoint chord (direction)'),
        plt.Line2D([0],[0], color=COL_ARC,     lw=2.0, label='Internal angle (C06)'),
        plt.Line2D([0],[0], color=COL_BASELINE, lw=1.0,
                   linestyle='--', label='Base endpoints (orientation baseline)'),
    ]
    fig.legend(handles=handles, loc='lower center', ncol=3,
               frameon=False, fontsize=9, bbox_to_anchor=(0.5, -0.08))

    fig.suptitle(
        'C06 — Shaft angle  ·  internal angle between Shaft and Base midline directions  ·  hook',
        fontsize=12, fontweight='bold', color='#1a4e78', y=1.02)

    plt.tight_layout(pad=0.9)
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    fig.savefig(OUT, dpi=150, bbox_inches='tight', facecolor=COL_BG)
    plt.close()
    print(f'Wrote {os.path.abspath(OUT)}')
