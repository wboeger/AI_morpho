"""
Generate PNG diagram images for 13 missing characters:
  Anchors: A01, A03, A04
  Hooks:   C01, C02, C03, C05, C07, C08, C09, C10, C11, C12

Orientation:
  Hooks:   rotate so Base chord horizontal (Base[-1] left, Base[0] right);
           flip y if Shaft goes downward
  Anchors: rotate so Shaft endpoints chord horizontal (Shaft[-1] left, Shaft[0] right);
           flip y if SuperficialRoot/Point goes downward
"""
import os, sys, math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Arc
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import create_app
from app.models import CharacterDefinition, CharacterValue, Structure, Specimen
from app.geometry import _central_axis, _midline_vector, arc_length
from app.procrustes import scale_to_unit

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'app', 'static', 'diagrams')
os.makedirs(os.path.abspath(OUT_DIR), exist_ok=True)

# ── Colour palette ────────────────────────────────────────────────────────────
COL_OUTLINE  = '#90a4ae'
COL_PARTА    = '#1565c0'   # part A (blue)
COL_PARTB    = '#e65100'   # part B (orange)
COL_CHORD    = '#6a1b9a'   # chord line
COL_ARC_C    = '#c62828'   # angle arc
COL_BG       = '#f8fafc'
COL_BADGE    = '#1a4e78'
COL_SINUOSITY= '#2e7d32'   # sinuosity part highlight
COL_PRESENCE = '#ad1457'   # presence-threshold highlight

# ── Procrustes orientation ────────────────────────────────────────────────────

def _arc_len(lm, indices):
    """Arc length along a sequence of landmark indices."""
    pts = lm[indices]
    if len(pts) < 2:
        return 0.0
    return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))


def procrustes_hook(lm, bnd):
    """Rotate so Base chord is horizontal; Base[-1] left, Base[0] right.
    Flip y if Shaft body goes downward."""
    lm = scale_to_unit(lm)
    bi = bnd.get('Base', [])
    if not bi:
        return lm
    proximal, distal = lm[bi[-1]], lm[bi[0]]
    v = distal - proximal
    ang = -math.atan2(v[1], v[0])
    c, s = math.cos(ang), math.sin(ang)
    lm = (np.array([[c, -s], [s, c]]) @ lm.T).T
    # translate so proximal Base endpoint at origin
    lm -= lm[bi[-1]]
    # flip y if Shaft goes downward
    si = bnd.get('Shaft', [])
    if si and lm[si].mean(axis=0)[1] < 0:
        lm[:, 1] *= -1
    return lm


def procrustes_anchor(lm, bnd):
    """Rotate so Shaft chord is horizontal; Shaft[-1] left, Shaft[0] right.
    Flip y if SuperficialRoot or Point goes upward (towards positive y)."""
    lm = scale_to_unit(lm)
    si = bnd.get('Shaft', [])
    if not si:
        return lm
    p_left, p_right = lm[si[-1]], lm[si[0]]
    v = p_right - p_left
    ang = -math.atan2(v[1], v[0])
    c, s = math.cos(ang), math.sin(ang)
    lm = (np.array([[c, -s], [s, c]]) @ lm.T).T
    lm -= lm[si[-1]]
    # flip y so SuperficialRoot or Point goes upward (positive y)
    for part in ('SuperficialRoot', 'Point', 'DeepRoot'):
        idx = bnd.get(part, [])
        if idx:
            if lm[idx].mean(axis=0)[1] < 0:
                lm[:, 1] *= -1
            break
    return lm


# ── Panel cosmetics ───────────────────────────────────────────────────────────

def _shorten_species(name):
    return (name
            .replace('Gyrodactylus', 'G.')
            .replace('Gyrodactyloides', 'Gd.')
            .replace('Macrogyrodactylus', 'M.')
            .replace('Afrogyrodactylus', 'A.')
            .replace('Diechodactylus', 'D.')
            .replace('Ieredactylus', 'I.')
            .replace('Scleroductus', 'Sc.'))


def _panel_cosmetics(ax, species_name, state_code, state_name, thresh_label):
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor('#dde3ea')
        sp.set_linewidth(0.9)
    ax.set_facecolor('white')
    ax.set_title(thresh_label, fontsize=10.5, color='#475569', pad=5)
    short = _shorten_species(species_name)
    ax.text(0.5, 0.02,
            r'$\it{' + short.replace(' ', r'\ ') + r'}$',
            ha='center', va='bottom', transform=ax.transAxes,
            fontsize=8, color='#334155')
    ax.text(0.5, -0.11,
            f'State {state_code}:  {state_name}',
            ha='center', va='top', transform=ax.transAxes,
            fontsize=9, color='white', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor=COL_BADGE, edgecolor='none'))


def _set_limits(ax, lm, pad=0.20):
    ax.set_xlim(lm[:, 0].min() - pad, lm[:, 0].max() + pad)
    ax.set_ylim(lm[:, 1].min() - pad, lm[:, 1].max() + pad)


# ── thresh label helper ───────────────────────────────────────────────────────

def _thresh_str(s, unit=''):
    lo, hi = s.get('threshold_min'), s.get('threshold_max')
    u = f' {unit}' if unit else ''
    if lo is None and hi is not None:
        return f'< {hi}{u}'
    if hi is None and lo is not None:
        return f'> {lo}{u}'
    if lo is not None and hi is not None:
        return f'{lo} – {hi}{u}'
    return '(any)'


def _state_midpoint(s, vlist_raws):
    lo, hi = s.get('threshold_min'), s.get('threshold_max')
    if lo is None and hi is not None:
        return hi * 0.6
    if hi is None and lo is not None:
        return lo * 1.2
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    # fallback: median of actual values
    raws = sorted(v for v in vlist_raws if v is not None)
    return raws[len(raws) // 2] if raws else 0


# ── Panel selectors ───────────────────────────────────────────────────────────

def pick_panels(char_def, by_state, n_panels_max=4):
    """Return list of (code, name, thresh_label, specimen_data) for up to n_panels_max states."""
    states = char_def.states_json or []
    panels = []
    for s in states[:n_panels_max]:
        code = s['code']
        vlist = by_state.get(code, [])
        if not vlist:
            continue
        raws = [v.raw_value for v in vlist]
        ideal = _state_midpoint(s, raws)
        best = min(vlist, key=lambda x: abs((x.raw_value or 999999) - ideal))
        struct = Structure.query.get(best.structure_id)
        spec = Specimen.query.get(struct.specimen_id)
        lm_raw = np.array(struct.landmarks_json, dtype=float)
        bnd = struct.boundary_json or {}
        unit = _get_unit(char_def.geometric_operation)
        panels.append((code, s['name'], _thresh_str(s, unit),
                       best.raw_value, spec.species_name, lm_raw, bnd))
    return panels


def _get_unit(op):
    if op == 'junction_angle':
        return '°'
    return ''


# ── Draw helpers ──────────────────────────────────────────────────────────────

def _draw_outline(ax, lm):
    ax.plot(lm[:, 0], lm[:, 1], color=COL_OUTLINE, lw=1.0, zorder=1,
            solid_capstyle='round')


def _draw_part(ax, lm, indices, color, lw=3.0, zorder=2):
    if not indices:
        return
    pts = lm[indices]
    ax.plot(pts[:, 0], pts[:, 1], color=color, lw=lw,
            solid_capstyle='round', zorder=zorder)


# ── ratio_arc_length diagram ──────────────────────────────────────────────────

def draw_ratio_panel(ax, lm, bnd, species, raw, state_code, state_name,
                     thresh_label, part_a_name, part_b_name,
                     col_a=COL_PARTА, col_b=COL_PARTB):
    _draw_outline(ax, lm)
    ai = bnd.get(part_a_name, [])
    bi_idx = bnd.get(part_b_name, [])
    len_a = _arc_len(lm, ai)
    len_b = _arc_len(lm, bi_idx)
    _draw_part(ax, lm, ai, col_a, lw=3.2, zorder=3)
    _draw_part(ax, lm, bi_idx, col_b, lw=3.2, zorder=2)
    # label
    if ai:
        ctr_a = lm[ai].mean(axis=0)
        ax.text(ctr_a[0], ctr_a[1], f'{len_a:.3f}',
                ha='center', va='center', fontsize=7.5, color=col_a,
                fontweight='bold', zorder=6,
                bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                          edgecolor=col_a, alpha=0.85, linewidth=0.8))
    if bi_idx:
        ctr_b = lm[bi_idx].mean(axis=0)
        ax.text(ctr_b[0], ctr_b[1], f'{len_b:.3f}',
                ha='center', va='center', fontsize=7.5, color=col_b,
                fontweight='bold', zorder=6,
                bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                          edgecolor=col_b, alpha=0.85, linewidth=0.8))
    # ratio annotation
    ax.text(0.98, 0.98, f'ratio = {raw:.3f}',
            ha='right', va='top', transform=ax.transAxes,
            fontsize=8, color='#334155',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#f1f5f9',
                      edgecolor='#94a3b8', alpha=0.9))
    _panel_cosmetics(ax, species, state_code, state_name, thresh_label)


# ── junction_angle diagram ────────────────────────────────────────────────────

def _fork_between(lm, idx_a, idx_b):
    """Find the junction point between two boundary sections."""
    # Nearest endpoint pair
    ends_a = [lm[idx_a[0]], lm[idx_a[-1]]]
    ends_b = [lm[idx_b[0]], lm[idx_b[-1]]]
    best_d = float('inf')
    fork = (ends_a[0] + ends_b[0]) / 2.0
    for ea in ends_a:
        for eb in ends_b:
            d = np.linalg.norm(ea - eb)
            if d < best_d:
                best_d = d
                fork = (ea + eb) / 2.0
    return fork


def draw_junction_panel(ax, lm, bnd, species, raw, state_code, state_name,
                        thresh_label, part_a_name, part_b_name):
    _draw_outline(ax, lm)
    ai = bnd.get(part_a_name, [])
    bi_idx = bnd.get(part_b_name, [])
    _draw_part(ax, lm, ai, COL_PARTА, lw=3.2, zorder=2)
    _draw_part(ax, lm, bi_idx, COL_PARTB, lw=3.2, zorder=2)

    if not ai or not bi_idx:
        _panel_cosmetics(ax, species, state_code, state_name, thresh_label)
        return

    fork = _fork_between(lm, ai, bi_idx)

    # Direction vectors from fork into each part
    axis_a = _central_axis(lm, ai, ref_point=fork)
    axis_b = _central_axis(lm, bi_idx, ref_point=fork)

    v_a = v_b = None
    if len(axis_a) >= 2:
        if np.linalg.norm(axis_a[0] - fork) > np.linalg.norm(axis_a[-1] - fork):
            axis_a = axis_a[::-1]
        half = max(2, len(axis_a) // 2)
        v_a = _midline_vector(axis_a[:half])
        if np.linalg.norm(v_a) > 1e-10:
            v_a = v_a / np.linalg.norm(v_a)
        else:
            v_a = None

    if len(axis_b) >= 2:
        if np.linalg.norm(axis_b[0] - fork) > np.linalg.norm(axis_b[-1] - fork):
            axis_b = axis_b[::-1]
        half = max(2, len(axis_b) // 2)
        v_b = _midline_vector(axis_b[:half])
        if np.linalg.norm(v_b) > 1e-10:
            v_b = v_b / np.linalg.norm(v_b)
        else:
            v_b = None

    if v_a is not None and v_b is not None:
        reach = 0.45
        # Draw arrows from fork into each part
        ax.annotate('', xy=fork + reach * v_a, xytext=fork,
                    arrowprops=dict(arrowstyle='->', color=COL_PARTА, lw=1.5), zorder=8)
        ax.plot([fork[0], (fork + reach * v_a)[0]],
                [fork[1], (fork + reach * v_a)[1]],
                color=COL_PARTА, lw=1.6, zorder=7)
        ax.annotate('', xy=fork + reach * v_b, xytext=fork,
                    arrowprops=dict(arrowstyle='->', color=COL_PARTB, lw=1.5), zorder=8)
        ax.plot([fork[0], (fork + reach * v_b)[0]],
                [fork[1], (fork + reach * v_b)[1]],
                color=COL_PARTB, lw=1.6, zorder=7)

        # Angle arc
        a_a = math.degrees(math.atan2(v_a[1], v_a[0]))
        a_b = math.degrees(math.atan2(v_b[1], v_b[0]))
        ccw = (a_b - a_a) % 360
        if ccw <= 180:
            theta1, theta2 = a_a, a_a + ccw
        else:
            theta1, theta2 = a_b, a_b + (360 - ccw)
        arc_r = 0.16
        arc_patch = Arc(fork, 2 * arc_r, 2 * arc_r,
                        angle=0, theta1=theta1, theta2=theta2,
                        color=COL_ARC_C, lw=2.0, zorder=9)
        ax.add_patch(arc_patch)
        mid_a = math.radians((theta1 + theta2) / 2)
        lx = fork[0] + (arc_r + 0.13) * math.cos(mid_a)
        ly = fork[1] + (arc_r + 0.13) * math.sin(mid_a)
        ax.text(lx, ly, f'{raw:.0f}°', ha='center', va='center',
                fontsize=9, color=COL_ARC_C, fontweight='bold', zorder=10)
        ax.plot(*fork, 'o', color=COL_ARC_C, ms=5, zorder=11,
                markeredgecolor='white', markeredgewidth=0.7)

    _panel_cosmetics(ax, species, state_code, state_name, thresh_label)


# ── sinuosity diagram ─────────────────────────────────────────────────────────

def draw_sinuosity_panel(ax, lm, bnd, species, raw, state_code, state_name,
                         thresh_label, part_name):
    _draw_outline(ax, lm)
    idx = bnd.get(part_name, [])
    if not idx:
        _panel_cosmetics(ax, species, state_code, state_name, thresh_label)
        return

    pts = lm[idx]
    ax.plot(pts[:, 0], pts[:, 1], color=COL_SINUOSITY, lw=3.0,
            solid_capstyle='round', zorder=3)

    # Chord
    chord_pts = np.array([pts[0], pts[-1]])
    arc_l = _arc_len(lm, idx)
    chord_l = float(np.linalg.norm(pts[-1] - pts[0]))
    ax.plot(chord_pts[:, 0], chord_pts[:, 1],
            color=COL_CHORD, lw=1.5, linestyle='--', zorder=4, alpha=0.9)
    ax.plot(*pts[0], 'D', color=COL_CHORD, ms=4, zorder=5,
            markeredgecolor='white', markeredgewidth=0.5)
    ax.plot(*pts[-1], 'D', color=COL_CHORD, ms=4, zorder=5,
            markeredgecolor='white', markeredgewidth=0.5)

    # Mid-chord label
    chord_mid = (pts[0] + pts[-1]) / 2.0
    ax.text(chord_mid[0], chord_mid[1] - 0.06,
            f'chord={chord_l:.3f}',
            ha='center', va='top', fontsize=7, color=COL_CHORD, zorder=6)
    arc_mid = pts[len(pts) // 2]
    ax.text(arc_mid[0], arc_mid[1],
            f'arc={arc_l:.3f}',
            ha='center', va='bottom', fontsize=7, color=COL_SINUOSITY,
            zorder=6, bbox=dict(boxstyle='round,pad=0.1', facecolor='white',
                                edgecolor='none', alpha=0.7))

    ax.text(0.98, 0.98, f'sinuosity = {raw:.4f}',
            ha='right', va='top', transform=ax.transAxes,
            fontsize=8, color='#334155',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#f1f5f9',
                      edgecolor='#94a3b8', alpha=0.9))
    _panel_cosmetics(ax, species, state_code, state_name, thresh_label)


# ── mean_curvature diagram ────────────────────────────────────────────────────

def draw_curvature_panel(ax, lm, bnd, species, raw, state_code, state_name,
                         thresh_label, part_name):
    _draw_outline(ax, lm)
    idx = bnd.get(part_name, [])
    if not idx or len(idx) < 3:
        _panel_cosmetics(ax, species, state_code, state_name, thresh_label)
        return

    pts = lm[idx]

    # Compute local curvature at each interior point
    n = len(pts)
    curv = np.zeros(n)
    for i in range(1, n - 1):
        v1 = pts[i] - pts[i - 1]
        v2 = pts[i + 1] - pts[i]
        l1 = np.linalg.norm(v1)
        l2 = np.linalg.norm(v2)
        if l1 > 1e-10 and l2 > 1e-10:
            cos_a = np.clip(np.dot(v1, v2) / (l1 * l2), -1, 1)
            angle = math.acos(cos_a)
            step = (l1 + l2) / 2.0
            curv[i] = angle / step
    # Endpoints inherit from neighbours
    curv[0] = curv[1]
    curv[-1] = curv[-2]

    # Colour by curvature magnitude
    vmax = max(curv.max(), 1e-6)
    norm = Normalize(vmin=0, vmax=vmax)
    cmap = plt.cm.plasma
    for i in range(n - 1):
        c = (curv[i] + curv[i + 1]) / 2.0
        color = cmap(norm(c))
        ax.plot(pts[i:i+2, 0], pts[i:i+2, 1],
                color=color, lw=4.0, solid_capstyle='round', zorder=3)

    ax.text(0.98, 0.98, f'mean curv = {raw:.4f}',
            ha='right', va='top', transform=ax.transAxes,
            fontsize=8, color='#334155',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#f1f5f9',
                      edgecolor='#94a3b8', alpha=0.9))

    # Small colourbar
    sm = ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    try:
        cb = plt.colorbar(sm, ax=ax, fraction=0.03, pad=0.02,
                          orientation='vertical', shrink=0.5)
        cb.set_label('local κ', fontsize=7)
        cb.ax.tick_params(labelsize=6)
    except Exception:
        pass

    _panel_cosmetics(ax, species, state_code, state_name, thresh_label)


# ── presence_threshold diagram ────────────────────────────────────────────────

def draw_presence_panel(ax, lm, bnd, species, raw, state_code, state_name,
                        thresh_label, part_name):
    _draw_outline(ax, lm)
    idx = bnd.get(part_name, [])
    # Total arc length of entire outline
    total_arc = _arc_len(lm, list(range(len(lm))))

    if idx:
        _draw_part(ax, lm, idx, COL_PRESENCE, lw=4.0, zorder=3)
        part_arc = _arc_len(lm, idx)
        frac = part_arc / total_arc * 100.0 if total_arc > 0 else 0.0
        ctr = lm[idx].mean(axis=0)
        ax.text(ctr[0], ctr[1],
                f'{frac:.1f}%\nof total',
                ha='center', va='center', fontsize=7.5, color=COL_PRESENCE,
                fontweight='bold', zorder=6,
                bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                          edgecolor=COL_PRESENCE, alpha=0.85, linewidth=0.8))

    ax.text(0.98, 0.98, f'fraction = {raw:.4f}',
            ha='right', va='top', transform=ax.transAxes,
            fontsize=8, color='#334155',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#f1f5f9',
                      edgecolor='#94a3b8', alpha=0.9))
    _panel_cosmetics(ax, species, state_code, state_name, thresh_label)


# ── Figure builder ────────────────────────────────────────────────────────────

def build_figure(panels, orient_fn, draw_fn, title, out_path, legend_handles=None):
    n = len(panels)
    if n == 0:
        print(f'  WARNING: no panels for {out_path}')
        return
    fig, axes = plt.subplots(1, n, figsize=(n * 3.6, 5.2))
    fig.patch.set_facecolor(COL_BG)
    ax_list = axes if n > 1 else [axes]
    PAD = 0.22

    for ax, (code, name, thresh, raw, species, lm_raw, bnd) in zip(ax_list, panels):
        lm = orient_fn(lm_raw, bnd)
        _set_limits(ax, lm, PAD)
        draw_fn(ax, lm, bnd, species, raw, code, name, thresh)

    if legend_handles:
        fig.legend(handles=legend_handles, loc='lower center', ncol=3,
                   frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, -0.06))

    fig.suptitle(title, fontsize=11.5, fontweight='bold', color='#1a4e78', y=1.01)
    plt.tight_layout(pad=0.85)
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=COL_BG)
    plt.close()
    print(f'  Wrote {os.path.basename(out_path)}')


# ── Main ──────────────────────────────────────────────────────────────────────

app = create_app()

with app.app_context():

    def get_char_panels(code):
        c = CharacterDefinition.query.filter_by(code=code).first()
        if not c:
            print(f'  MISSING definition for {code}')
            return None, None
        vals = (CharacterValue.query
                .filter_by(character_id=c.id)
                .filter(CharacterValue.state != None,
                        CharacterValue.raw_value != None)
                .all())
        by_state = {}
        for v in vals:
            if v.state and v.state not in ('?', '-'):
                by_state.setdefault(v.state, []).append(v)
        return c, by_state

    # ── ratio_arc_length legend ──────────────────────────────────────────────
    ral_legend = [
        mpatches.Patch(color=COL_PARTА, label='Part A (numerator)'),
        mpatches.Patch(color=COL_PARTB, label='Part B (denominator)'),
        plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline'),
    ]

    # ── sinuosity legend ─────────────────────────────────────────────────────
    sin_legend = [
        mpatches.Patch(color=COL_SINUOSITY, label='Part (arc)'),
        plt.Line2D([0],[0], color=COL_CHORD, lw=1.5, linestyle='--', label='Chord (endpoints)'),
        plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline'),
    ]

    # ── junction_angle legend ────────────────────────────────────────────────
    junc_legend = [
        mpatches.Patch(color=COL_PARTА, label='Part A'),
        mpatches.Patch(color=COL_PARTB, label='Part B'),
        plt.Line2D([0],[0], color=COL_ARC_C, lw=2, label='Junction angle arc'),
    ]

    # ── A01: Point / Shaft ratio_arc_length ──────────────────────────────────
    print('A01')
    c, by_state = get_char_panels('A01')
    if c:
        panels = pick_panels(c, by_state, 4)
        build_figure(
            panels, procrustes_anchor,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_ratio_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                 'Point', 'Shaft'),
            'A01 — Point / Shaft arc-length ratio  ·  anchor',
            os.path.join(OUT_DIR, 'a01_diagram.png'),
            ral_legend
        )

    # ── A03: SuperficialRoot / Shaft ratio_arc_length ────────────────────────
    print('A03')
    c, by_state = get_char_panels('A03')
    if c:
        panels = pick_panels(c, by_state, 4)
        build_figure(
            panels, procrustes_anchor,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_ratio_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                 'SuperficialRoot', 'Shaft',
                                 col_a='#2e7d32', col_b=COL_PARTB),
            'A03 — SuperficialRoot / Shaft arc-length ratio  ·  anchor',
            os.path.join(OUT_DIR, 'a03_diagram.png'),
            [mpatches.Patch(color='#2e7d32', label='SuperficialRoot (numerator)'),
             mpatches.Patch(color=COL_PARTB, label='Shaft (denominator)'),
             plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline')]
        )

    # ── A04: DeepRoot / Shaft ratio_arc_length ──────────────────────────────
    print('A04')
    c, by_state = get_char_panels('A04')
    if c:
        panels = pick_panels(c, by_state, 2)
        build_figure(
            panels, procrustes_anchor,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_ratio_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                 'DeepRoot', 'Shaft',
                                 col_a='#6a1b9a', col_b=COL_PARTB),
            'A04 — DeepRoot / Shaft arc-length ratio  ·  anchor',
            os.path.join(OUT_DIR, 'a04_diagram.png'),
            [mpatches.Patch(color='#6a1b9a', label='DeepRoot (numerator)'),
             mpatches.Patch(color=COL_PARTB, label='Shaft (denominator)'),
             plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline')]
        )

    # ── C01: Point / Shaft ratio_arc_length ─────────────────────────────────
    print('C01')
    c, by_state = get_char_panels('C01')
    if c:
        panels = pick_panels(c, by_state, 4)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_ratio_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                 'Point', 'Shaft'),
            'C01 — Point / Shaft arc-length ratio  ·  hook',
            os.path.join(OUT_DIR, 'c01_diagram.png'),
            ral_legend
        )

    # ── C02: junction_angle Point / Shaft ───────────────────────────────────
    print('C02')
    c, by_state = get_char_panels('C02')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_junction_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                    'Point', 'Shaft'),
            'C02 — Point curvature  ·  junction angle Point–Shaft  ·  hook',
            os.path.join(OUT_DIR, 'c02_diagram.png'),
            junc_legend
        )

    # ── C03: sinuosity Point ─────────────────────────────────────────────────
    print('C03')
    c, by_state = get_char_panels('C03')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_sinuosity_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Point'),
            'C03 — Point waviness  ·  sinuosity of Point  ·  hook',
            os.path.join(OUT_DIR, 'c03_diagram.png'),
            sin_legend
        )

    # ── C05: mean_curvature Shaft ────────────────────────────────────────────
    print('C05')
    c, by_state = get_char_panels('C05')
    if c:
        # States: 0, 1, 2 (3 is manual/special — may have no threshold)
        panels = pick_panels(c, by_state, 4)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_curvature_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Shaft'),
            'C05 — Shaft curvature  ·  mean curvature of Shaft  ·  hook',
            os.path.join(OUT_DIR, 'c05_diagram.png'),
            [plt.Line2D([0],[0], color=plt.cm.plasma(0.2), lw=4, label='Low curvature'),
             plt.Line2D([0],[0], color=plt.cm.plasma(0.7), lw=4, label='High curvature'),
             plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline')]
        )

    # ── C07: sinuosity Shelf ─────────────────────────────────────────────────
    print('C07')
    c, by_state = get_char_panels('C07')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_sinuosity_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Shelf'),
            'C07 — Shelf profile  ·  sinuosity of Shelf  ·  hook',
            os.path.join(OUT_DIR, 'c07_diagram.png'),
            sin_legend
        )

    # ── C08: sinuosity Base ──────────────────────────────────────────────────
    print('C08')
    c, by_state = get_char_panels('C08')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_sinuosity_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Base'),
            'C08 — Base profile  ·  sinuosity of Base  ·  hook',
            os.path.join(OUT_DIR, 'c08_diagram.png'),
            sin_legend
        )

    # ── C09: ratio_arc_length Base / Heel ───────────────────────────────────
    print('C09')
    c, by_state = get_char_panels('C09')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_ratio_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                 'Base', 'Heel'),
            'C09 — Base–Heel ratio  ·  Base / Heel arc-length ratio  ·  hook',
            os.path.join(OUT_DIR, 'c09_diagram.png'),
            [mpatches.Patch(color=COL_PARTА, label='Base (numerator)'),
             mpatches.Patch(color=COL_PARTB, label='Heel (denominator)'),
             plt.Line2D([0],[0], color=COL_OUTLINE, lw=1, label='Full outline')]
        )

    # ── C10: presence_threshold Heel ────────────────────────────────────────
    print('C10')
    c, by_state = get_char_panels('C10')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_presence_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Heel'),
            'C10 — Heel conspicuousness  ·  Heel fraction of total arc  ·  hook',
            os.path.join(OUT_DIR, 'c10_diagram.png'),
            [mpatches.Patch(color=COL_PRESENCE, label='Heel (highlighted)'),
             mpatches.Patch(color=COL_OUTLINE, label='Remaining outline')]
        )

    # ── C11: sinuosity Heel ──────────────────────────────────────────────────
    print('C11')
    c, by_state = get_char_panels('C11')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_sinuosity_panel(ax, lm, bnd, sp, raw, sc, sn, tl, 'Heel'),
            'C11 — Heel profile  ·  sinuosity of Heel  ·  hook',
            os.path.join(OUT_DIR, 'c11_diagram.png'),
            sin_legend
        )

    # ── C12: junction_angle Heel / Shaft ────────────────────────────────────
    print('C12')
    c, by_state = get_char_panels('C12')
    if c:
        panels = pick_panels(c, by_state, 3)
        build_figure(
            panels, procrustes_hook,
            lambda ax, lm, bnd, sp, raw, sc, sn, tl:
                draw_junction_panel(ax, lm, bnd, sp, raw, sc, sn, tl,
                                    'Heel', 'Shaft'),
            'C12 — Heel–Shaft transition  ·  junction angle Heel–Shaft  ·  hook',
            os.path.join(OUT_DIR, 'c12_diagram.png'),
            [mpatches.Patch(color=COL_PARTА, label='Heel'),
             mpatches.Patch(color=COL_PARTB, label='Shaft'),
             plt.Line2D([0],[0], color=COL_ARC_C, lw=2, label='Junction angle arc')]
        )

    print('\nDone. Summary of written files:')
    for fname in sorted(os.listdir(OUT_DIR)):
        if fname.endswith('.png') and fname != 'c06_shaft_angle.png':
            fpath = os.path.join(OUT_DIR, fname)
            kb = os.path.getsize(fpath) // 1024
            print(f'  {fname}  ({kb} kB)')
