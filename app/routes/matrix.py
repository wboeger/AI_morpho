import re
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import (
    Project, Specimen, Structure, CharacterDefinition, CharacterValue,
    CorrectionHistory, ActivityLog
)

matrix_bp = Blueprint('matrix', __name__)


@matrix_bp.route('/project/<int:project_id>/matrix')
@login_required
def matrix_view(project_id):
    project = Project.query.get_or_404(project_id)
    structure_filter = request.args.get('structure_type', '')
    dna_only = request.args.get('dna_only') == '1'
    unconfirmed_only = request.args.get('unconfirmed') == '1'

    # Get active characters — only for structure types that have data
    char_query = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code)
    if structure_filter:
        char_query = char_query.filter_by(structure_type=structure_filter)
    characters = char_query.all()

    # Get all species in project
    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()

    if dna_only:
        from app.models import DNASequence
        ids_with_dna = {s.specimen_id for s in
                        DNASequence.query.filter(
                            DNASequence.specimen_id.in_([sp.id for sp in specimens]),
                            DNASequence.available == True
                        ).all()}
        specimens = [s for s in specimens if s.id in ids_with_dna]

    # Build matrix: species -> {char_code -> value dict}
    matrix_data = []
    for specimen in specimens:
        structures = Structure.query.filter_by(specimen_id=specimen.id).all()
        row = {'specimen': specimen, 'cells': {}}

        for char in characters:
            # Find the structure of matching type
            struct = next((s for s in structures if s.structure_type == char.structure_type), None)
            if struct:
                val = CharacterValue.query.filter_by(
                    structure_id=struct.id, character_id=char.id
                ).first()
                if val:
                    row['cells'][char.code] = {
                        'id': val.id,
                        'state': val.state,
                        'raw_value': val.raw_value,
                        'confidence': val.confidence,
                        'auto_assigned': val.auto_assigned,
                    }
                else:
                    row['cells'][char.code] = None
            else:
                row['cells'][char.code] = None

        if unconfirmed_only:
            has_unconfirmed = any(
                v is not None and v.get('state') == '?'
                for v in row['cells'].values()
            )
            if not has_unconfirmed:
                continue

        matrix_data.append(row)

    # If a phylogenetic tree exists, reorder rows to match tree leaf order
    if project.tree_newick:
        leaf_order = _parse_leaf_order(project.tree_newick)
        used_ids = set()
        ordered = []
        for leaf in leaf_order:
            for row in matrix_data:
                sp_id = row['specimen'].id
                if sp_id not in used_ids and _match_leaf(leaf, row['specimen'].species_name):
                    ordered.append(row)
                    used_ids.add(sp_id)
                    break
        # Append any specimens not matched in the tree (at the end)
        for row in matrix_data:
            if row['specimen'].id not in used_ids:
                ordered.append(row)
        matrix_data = ordered

    return render_template('matrix/matrix_view.html',
                           project=project, characters=characters,
                           matrix_data=matrix_data,
                           structure_filter=structure_filter,
                           dna_only=dna_only, unconfirmed_only=unconfirmed_only,
                           has_tree=bool(project.tree_newick))


@matrix_bp.route('/project/<int:project_id>/matrix/gallery/<int:char_id>')
@login_required
def gallery_view(project_id, char_id):
    project = Project.query.get_or_404(project_id)
    char = CharacterDefinition.query.get_or_404(char_id)
    from config import Config

    # Get structures of matching type
    structures = (Structure.query
                  .join(Specimen)
                  .filter(Specimen.project_id == project_id,
                          Structure.structure_type == char.structure_type)
                  .all())

    entries = []

    if structures:
        # Normal case: entries built from structures of the character's type
        for struct in structures:
            specimen = Specimen.query.get(struct.specimen_id)
            val = CharacterValue.query.filter_by(
                structure_id=struct.id, character_id=char.id
            ).first()

            alt_structures = {}
            for st in Structure.query.filter_by(specimen_id=specimen.id).all():
                alt_structures[st.structure_type] = {
                    'image_url': f'/uploads/{st.image_path}' if st.image_path else None,
                    'landmarks': st.landmarks_json,
                    'boundaries': st.boundary_json,
                }

            entries.append({
                'structure': struct,
                'specimen': specimen,
                'value': val,
                'image_url': f'/uploads/{struct.image_path}' if struct.image_path else None,
                'landmarks': struct.landmarks_json,
                'boundaries': struct.boundary_json,
                'alt_structures': alt_structures,
            })
    else:
        # No structures of this type exist — show all specimens with
        # images/shapes from other structure types for reference while coding
        specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()
        for specimen in specimens:
            all_structs = Structure.query.filter_by(specimen_id=specimen.id).all()
            if not all_structs:
                continue

            alt_structures = {}
            for st in all_structs:
                alt_structures[st.structure_type] = {
                    'image_url': f'/uploads/{st.image_path}' if st.image_path else None,
                    'landmarks': st.landmarks_json,
                    'boundaries': st.boundary_json,
                }

            # Use the first available structure as a stand-in for state assignment
            primary = all_structs[0]
            val = CharacterValue.query.filter_by(
                structure_id=primary.id, character_id=char.id
            ).first()

            entries.append({
                'structure': primary,
                'specimen': specimen,
                'value': val,
                'image_url': f'/uploads/{primary.image_path}' if primary.image_path else None,
                'landmarks': primary.landmarks_json,
                'boundaries': primary.boundary_json,
                'alt_structures': alt_structures,
            })

    # Sort by raw_value for geometric, by state for manual
    if char.computation_type == 'geometric':
        entries.sort(key=lambda e: (e['value'].raw_value if e['value'] and e['value'].raw_value is not None else 0))
    else:
        entries.sort(key=lambda e: (e['value'].state if e['value'] and e['value'].state else '?'))

    parts = Config.STRUCTURE_PARTS.get(char.structure_type, [])

    # Find which structure types actually have data in this project
    available_types = sorted({st.structure_type for st in
        Structure.query.join(Specimen).filter(Specimen.project_id == project_id).all()})

    return render_template('matrix/gallery.html',
                           project=project, char=char, entries=entries,
                           parts=parts, available_types=available_types,
                           all_parts=Config.STRUCTURE_PARTS)


@matrix_bp.route('/project/<int:project_id>/matrix/code/<int:structure_id>')
@login_required
def manual_coding(project_id, structure_id):
    """Manual coding interface for bar and MCO characters."""
    project = Project.query.get_or_404(project_id)
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    # Get manual characters for this structure type
    characters = CharacterDefinition.query.filter_by(
        project_id=project_id,
        structure_type=structure.structure_type,
        computation_type='manual',
        active=True
    ).order_by(CharacterDefinition.code).all()

    # Get existing values
    values = {}
    for char in characters:
        val = CharacterValue.query.filter_by(
            structure_id=structure.id, character_id=char.id
        ).first()
        values[char.code] = val

    image_url = f'/uploads/{structure.image_path}' if structure.image_path else None

    # Count remaining uncoded specimens for progress bar
    total_structures = (Structure.query
                        .join(Specimen)
                        .filter(Specimen.project_id == project_id,
                                Structure.structure_type == structure.structure_type)
                        .count())

    return render_template('matrix/manual_coding.html',
                           project=project, structure=structure,
                           specimen=specimen, characters=characters,
                           values=values, image_url=image_url,
                           total_structures=total_structures)


@matrix_bp.route('/api/project/<int:project_id>/matrix/override', methods=['POST'])
@login_required
def override_value(project_id):
    data = request.get_json()
    value_id = data.get('value_id')
    new_state = data.get('state')
    reason = data.get('reason', '')

    val = CharacterValue.query.get_or_404(value_id)
    old_state = val.state

    # Log correction
    correction = CorrectionHistory(
        project_id=project_id,
        structure_id=val.structure_id,
        character_id=val.character_id,
        old_state=old_state,
        new_state=new_state,
        reason=reason,
        user_id=current_user.id,
    )
    db.session.add(correction)

    val.state = new_state
    val.override_by = current_user.id
    val.override_reason = reason
    val.override_at = datetime.now(timezone.utc)
    val.confidence = 1.0

    db.session.commit()
    return jsonify({'status': 'ok', 'old_state': old_state, 'new_state': new_state})


@matrix_bp.route('/api/project/<int:project_id>/matrix/assign', methods=['POST'])
@login_required
def assign_manual_value(project_id):
    """Assign a manual character value (for bar/MCO coding)."""
    data = request.get_json()
    structure_id = data.get('structure_id')
    character_id = data.get('character_id')
    state = data.get('state')

    # Check dependencies
    char = CharacterDefinition.query.get_or_404(character_id)
    structure = Structure.query.get_or_404(structure_id)

    # If the structure type doesn't match the character's type,
    # create or find a structure of the correct type for this specimen
    if structure.structure_type != char.structure_type:
        correct_struct = Structure.query.filter_by(
            specimen_id=structure.specimen_id,
            structure_type=char.structure_type
        ).first()
        if not correct_struct:
            correct_struct = Structure(
                specimen_id=structure.specimen_id,
                structure_type=char.structure_type,
            )
            db.session.add(correct_struct)
            db.session.flush()
        structure = correct_struct
        structure_id = structure.id

    from app.characters import check_dependencies
    if check_dependencies(char, structure, project_id):
        state = '-'

    val = CharacterValue.query.filter_by(
        structure_id=structure_id, character_id=character_id
    ).first()

    if val:
        val.state = state
        val.confidence = 1.0
        val.auto_assigned = False
        val.override_by = current_user.id
        val.override_at = datetime.now(timezone.utc)
        val.reviewer_id = current_user.id
    else:
        val = CharacterValue(
            structure_id=structure_id,
            character_id=character_id,
            state=state,
            confidence=1.0,
            auto_assigned=False,
            override_by=current_user.id,
            override_at=datetime.now(timezone.utc),
            reviewer_id=current_user.id,
        )
        db.session.add(val)

    db.session.commit()
    return jsonify({'status': 'ok', 'state': state})


@matrix_bp.route('/api/project/<int:project_id>/matrix/cell_detail', methods=['GET'])
@login_required
def cell_detail(project_id):
    """Get detailed info for a matrix cell popup."""
    value_id = request.args.get('value_id', type=int)
    val = CharacterValue.query.get_or_404(value_id)
    char = CharacterDefinition.query.get(val.character_id)
    structure = Structure.query.get(val.structure_id)
    specimen = Specimen.query.get(structure.specimen_id)

    return jsonify({
        'species': specimen.species_name,
        'character': char.name,
        'code': char.code,
        'state': val.state,
        'raw_value': val.raw_value,
        'confidence': val.confidence,
        'auto_assigned': val.auto_assigned,
        'override_reason': val.override_reason,
        'states': char.states_json,
        'computation_type': char.computation_type,
        'image_url': f'/uploads/{structure.image_path}' if structure.image_path else None,
        'landmarks': structure.landmarks_json,
        'boundaries': structure.boundary_json,
    })


def _parse_leaf_order(newick: str) -> list:
    """Return tip labels in left-to-right order as they appear in the Newick string."""
    s = newick.strip().rstrip(';')
    leaves = []
    idx = [0]

    def parse():
        is_leaf = True
        if idx[0] < len(s) and s[idx[0]] == '(':
            is_leaf = False
            idx[0] += 1
            parse()
            while idx[0] < len(s) and s[idx[0]] == ',':
                idx[0] += 1
                parse()
            if idx[0] < len(s) and s[idx[0]] == ')':
                idx[0] += 1
        name = ''
        while idx[0] < len(s) and s[idx[0]] not in ':,);':
            name += s[idx[0]]
            idx[0] += 1
        name = name.strip().strip("'\"")
        if idx[0] < len(s) and s[idx[0]] == ':':
            idx[0] += 1
            while idx[0] < len(s) and s[idx[0]] not in ',);':
                idx[0] += 1
        if is_leaf and name:
            leaves.append(name)

    try:
        parse()
    except Exception:
        pass
    return leaves


def _normalize_leaf_label(label: str) -> str:
    """Normalize a Newick leaf label for species matching.

    Handles formats like:
      - 'KX981461.1|Aglaiogyrodactylus_forficulatus'
      - '_R_HF548677.1|Gyrodactyloides_sp.'
      - 'Gyrodactylus_derjavinoides'
    Returns a lowercase, space-separated species name.
    """
    # Take the part after the last '|' if present (accession|species format)
    if '|' in label:
        label = label.split('|')[-1]
    # Strip leading _R_ (FigTree rotation flag)
    label = re.sub(r'^_R_', '', label, flags=re.IGNORECASE)
    # Replace underscores with spaces
    return label.replace('_', ' ').strip().lower()


def _match_leaf(leaf_label: str, species_name: str) -> bool:
    """Check if a tree leaf label matches a specimen species name."""
    # Skip FigTree annotation artifacts (e.g. '!rotate=false]')
    if '!' in leaf_label or '=' in leaf_label:
        return False
    leaf_norm = _normalize_leaf_label(leaf_label)
    sn_norm = species_name.strip().lower()
    if leaf_norm == sn_norm:
        return True
    leaf_parts = leaf_norm.split()
    sn_parts = sn_norm.split()
    if len(leaf_parts) >= 2 and len(sn_parts) >= 2:
        return leaf_parts[1] == sn_parts[1]
    return False


def _extract_newick_from_nexus(text: str) -> str:
    """Extract the first Newick tree string from a NEXUS-format file.

    Handles optional 'translate' blocks that map numeric IDs to taxon names.
    Returns a plain Newick string, or the original text if not NEXUS.
    """
    if not text.lstrip().upper().startswith('#NEXUS'):
        return text

    # Find the trees block
    trees_match = re.search(r'begin\s+trees\s*;(.*?)end\s*;', text,
                            re.IGNORECASE | re.DOTALL)
    if not trees_match:
        return text

    trees_block = trees_match.group(1)

    # Parse optional translate block: maps numeric/short IDs to full names
    translate = {}
    trans_match = re.search(r'translate\s+(.*?)\s*;', trees_block,
                            re.IGNORECASE | re.DOTALL)
    if trans_match:
        # Each entry is "ID name" separated by commas
        for entry in re.split(r',', trans_match.group(1)):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(None, 1)   # split on first whitespace only
            if len(parts) == 2:
                translate[parts[0]] = parts[1].strip("'\"")

    # Find first 'tree' statement
    tree_match = re.search(r'tree\s+\S+\s*=\s*(\[.*?\])?\s*(\(.*)', trees_block,
                           re.IGNORECASE | re.DOTALL)
    if not tree_match:
        return text

    newick = tree_match.group(2).strip()
    # Trim to the first semicolon
    semi = newick.find(';')
    if semi != -1:
        newick = newick[:semi + 1]

    # Strip all square-bracket annotations (FigTree, BEAST, MrBayes, etc.)
    newick = re.sub(r'\[.*?\]', '', newick)

    # Apply translate mappings: replace numeric labels in Newick
    if translate:
        def replace_id(m):
            key = m.group(1)
            return translate.get(key, key)
        newick = re.sub(r'(?<=[,()])(\d+)(?=[,:);])', replace_id, newick)

    return newick


def _clean_newick_labels(newick: str, project_id: int) -> str:
    """Clean Newick tip labels to match specimen species names in the project.

    Tree files often have labels like 'Gyrodactylus_turnbulli_KX231834' or
    'G_turnbulli_123_isolate1'. This extracts the species epithet by:
    1. Replacing underscores with spaces
    2. Stripping GenBank accession numbers (e.g. KX231834, MN123456)
    3. Stripping trailing numeric IDs, isolate labels, etc.
    4. Matching against known species names in the project
    """
    # Get all species names in the project for matching
    specimens = Specimen.query.filter_by(project_id=project_id).all()
    known_epithets = {}
    for sp in specimens:
        # Build lookup: species epithet (last word) -> full species name
        parts = sp.species_name.strip().split()
        if len(parts) >= 2:
            epithet = parts[-1].lower()
            known_epithets[epithet] = sp.species_name
        # Also index full name lowered
        known_epithets[sp.species_name.lower().replace(' ', '_')] = sp.species_name

    # GenBank accession pattern: 1-2 letters + 5-8 digits
    accession_re = re.compile(r'\b[A-Z]{1,2}\d{5,8}\b')

    def clean_label(match):
        label = match.group(1)
        original = label

        # Replace underscores with spaces for processing
        clean = label.replace('_', ' ').strip()

        # Remove GenBank accession numbers
        clean = accession_re.sub('', clean).strip()

        # Remove trailing pure-numeric tokens (isolate numbers, sample IDs)
        tokens = clean.split()
        while tokens and re.match(r'^\d+$', tokens[-1]):
            tokens.pop()

        # Remove common suffixes like 'isolate', 'voucher', 'clone' and anything after
        stop_words = {'isolate', 'voucher', 'clone', 'specimen', 'sample', 'seq', 'sequence'}
        filtered = []
        for t in tokens:
            if t.lower() in stop_words:
                break
            filtered.append(t)
        tokens = filtered if filtered else tokens

        # Try to match against known species names
        # First try: exact match on epithet (second word)
        if len(tokens) >= 2:
            epithet = tokens[1].lower()
            if epithet in known_epithets:
                return known_epithets[epithet].replace(' ', '_')

        # Second try: match any token as epithet
        for t in tokens:
            if t.lower() in known_epithets:
                return known_epithets[t.lower()].replace(' ', '_')

        # Fallback: keep genus + species (first two tokens), underscored
        if len(tokens) >= 2:
            return '_'.join(tokens[:2])
        return original

    # Match tip labels in Newick: any text that is NOT ( ) , ; :
    # Tip labels appear before : or , or ) — capture the label
    result = re.sub(r'([A-Za-z][A-Za-z0-9_ ]*?)(?=\s*[;:,\)])', clean_label, newick)
    return result


@matrix_bp.route('/project/<int:project_id>/tree/upload', methods=['POST'])
@login_required
def upload_tree(project_id):
    project = Project.query.get_or_404(project_id)

    if 'tree_file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided', 'has_tree': False})

    try:
        f = request.files['tree_file']
        raw = f.read().decode('utf-8').strip()
        if not raw:
            return jsonify({'status': 'error', 'message': 'File is empty', 'has_tree': False})

        # Support both NEXUS (.nex/.nexus) and plain Newick formats
        newick = _extract_newick_from_nexus(raw)
        cleaned = _clean_newick_labels(newick, project_id)

        if not cleaned or not cleaned.strip('();, \t\n'):
            return jsonify({'status': 'error', 'message': 'No tree found in file', 'has_tree': False})

        project.tree_newick = cleaned
        db.session.commit()
        return jsonify({'status': 'ok', 'has_tree': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e), 'has_tree': bool(project.tree_newick)})


@matrix_bp.route('/api/project/<int:project_id>/tree', methods=['GET'])
@login_required
def get_tree(project_id):
    project = Project.query.get_or_404(project_id)
    return jsonify({'newick': project.tree_newick or ''})
