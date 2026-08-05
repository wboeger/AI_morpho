import re
import copy
import math
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
                         alias_map, tree_root, method='fitch'):
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

    annotated, score, score_label, signal, extra = _run_method(method, tree_root, tip_states)

    result = {
        'id':              desc['id'],
        'code':            desc['code'],
        'name':            desc['name'],
        'structure_type':  'ecology',
        'method':          method,
        'score':           score,
        'score_label':     score_label,
        'parsimony_score': score,   # back-compat alias for existing JS reading this field
        'signal':          signal,
        'states':          [{'code': c, 'name': v} for v, c in value_to_code.items()],
        'tree':            annotated,
    }
    result.update(extra)
    return result


def _norm_name(s):
    s = (s or '').strip().strip("'\"")
    if '!' in s or '=' in s:
        return '\x00'
    if '|' in s:
        s = s.split('|')[-1]
    s = re.sub(r'^_R_', '', s, flags=re.IGNORECASE)
    # Drop periods too — tree tips write 'sp'/'cf'/'aff' without the dot that
    # specimen names keep (e.g. 'Genus_sp' vs 'Genus sp.'); without this a
    # specimen's species-name key never matched its own tree tip, so its
    # states were silently dropped from the parsimony/optimization inputs.
    return s.lower().replace('_', ' ').replace('.', '').strip()


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


def _liebermann_optimize(tree_root, tip_states):
    """Two-pass polymorphic ancestral-state optimization, ported from
    lieberman6.R (downward_pass + upward_pass). Unlike Fitch, which always
    forces a single state at ambiguous nodes, this keeps the full retained
    state set at every node — the point being to preserve genuine
    polymorphism (e.g. "this ancestor plausibly used either host A or B")
    instead of arbitrarily picking one.

    Downward pass: postorder Fitch-style intersection/union, generalized to
    N-ary nodes (the R script assumes strictly bifurcating).

    Upward pass: the R script tightens/expands each node's state set using
    its parent's (already-processed) set, iterating over ape's internal
    node-index order. That order isn't a portable concept outside ape's
    specific numbering, so this is a direct, order-independent
    generalization of the same rule via a preorder (root-to-tip) recursion:
    each node is refined using its parent's *already finalized* state
    before its own children are visited, which is the same information-flow
    direction ("push the parent's resolution down") the R code relies on.

    tip_states: {normalized_species_name: set_of_state_codes}
    Returns (annotated_tree, stats) where stats has ambiguity/counts instead
    of a single parsimony-style step count (polymorphism isn't a step count).
    """
    tree = copy.deepcopy(tree_root)
    node_states = {}

    def downward(n):
        children = n.get('children', [])
        if not children:
            name = _norm_name(n.get('name', ''))
            s = tip_states.get(name)
            node_states[id(n)] = set(s) if s else None
            return node_states[id(n)]
        child_sets = [downward(c) for c in children]
        non_null = [cs for cs in child_sets if cs is not None]
        if not non_null:
            node_states[id(n)] = None
        elif len(non_null) == 1:
            node_states[id(n)] = set(non_null[0])
        else:
            inter = non_null[0].copy()
            for cs in non_null[1:]:
                inter &= cs
            if inter:
                node_states[id(n)] = inter
            else:
                union = set()
                for cs in non_null:
                    union |= cs
                node_states[id(n)] = union
        return node_states[id(n)]

    downward(tree)

    def upward(n, parent_state):
        ns = node_states.get(id(n))
        if parent_state is not None and ns is not None:
            if parent_state <= ns:
                # Parent's set is already consistent with (a subset of) this
                # node's set — tighten to their intersection if that narrows it.
                if len(ns) > len(parent_state):
                    ns = ns & parent_state
                    node_states[id(n)] = ns
            else:
                children = n.get('children', [])
                child_sets = [node_states.get(id(c)) for c in children]
                non_null = [cs for cs in child_sets if cs is not None]
                if len(non_null) >= 2:
                    pairwise_inter = non_null[0].copy()
                    for cs in non_null[1:]:
                        pairwise_inter &= cs
                    if pairwise_inter:
                        union_children = set()
                        for cs in non_null:
                            union_children |= cs
                        additional = parent_state & union_children
                    else:
                        additional = parent_state - ns
                    ns = ns | additional
                    node_states[id(n)] = ns
        for c in n.get('children', []):
            upward(c, ns)

    upward(tree, None)

    stats = {'ambiguity': 0, 'n_polymorphic': 0, 'n_monomorphic': 0, 'n_missing': 0}

    def annotate(n, parent_state):
        s = node_states.get(id(n))
        if s is None:
            n['state'] = None
            n['states_list'] = []
            n['missing'] = True
            n['equivocal'] = True
            stats['n_missing'] += 1
        else:
            slist = sorted(s)
            n['states_list'] = slist
            n['state'] = slist[0]           # primary state, for coloring/continuity
            polymorphic = len(slist) > 1
            n['equivocal'] = polymorphic    # shown as "?" like Fitch's ambiguous nodes,
                                             # full set is still in states_list for the UI
            if polymorphic:
                stats['n_polymorphic'] += 1
                stats['ambiguity'] += len(slist) - 1
            else:
                stats['n_monomorphic'] += 1
        n['changed'] = bool(
            parent_state is not None
            and n.get('state') is not None
            and n['state'] != parent_state
        )
        child_state = n['state'] if n.get('state') is not None else parent_state
        for c in n.get('children', []):
            annotate(c, child_state)

    annotate(tree, None)
    return tree, stats


# ── Mk equal-rates likelihood ancestral-state reconstruction ────────────────
# Closed-form transition probabilities for the symmetric k-state Mk model
# (Lewis 2001 / "Neyman k-state" model): instantaneous rate matrix Q has
# off-diagonal r/(k-1), diagonal -r. Q = c(J - kI) with c = r/(k-1) has
# eigenvalues 0 (the stationary/all-ones direction) and -rk/(k-1)
# (multiplicity k-1), giving exp(Qt) = J/k + exp(-rkt/(k-1))*(I - J/k):
#   P_same(t) = 1/k + (k-1)/k * exp(-rkt/(k-1))
#   P_diff(t) = 1/k -   1/k   * exp(-rkt/(k-1))
# Verified against a hand-derived 2-taxon closed form during development.
def _er_probs(k, r, t):
    if k <= 1:
        return 1.0, 0.0
    t = max(t or 0.0, 1e-8)
    decay = math.exp(-r * k * t / (k - 1))
    p_same = 1.0 / k + (k - 1) / k * decay
    p_diff = 1.0 / k - 1.0 / k * decay
    return p_same, p_diff


def _er_transmit(vec, k, p_same, p_diff):
    """Apply the (symmetric) ER transition matrix to a likelihood/message vector."""
    total = sum(vec)
    return [p_diff * (total - vec[i]) + p_same * vec[i] for i in range(k)]


def _mk_tip_vector(node, tip_states, states, idx, k):
    name = _norm_name(node.get('name', ''))
    obs = tip_states.get(name)
    if not obs:
        return [1.0] * k   # missing data: fully ambiguous, contributes no information
    vec = [0.0] * k
    for s in obs:
        if s in idx:
            vec[idx[s]] = 1.0
    return vec if sum(vec) > 0 else [1.0] * k


def _mk_pruning_loglik(tree_root, tip_states, states, r, down=None):
    """Felsenstein pruning under the ER model. If `down` (a dict) is given,
    records each node's downward conditional-likelihood vector and each
    internal node's per-child transmitted message (needed for the marginal
    reconstruction belief-propagation pass) — otherwise just returns logL,
    used during rate-fitting where per-node detail isn't needed.
    """
    k = len(states)
    idx = {s: i for i, s in enumerate(states)}

    def rec(n):
        children = n.get('children', [])
        if not children:
            vec = _mk_tip_vector(n, tip_states, states, idx, k)
            if down is not None:
                down[id(n)] = vec
            return vec
        acc = [1.0] * k
        child_msgs = []
        for c in children:
            t = c.get('length', 1.0) or 1.0
            p_same, p_diff = _er_probs(k, r, t)
            cl = rec(c)
            msg = _er_transmit(cl, k, p_same, p_diff)
            child_msgs.append(msg)
            for i in range(k):
                acc[i] *= msg[i]
        if down is not None:
            down[id(n)] = acc
            n['_child_msgs'] = child_msgs
        return acc

    root_vec = rec(tree_root)
    prior = 1.0 / k
    total = sum(prior * v for v in root_vec)
    logl = math.log(total) if total > 0 else float('-inf')
    return logl


def _fit_er_rate(tree_root, tip_states, states, iters=40):
    """1-D golden-section search for the ML rate r > 0. A single symmetric
    rate is the only free parameter of the ER model, so a derivative-free
    bracketed search is sufficient — no need for scipy (not a dependency
    of this project)."""
    f = lambda r: _mk_pruning_loglik(tree_root, tip_states, states, r)
    gr = (math.sqrt(5) - 1) / 2
    lo, hi = 1e-4, 50.0
    c  = hi - gr * (hi - lo)
    dp = lo + gr * (hi - lo)
    fc, fd = f(c), f(dp)
    for _ in range(iters):
        if fc > fd:
            hi, dp, fd = dp, c, fc
            c = hi - gr * (hi - lo)
            fc = f(c)
        else:
            lo, c, fc = c, dp, fd
            dp = lo + gr * (hi - lo)
            fd = f(dp)
    r_best = (lo + hi) / 2
    return r_best, f(r_best)


def _mk_er_optimize(tree_root, tip_states):
    """ML ancestral-state reconstruction under the equal-rates Mk model.
    Fits a single rate by ML, then computes marginal posterior state
    probabilities at every node via belief propagation (mathematically the
    rerooting method: a downward/postorder pass plus an upward/preorder
    pass sending each node its parent-side "outside" evidence, built from
    the parent's incoming message times the product of sibling messages —
    the standard sum-product algorithm on a tree). Verified during
    development: marginals sum to 1 at every node, tips pin to their
    observed state, and a fully-consistent toy dataset produces strongly
    peaked ancestral posteriors as expected.

    tip_states: {normalized_species_name: set_of_state_codes}
    Returns (annotated_tree, info) with info = {'rate', 'log_likelihood'}.
    """
    tree = copy.deepcopy(tree_root)
    states = sorted({s for vals in tip_states.values() for s in vals})
    k = len(states)
    if k < 2:
        # Invariant or no data: nothing to optimize: mark every node missing/
        # unresolved and bail out cheaply rather than dividing by zero below.
        def mark_missing(n):
            n['state'] = states[0] if states else None
            n['states_list'] = states
            n['missing'] = not tip_states
            n['equivocal'] = False
            n['changed'] = False
            for c in n.get('children', []):
                mark_missing(c)
        mark_missing(tree)
        return tree, {'rate': None, 'log_likelihood': None}

    r_best, logl = _fit_er_rate(tree, tip_states, states)

    down = {}
    _mk_pruning_loglik(tree, tip_states, states, r_best, down=down)

    def marginal(n, parent_up):
        up_here = parent_up if parent_up is not None else [1.0 / k] * k
        down_here = down[id(n)]
        marg = [up_here[i] * down_here[i] for i in range(k)]
        tot = sum(marg)
        marg = [m / tot for m in marg] if tot > 0 else [1.0 / k] * k

        is_leaf   = not n.get('children')
        is_missing = is_leaf and _norm_name(n.get('name', '')) not in tip_states

        best_i = max(range(k), key=lambda i: marg[i])
        n['states_list'] = states
        n['probs']       = {states[i]: round(marg[i], 4) for i in range(k)}
        n['missing']     = is_missing
        if is_missing:
            # Same convention as Fitch/Liebermann: a tip with no data shows as
            # missing ("?", dashed circle), not our best (diffuse-prior) guess.
            # The renderer's own missing-check is `!node.state`, so this must
            # actually be None, not just an informational flag.
            n['state'] = None
            n['equivocal'] = True
        else:
            n['state'] = states[best_i]
            # "Equivocal" mirrors Fitch's meaning (no confident single state) —
            # here: the ML posterior doesn't clearly favor one state.
            n['equivocal'] = marg[best_i] < 0.5

        children = n.get('children', [])
        child_msgs = n.pop('_child_msgs', [])
        for ci, c in enumerate(children):
            combined = up_here[:]
            for cj, msg in enumerate(child_msgs):
                if cj == ci:
                    continue
                for i in range(k):
                    combined[i] *= msg[i]
            t = c.get('length', 1.0) or 1.0
            p_same, p_diff = _er_probs(k, r_best, t)
            msg_to_child = _er_transmit(combined, k, p_same, p_diff)
            tot2 = sum(msg_to_child)
            if tot2 > 0:
                msg_to_child = [m / tot2 for m in msg_to_child]
            marginal(c, msg_to_child)

    marginal(tree, None)

    def flag_changes(n, parent_state):
        n['changed'] = bool(
            parent_state is not None
            and n.get('state') is not None
            and n['state'] != parent_state
        )
        for c in n.get('children', []):
            flag_changes(c, n['state'])
    flag_changes(tree, None)

    return tree, {'rate': round(r_best, 4), 'log_likelihood': round(logl, 3)}


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


def _liebermann_score_only(tree_root, tip_states):
    """Fast downward+upward Liebermann pass that returns only the ambiguity
    score (sum of |state set| - 1 over all polymorphic nodes). Operates
    directly on tree_root without copying/annotating it — mirrors
    _fitch_score_only. Used for permutation testing.
    """
    node_states = {}

    def downward(n):
        children = n.get('children', [])
        if not children:
            name = _norm_name(n.get('name', ''))
            s = tip_states.get(name)
            r = set(s) if s else None
            node_states[id(n)] = r
            return r
        child_sets = [downward(c) for c in children]
        non_null = [cs for cs in child_sets if cs is not None]
        if not non_null:
            node_states[id(n)] = None
        elif len(non_null) == 1:
            node_states[id(n)] = set(non_null[0])
        else:
            inter = non_null[0].copy()
            for cs in non_null[1:]:
                inter &= cs
            if inter:
                node_states[id(n)] = inter
            else:
                union = set()
                for cs in non_null:
                    union |= cs
                node_states[id(n)] = union
        return node_states[id(n)]

    downward(tree_root)

    def upward(n, parent_state):
        ns = node_states.get(id(n))
        if parent_state is not None and ns is not None:
            if parent_state <= ns:
                if len(ns) > len(parent_state):
                    ns = ns & parent_state
                    node_states[id(n)] = ns
            else:
                children = n.get('children', [])
                child_sets = [node_states.get(id(c)) for c in children]
                non_null = [cs for cs in child_sets if cs is not None]
                if len(non_null) >= 2:
                    pairwise_inter = non_null[0].copy()
                    for cs in non_null[1:]:
                        pairwise_inter &= cs
                    if pairwise_inter:
                        union_children = set()
                        for cs in non_null:
                            union_children |= cs
                        additional = parent_state & union_children
                    else:
                        additional = parent_state - ns
                    ns = ns | additional
                    node_states[id(n)] = ns
        for c in n.get('children', []):
            upward(c, ns)

    upward(tree_root, None)

    ambiguity = 0
    for s in node_states.values():
        if s is not None and len(s) > 1:
            ambiguity += len(s) - 1
    return ambiguity


def _compute_signal(method, tree_root, tip_states, observed_score, extra=None, n_perm=499):
    """Permutation-based phylogenetic-signal test, generalized across the
    three optimization methods.

    All three share the same null model: shuffle which tip gets which
    (canonical, single-valued) observed state, keep the tree fixed, and see
    how often a random assignment scores at least as well as the real data.
    A low p-value means the real distribution of states on the tree is
    unlikely to arise by chance — i.e. there is phylogenetic signal.

    CI/RI (Consistency/Retention Index) are specific to parsimony step
    counts, so they are only computed for method='fitch'; other methods get
    ci=ri=None and only the p-value.

    For 'mk_er', re-fitting the ML rate for every one of n_perm permutations
    would be far too slow, so the rate fitted on the *observed* data
    (extra['rate']) is reused as a fixed nuisance parameter when scoring each
    permutation — the same approximation classically used for parametric
    permutation tests where refitting is prohibitive.

    tip_states: {normalized_species_name: set_of_state_codes}
    Returns {'ci', 'ri', 'p_value', 'note'}.
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
        ci = ri = 1.0 if method == 'fitch' else None
        return {'ci': ci, 'ri': ri, 'p_value': None, 'note': 'invariant'}
    if n_taxa < 3:
        return {'ci': None, 'ri': None, 'p_value': None, 'note': 'insufficient_data'}

    ci = ri = None
    if method == 'fitch':
        m = k - 1                                   # minimum possible steps
        g = n_taxa - max(state_counts.values())     # maximum possible steps
        s = observed_score
        ci = round(m / s, 3) if s > 0 else 1.0
        ri_denom = g - m
        ri_raw   = ((g - s) / ri_denom) if ri_denom > 0 else 1.0
        ri       = round(max(0.0, min(1.0, ri_raw)), 3)

    if observed_score is None:
        return {'ci': ci, 'ri': ri, 'p_value': None, 'note': None}

    # Permutation test
    leaf_names   = list(canon.keys())
    leaf_states  = [canon[nm] for nm in leaf_names]
    shuffled     = leaf_states[:]

    if method == 'liebermann':
        score_fn = lambda perm_tip: _liebermann_score_only(tree_root, perm_tip)
    elif method == 'mk_er':
        states_list = sorted(state_counts.keys())
        r_fixed = (extra or {}).get('rate') or 0.1
        score_fn = lambda perm_tip: -_mk_pruning_loglik(tree_root, perm_tip, states_list, r_fixed)
    else:
        score_fn = lambda perm_tip: _fitch_score_only(tree_root, perm_tip)

    n_le = 0
    for _ in range(n_perm):
        random.shuffle(shuffled)
        perm_tip = {leaf_names[i]: {shuffled[i]} for i in range(len(leaf_names))}
        if score_fn(perm_tip) <= observed_score:
            n_le += 1

    p_value = round((n_le + 1) / (n_perm + 1), 4)

    return {'ci': ci, 'ri': ri, 'p_value': p_value, 'note': None}


OPTIMIZATION_METHODS = {'fitch', 'liebermann', 'mk_er'}


def _run_method(method, tree_root, tip_states):
    """Dispatch to the selected optimization method. Returns
    (annotated_tree, score, score_label, signal, extra) where `score` is
    whatever summary number that method reports (lower isn't universally
    "better" across methods — score_label says what it is), `signal` is the
    CI/RI/permutation-test block (Fitch-specific, None otherwise — a
    permutation test against parsimony-step nulls wouldn't mean anything for
    a likelihood or polymorphism score), and `extra` carries method-specific
    fields (e.g. the fitted rate for Mk-ER) merged into the result dict.
    """
    if method == 'liebermann':
        annotated, stats = _liebermann_optimize(tree_root, tip_states)
        signal = _compute_signal('liebermann', tree_root, tip_states, stats['ambiguity'])
        return (annotated, stats['ambiguity'], 'ambiguity', signal,
                {'liebermann_stats': stats})
    if method == 'mk_er':
        annotated, info = _mk_er_optimize(tree_root, tip_states)
        score = -info['log_likelihood'] if info['log_likelihood'] is not None else None
        signal = _compute_signal('mk_er', tree_root, tip_states, score,
                                  extra={'rate': info['rate']})
        return (annotated, score, '-logL', signal,
                {'rate': info['rate'], 'log_likelihood': info['log_likelihood']})
    # default: fitch
    annotated, pscore = _fitch_parsimony(tree_root, tip_states)
    signal = _compute_signal('fitch', tree_root, tip_states, pscore)
    return annotated, pscore, 'steps', signal, {}


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
    method = body.get('method') or 'fitch'
    if method not in OPTIMIZATION_METHODS:
        return jsonify({'error': f'Unknown optimization method "{method}".'}), 400
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

        annotated, score, score_label, signal, extra = _run_method(method, tree_root, tip_states)

        result = {
            'id': char.id,
            'code': char.code,
            'name': char.name,
            'structure_type': char.structure_type,
            'method': method,
            'score': score,
            'score_label': score_label,
            'parsimony_score': score,   # back-compat alias for existing JS reading this field
            'signal': signal,
            'states': [
                {'code': s.get('code', ''), 'name': s.get('name', '')}
                for s in (char.states_json or [])
            ],
            'tree': annotated,
        }
        result.update(extra)
        results.append(result)

    # Specimen-level ecological characters (host habitat, distribution, host
    # family/order). Included by default; when an explicit selection was sent,
    # only those whose id was requested.
    sp_by_id = {sp.id: sp for sp in specimens}
    for desc in _virtual_char_descriptors():
        if char_ids and desc['id'] not in virtual_ids:
            continue
        vres = _virtual_char_result(desc, specimens, species_to_sp_ids,
                                    sp_by_id, alias_map, tree_root, method)
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
