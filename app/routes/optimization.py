import re
import copy
import random
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from sqlalchemy import func as sqlfunc
from app import db
from app.models import (Project, Specimen, Structure,
                        CharacterDefinition, CharacterValue, SpeciesAlias)

optimization_bp = Blueprint('optimization', __name__)


# ── Specimen-level (ecological) virtual characters ───────────────────────────
# Host / distribution data live as free-text fields on Specimen, not as
# Structure-based CharacterValues. These descriptors expose them to the
# optimization pipeline as discrete, Fitch-optimizable characters. Their ids
# are strings prefixed "v_" so they never collide with real character ids.
def _virtual_char_descriptors():
    from app.routes.matrix import _normalize_habitat
    return [
        {'id': 'v_habitat',      'code': 'ECO1',
         'name': 'Host habitat',           'get': lambda sp: _normalize_habitat(sp.host_habitat)},
        {'id': 'v_distribution', 'code': 'ECO2',
         'name': 'Distribution (locality)', 'get': lambda sp: (sp.geographic_area or '').strip()},
        {'id': 'v_host_family',  'code': 'ECO3',
         'name': 'Host family',            'get': lambda sp: (sp.host_family or '').strip()},
        {'id': 'v_host_order',   'code': 'ECO4',
         'name': 'Host order',             'get': lambda sp: (sp.host_order or '').strip()},
        {'id': 'v_parasite_habitat', 'code': 'ECO5',
         'name': 'Parasite habitat',       'get': lambda sp: _normalize_habitat(sp.parasite_habitat)},
    ]


def _virtual_char_result(desc, specimens, species_to_sp_ids, sp_by_id,
                         alias_map, tree_root):
    """Build one optimization result dict for a specimen-level virtual
    character, or None if no specimen carries a value for it."""
    getval = desc['get']

    # Distinct non-empty raw values -> stable state codes "0","1",...
    distinct = []
    seen = set()
    for sp in specimens:
        v = getval(sp)
        if v and v not in seen:
            seen.add(v)
            distinct.append(v)
    if not distinct:
        return None
    distinct.sort()
    value_to_code = {v: str(i) for i, v in enumerate(distinct)}

    # tip_states: normalized species name -> set of state codes
    tip_states = {}
    for norm_sp, sp_ids in species_to_sp_ids.items():
        observed = set()
        for sp_id in sp_ids:
            sp = sp_by_id.get(sp_id)
            v = getval(sp) if sp else ''
            if v:
                observed.add(value_to_code[v])
        if observed:
            tip_states[norm_sp] = observed

    if not tip_states:
        return None

    # Propagate aliases so tree labels resolve to tip_states
    for lbl_norm, sp_norm in alias_map.items():
        if sp_norm in tip_states and lbl_norm not in tip_states:
            tip_states[lbl_norm] = tip_states[sp_norm]

    annotated, pscore = _fitch_parsimony(tree_root, tip_states)
    signal = _compute_signal(tree_root, tip_states, pscore)

    return {
        'id':              desc['id'],
        'code':            desc['code'],
        'name':            desc['name'],
        'structure_type':  'ecology',
        'parsimony_score': pscore,
        'signal':          signal,
        'states':          [{'code': c, 'name': v} for v, c in value_to_code.items()],
        'tree':            annotated,
    }


def _norm_name(s):
    s = (s or '').strip().strip("'\"")
    if '!' in s or '=' in s:
        return '\x00'
    if '|' in s:
        s = s.split('|')[-1]
    s = re.sub(r'^_R_', '', s, flags=re.IGNORECASE)
    return s.lower().replace('_', ' ').strip()


def _parse_newick(s):
    import re as _re
    s = s.strip().rstrip(';')
    s = _re.sub(r'\[[^\]]*\]', '', s)   # strip FigTree/NHX [..] comments
    pos = [0]

    def parse():
        node = {'children': [], 'name': '', 'length': 1.0}
        if pos[0] < len(s) and s[pos[0]] == '(':
            pos[0] += 1
            node['children'].append(parse())
            while pos[0] < len(s) and s[pos[0]] == ',':
                pos[0] += 1
                node['children'].append(parse())
            if pos[0] < len(s) and s[pos[0]] == ')':
                pos[0] += 1
        name = ''
        while pos[0] < len(s) and s[pos[0]] not in ',);:':
            name += s[pos[0]]
            pos[0] += 1
        node['name'] = name.strip().strip("'\"").replace('_', ' ')
        if pos[0] < len(s) and s[pos[0]] == ':':
            pos[0] += 1
            length = ''
            while pos[0] < len(s) and s[pos[0]] not in ',);':
                length += s[pos[0]]
                pos[0] += 1
            try:
                node['length'] = float(length)
            except ValueError:
                node['length'] = 1.0
        if not node['children']:
            del node['children']
        return node

    try:
        return parse()
    except Exception:
        return None


def _fitch_parsimony(tree_root, tip_states):
    """Two-pass Fitch parsimony on a deep copy of tree_root.

    Parsimony score = actual branch changes counted in the top-down pass,
    which handles polytomies correctly (unlike bottom-up union counting alone).

    tip_states: {normalized_species_name: set_of_state_codes}
    Returns (annotated_tree, parsimony_score).
    """
    tree = copy.deepcopy(tree_root)

    def bottom_up(node):
        children = node.get('children', [])
        if not children:
            name = _norm_name(node.get('name', ''))
            states = tip_states.get(name)
            node['_s'] = set(states) if states else None
            return
        for child in children:
            bottom_up(child)
        child_sets = [c['_s'] for c in children if c.get('_s') is not None]
        if not child_sets:
            node['_s'] = None
            return
        inter = child_sets[0].copy()
        for cs in child_sets[1:]:
            inter &= cs
        if inter:
            node['_s'] = inter
        else:
            union = set()
            for cs in child_sets:
                union |= cs
            node['_s'] = union

    changes = [0]

    def top_down(node, parent_state=None):
        s = node.get('_s')
        is_leaf = not node.get('children')
        if s is None and is_leaf:
            # Tip scored "?" (no observed state): show it as missing, not the
            # inherited/estimated state. Propagate the parent state downward so
            # this tip does not disturb ancestral reconstruction elsewhere.
            node['state'] = None
            node['missing'] = True
        elif s is None:
            node['state'] = parent_state
        elif parent_state is not None and parent_state in s:
            node['state'] = parent_state
        else:
            node['state'] = sorted(s)[0]
        node['changed'] = bool(
            parent_state is not None
            and node.get('state') is not None
            and node['state'] != parent_state
        )
        if node['changed']:
            changes[0] += 1
        # An internal node is equivocal when the parsimony reconstruction does
        # not determine a single state (Fitch set has >1 state, or no descendant
        # data). Such nodes are displayed as "?" rather than a picked state. The
        # resolved node['state'] is kept for the (unchanged) parsimony score.
        if not is_leaf:
            node['equivocal'] = (s is None) or (len(s) > 1)
        node.pop('_s', None)
        # A missing tip has no state of its own; keep flowing the parent state.
        child_state = node['state'] if node['state'] is not None else parent_state
        for child in node.get('children', []):
            top_down(child, child_state)

    bottom_up(tree)
    top_down(tree)
    return tree, changes[0]


def _fitch_score_only(node, tip_states):
    """Fast two-pass Fitch that returns only the branch-change count.
    Does not modify the tree. Used for permutation testing.
    """
    node_states = {}

    def bu(n):
        nid = id(n)
        children = n.get('children', [])
        if not children:
            nm = _norm_name(n.get('name', ''))
            r = tip_states.get(nm)
            node_states[nid] = r
            return r
        child_sets = []
        for c in children:
            cs = bu(c)
            if cs is not None:
                child_sets.append(cs)
        if not child_sets:
            node_states[nid] = None
            return None
        inter = child_sets[0]
        for cs in child_sets[1:]:
            inter = inter & cs
        if inter:
            node_states[nid] = inter
            return inter
        union = set()
        for cs in child_sets:
            union |= cs
        node_states[nid] = union
        return union

    bu(node)

    n_changes = [0]

    def td(n, parent_state=None):
        nid = id(n)
        s = node_states.get(nid)
        if s is None:
            curr = parent_state
        elif parent_state is not None and parent_state in s:
            curr = parent_state
        else:
            curr = sorted(s)[0]
        if parent_state is not None and curr is not None and curr != parent_state:
            n_changes[0] += 1
        for c in n.get('children', []):
            td(c, curr)

    td(node)
    return n_changes[0]


def _compute_signal(tree_root, tip_states, observed_score, n_perm=499):
    """Compute CI, RI, and permutation p-value for phylogenetic signal.

    CI (Consistency Index) = m / s  (m = min possible steps, s = observed steps).
    RI (Retention Index)   = (g - s) / (g - m)  (g = max possible steps).
    p-value: proportion of random tip permutations with score <= observed.
    """
    if not tip_states:
        return {'ci': None, 'ri': None, 'p_value': None, 'note': 'no_data'}

    # Canonical single state per tip (alphabetically first for determinism)
    canon = {name: sorted(states)[0] for name, states in tip_states.items()}
    state_counts = {}
    for st in canon.values():
        state_counts[st] = state_counts.get(st, 0) + 1

    k      = len(state_counts)
    n_taxa = len(canon)

    if k < 2:
        return {'ci': 1.0, 'ri': 1.0, 'p_value': None, 'note': 'invariant'}
    if n_taxa < 3:
        return {'ci': None, 'ri': None, 'p_value': None, 'note': 'insufficient_data'}

    m = k - 1                                   # minimum possible steps
    g = n_taxa - max(state_counts.values())     # maximum possible steps

    s  = observed_score
    ci = round(m / s, 3) if s > 0 else 1.0
    ri_denom = g - m
    ri_raw   = ((g - s) / ri_denom) if ri_denom > 0 else 1.0
    ri       = round(max(0.0, min(1.0, ri_raw)), 3)

    # Permutation test
    leaf_names   = list(canon.keys())
    leaf_states  = [canon[nm] for nm in leaf_names]
    shuffled     = leaf_states[:]

    n_le = 0
    for _ in range(n_perm):
        random.shuffle(shuffled)
        perm_tip = {leaf_names[i]: {shuffled[i]} for i in range(len(leaf_names))}
        if _fitch_score_only(tree_root, perm_tip) <= s:
            n_le += 1

    p_value = round((n_le + 1) / (n_perm + 1), 4)

    return {'ci': ci, 'ri': ri, 'p_value': p_value, 'note': None}


@optimization_bp.route('/project/<int:project_id>/optimization')
@login_required
def optimization_view(project_id):
    project = Project.query.get_or_404(project_id)
    chars = (CharacterDefinition.query
             .filter_by(project_id=project_id, active=True)
             .order_by(sqlfunc.coalesce(CharacterDefinition.display_order, 999999),
                       CharacterDefinition.code)
             .all())
    return render_template('optimization/optimization.html',
                           project=project,
                           characters=chars,
                           tree_fragments=project.tree_fragments or {},
                           has_tree=bool(project.tree_newick))


@optimization_bp.route('/api/project/<int:project_id>/optimization/run', methods=['POST'])
@login_required
def run_optimization(project_id):
    project = Project.query.get_or_404(project_id)
    if not project.tree_newick:
        return jsonify({'error': 'No phylogenetic tree found for this project.'}), 400

    tree_root = _parse_newick(project.tree_newick)
    if not tree_root:
        return jsonify({'error': 'Failed to parse the phylogenetic tree.'}), 400

    # Alias map: normalized tree label → normalized specimen name
    aliases = SpeciesAlias.query.filter_by(project_id=project_id).all()
    alias_map = {_norm_name(a.tree_label): _norm_name(a.specimen_name) for a in aliases}

    # Normalized species name → list of specimen IDs
    specimens = Specimen.query.filter_by(project_id=project_id).all()
    species_to_sp_ids = {}
    for sp in specimens:
        species_to_sp_ids.setdefault(_norm_name(sp.species_name), []).append(sp.id)

    # Specimen ID → list of structure IDs
    all_structures = (Structure.query
                      .join(Specimen)
                      .filter(Specimen.project_id == project_id)
                      .all())
    sp_to_struct_ids = {}
    for st in all_structures:
        sp_to_struct_ids.setdefault(st.specimen_id, []).append(st.id)

    # Which characters to optimize
    body = request.get_json(silent=True) or {}
    char_ids = body.get('character_ids') or []
    virtual_ids = [str(cid) for cid in char_ids if str(cid).startswith('v_')]
    db_char_ids = [cid for cid in char_ids if not str(cid).startswith('v_')]
    if char_ids:
        chars = (CharacterDefinition.query
                 .filter(CharacterDefinition.id.in_(db_char_ids),
                         CharacterDefinition.project_id == project_id)
                 .all()) if db_char_ids else []
    else:
        chars = (CharacterDefinition.query
                 .filter_by(project_id=project_id, active=True)
                 .order_by(sqlfunc.coalesce(CharacterDefinition.display_order, 999999),
                           CharacterDefinition.code)
                 .all())

    # Bulk-fetch all character values in one query
    all_char_ids = [c.id for c in chars]
    values = CharacterValue.query.filter(
        CharacterValue.character_id.in_(all_char_ids)
    ).all()
    struct_states = {}
    for v in values:
        struct_states.setdefault(v.structure_id, {})[v.character_id] = v.state

    results = []
    for char in chars:
        # Build tip_states: norm species name → set of observed states
        tip_states = {}
        for norm_sp, sp_ids in species_to_sp_ids.items():
            observed = set()
            for sp_id in sp_ids:
                for sid in sp_to_struct_ids.get(sp_id, []):
                    st = struct_states.get(sid, {}).get(char.id)
                    # "?" is the absence of a state, not a state. Skip it so
                    # Fitch treats such tips as missing (no ?-> or ->? changes).
                    if st and st.strip() != '?':
                        observed.add(st)
            if observed:
                tip_states[norm_sp] = observed

        # Propagate aliases so tree labels resolve to tip_states
        for lbl_norm, sp_norm in alias_map.items():
            if sp_norm in tip_states and lbl_norm not in tip_states:
                tip_states[lbl_norm] = tip_states[sp_norm]

        annotated, pscore = _fitch_parsimony(tree_root, tip_states)
        signal = _compute_signal(tree_root, tip_states, pscore)

        results.append({
            'id': char.id,
            'code': char.code,
            'name': char.name,
            'structure_type': char.structure_type,
            'parsimony_score': pscore,
            'signal': signal,
            'states': [
                {'code': s.get('code', ''), 'name': s.get('name', '')}
                for s in (char.states_json or [])
            ],
            'tree': annotated,
        })

    # Specimen-level ecological characters (host habitat, distribution, host
    # family/order). Included by default; when an explicit selection was sent,
    # only those whose id was requested.
    sp_by_id = {sp.id: sp for sp in specimens}
    for desc in _virtual_char_descriptors():
        if char_ids and desc['id'] not in virtual_ids:
            continue
        vres = _virtual_char_result(desc, specimens, species_to_sp_ids,
                                    sp_by_id, alias_map, tree_root)
        if vres:
            results.append(vres)

    return jsonify({'characters': results})


def _structure_completeness(s):
    """Higher = more complete structure (prefer for display / value lookup)."""
    return (bool(s.landmarks_json) * 2 + bool(s.landmarks_confirmed) * 2 +
            bool(s.boundary_json) + bool(s.image_path))


@optimization_bp.route('/api/project/<int:project_id>/optimization/cell', methods=['GET'])
@login_required
def optimization_cell(project_id):
    """Resolve a tree tip (species label) + character to its structure image and
    current state, for the Matrix-style popup on the optimization tree. Returns a
    payload compatible with the matrix cell popup (state saved via the existing
    /matrix/override or /matrix/assign endpoints)."""
    Project.query.get_or_404(project_id)
    char_id = request.args.get('char_id', type=int)
    species = request.args.get('species', '') or ''
    char = CharacterDefinition.query.filter_by(id=char_id, project_id=project_id).first_or_404()

    # Resolve the tree label to a specimen: apply alias (tree_label -> specimen
    # name), then match on normalized species name.
    target = _norm_name(species)
    alias_map = {_norm_name(a.tree_label): _norm_name(a.specimen_name)
                 for a in SpeciesAlias.query.filter_by(project_id=project_id).all()}
    target = alias_map.get(target, target)

    specimen = next((sp for sp in Specimen.query.filter_by(project_id=project_id).all()
                     if _norm_name(sp.species_name) == target), None)
    if not specimen:
        return jsonify({'error': f'No specimen matches tree tip "{species}".'}), 404

    all_structs = Structure.query.filter_by(specimen_id=specimen.id).all()
    type_structs = [s for s in all_structs if s.structure_type == char.structure_type]
    if type_structs:
        primary = max(type_structs, key=_structure_completeness)
    elif all_structs:
        primary = max(all_structs, key=_structure_completeness)   # proxy image only
    else:
        return jsonify({'error': f'No structures for {specimen.species_name}.'}), 404

    val = CharacterValue.query.filter_by(
        structure_id=primary.id, character_id=char.id).first()

    return jsonify({
        'species':          specimen.species_name,
        'character':        char.name,
        'code':             char.code,
        'state':            val.state if val else None,
        'raw_value':        val.raw_value if val else None,
        'confidence':       val.confidence if val else None,
        'auto_assigned':    val.auto_assigned if val else None,
        'override_reason':  val.override_reason if val else None,
        'states':           char.states_json,
        'computation_type': char.computation_type,
        'image_url':        f'/uploads/{primary.image_path}' if primary.image_path else None,
        'has_target_structure': bool(type_structs),
        'value_id':         val.id if val else None,
        'struct_id':        primary.id,
        'char_id':          char.id,
    })
