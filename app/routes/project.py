import os
import re
import csv
import io
import json
import glob
import shutil
import numpy as np

# ---------------------------------------------------------------------------
# Upload-folder helpers
# ---------------------------------------------------------------------------
_PROJ_LABEL = {1: '18S', 2: 'ITS'}

def _upload_subdir(project_id: int, species_name: str) -> str:
    """Return '18S', 'ITS', or 'Common' for a specimen based on whether the
    species appears in more than one project."""
    from app.models import Specimen as _Sp
    count = (db.session.query(_Sp.project_id)
             .filter(_Sp.species_name == species_name)
             .distinct()
             .count())
    if count > 1:
        return 'Common'
    return _PROJ_LABEL.get(project_id, str(project_id))


def _structure_subdir(structure_type: str) -> str:
    """Map a structure type to its consolidated image subdirectory."""
    if structure_type == 'mco':
        return 'MCO'
    if structure_type == 'hook':
        return 'hook'
    if structure_type == 'anchor':
        return 'haptor'
    if structure_type in ('superficial_bar', 'deep_bar'):
        return 'bar'
    return '_unsorted'


def _store_structure_bytes(raw: bytes, filename: str, structure_type: str) -> str:
    """Save image bytes into data/uploads/structures/<type>/, deduplicated by
    content. If an identical image already exists anywhere under structures/,
    reuse it instead of writing a second copy. Returns the path relative to
    UPLOAD_FOLDER."""
    import os as _os
    import re as _re
    import hashlib as _hashlib
    from werkzeug.utils import secure_filename as _secure

    root = current_app.config['UPLOAD_FOLDER']
    base = _os.path.join(root, 'structures')
    digest = _hashlib.md5(raw).hexdigest()

    # Dedup: reuse an existing file with identical content.
    for r, _dirs, files in _os.walk(base):
        for fn in files:
            fp = _os.path.join(r, fn)
            try:
                with open(fp, 'rb') as fh:
                    if _hashlib.md5(fh.read()).hexdigest() == digest:
                        return _os.path.relpath(fp, root)
            except OSError:
                continue

    sub = _structure_subdir(structure_type)
    dest_dir = _os.path.join(base, sub)
    _os.makedirs(dest_dir, exist_ok=True)
    name = _re.sub(r'^\d+_', '', _secure(filename) or 'image.png')
    stem, ext = _os.path.splitext(name)
    cand, n = name, 1
    while _os.path.exists(_os.path.join(dest_dir, cand)):
        n += 1
        cand = f'{stem}_{n}{ext}'
    with open(_os.path.join(dest_dir, cand), 'wb') as out:
        out.write(raw)
    return _os.path.relpath(_os.path.join(dest_dir, cand), root)


def _save_structure_image(file_storage, structure_type: str) -> str:
    """Persist an uploaded structure image (FileStorage) under the consolidated
    scheme with content deduplication."""
    return _store_structure_bytes(file_storage.read(), file_storage.filename,
                                  structure_type)


_NO_IMAGE_REASON = 'no image — coded unknown'


def _apply_no_image_unknown(structure, uid=None):
    """Set every active character of this structure's type to '?' (unknown),
    because the structure has no image. Marked with _NO_IMAGE_REASON so it can
    be cleanly reverted if the 'no image' flag is removed."""
    from datetime import datetime, timezone
    from app.models import CharacterDefinition, CharacterValue, Specimen as _Sp
    specimen = _Sp.query.get(structure.specimen_id)
    chars = CharacterDefinition.query.filter_by(
        project_id=specimen.project_id,
        structure_type=structure.structure_type,
        active=True).all()
    for ch in chars:
        v = CharacterValue.query.filter_by(
            structure_id=structure.id, character_id=ch.id).first()
        if v:
            v.state = '?'
            v.confidence = 0.0
            v.auto_assigned = True
            v.override_reason = _NO_IMAGE_REASON
            v.override_by = uid
            v.override_at = datetime.now(timezone.utc)
        else:
            db.session.add(CharacterValue(
                structure_id=structure.id, character_id=ch.id,
                state='?', confidence=0.0, auto_assigned=True,
                override_reason=_NO_IMAGE_REASON, override_by=uid,
                override_at=datetime.now(timezone.utc), reviewer_id=uid))


def _clear_no_image_unknown(structure):
    """Remove the '?' values that were auto-set because of a 'no image' flag."""
    from app.models import CharacterValue
    for v in CharacterValue.query.filter_by(
            structure_id=structure.id, override_reason=_NO_IMAGE_REASON).all():
        db.session.delete(v)
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db, DOC_FILES
from app.models import (
    Project, ProjectMembership, Specimen, Structure, DNASequence, ActivityLog, User,
    SpecimenComment
)
from app.characters import initialize_project_characters

project_bp = Blueprint('project', __name__)


@project_bp.route('/')
@login_required
def dashboard():
    # Projects where user is creator or member
    owned = Project.query.filter_by(created_by=current_user.id).all()
    member_ids = [m.project_id for m in
                  ProjectMembership.query.filter_by(user_id=current_user.id).all()]
    member_projects = Project.query.filter(Project.id.in_(member_ids)).all() if member_ids else []
    projects = list({p.id: p for p in owned + member_projects}.values())
    return render_template('project/dashboard.html', projects=projects)


@project_bp.route('/docs/<path:filename>')
@login_required
def serve_doc(filename):
    """Download a user manual from the persistent volume (DATA_DIR/docs)."""
    from flask import send_from_directory, abort
    if filename not in DOC_FILES:
        abort(404)
    return send_from_directory(current_app.config['DOCS_FOLDER'],
                               filename, as_attachment=True)


@project_bp.route('/project/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash('Project name is required.', 'error')
        else:
            project = Project(name=name, description=description, created_by=current_user.id)
            db.session.add(project)
            db.session.flush()

            # Add creator as admin member
            membership = ProjectMembership(
                user_id=current_user.id, project_id=project.id, role='admin'
            )
            db.session.add(membership)

            # Initialize default character library
            initialize_project_characters(project.id, current_user.id)

            _log(project.id, f'Created project "{name}"')
            db.session.commit()

            flash('Project created with default character library.', 'success')
            return redirect(url_for('project.view_project', project_id=project.id))

    return render_template('project/new_project.html')


@project_bp.route('/project/<int:project_id>')
@login_required
def view_project(project_id):
    project = _get_project_or_404(project_id)
    dna_only = request.args.get('dna_only') == '1'

    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()

    if dna_only:
        specimen_ids_with_dna = {s.specimen_id for s in
                                 DNASequence.query.filter(
                                     DNASequence.specimen_id.in_([sp.id for sp in specimens]),
                                     DNASequence.available == True
                                 ).all()}
        specimens = [s for s in specimens if s.id in specimen_ids_with_dna]

    members = ProjectMembership.query.filter_by(project_id=project_id).all()
    member_user_ids = {m.user_id for m in members}
    # Users not yet on the project — offered for sharing autocomplete
    shareable_users = (User.query
                       .filter(User.active == True, ~User.id.in_(member_user_ids or [0]))
                       .order_by(User.username)
                       .all())
    can_manage_members = (project.created_by == current_user.id or
                          any(m.user_id == current_user.id and m.role == 'admin'
                              for m in members))

    # Compute progress stats
    total_structures = Structure.query.join(Specimen).filter(
        Specimen.project_id == project_id).count()
    landmarks_done = Structure.query.join(Specimen).filter(
        Specimen.project_id == project_id, Structure.landmarks_confirmed == True).count()
    boundaries_done = Structure.query.join(Specimen).filter(
        Specimen.project_id == project_id, Structure.boundary_confirmed == True).count()

    stats = {
        'specimens': len(specimens),
        'structures': total_structures,
        'landmarks_done': landmarks_done,
        'boundaries_done': boundaries_done,
    }

    all_structure_types = ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']
    structure_types = ['mco'] if 'MCO' in project.name.upper() else all_structure_types

    return render_template('project/view_project.html',
                           project=project, specimens=specimens,
                           members=members, stats=stats, dna_only=dna_only,
                           structure_types=structure_types,
                           shareable_users=shareable_users,
                           can_manage_members=can_manage_members,
                           project_owner_id=project.created_by)


@project_bp.route('/project/<int:project_id>/specimen/new', methods=['GET', 'POST'])
@login_required
def add_specimen(project_id):
    project = _get_project_or_404(project_id)

    if request.method == 'POST':
        species_name = request.form.get('species_name', '').strip()
        specimen_id_label = request.form.get('specimen_id_label', '').strip()
        notes = request.form.get('notes', '').strip()

        if not species_name:
            flash('Species name is required.', 'error')
        else:
            # Handle image upload
            image_path = None
            if 'image' in request.files:
                f = request.files['image']
                if f.filename:
                    filename = secure_filename(f.filename)
                    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'],
                                              'structures', '_specimen')
                    os.makedirs(upload_dir, exist_ok=True)
                    filepath = os.path.join(upload_dir, filename)
                    f.save(filepath)
                    image_path = os.path.relpath(filepath, current_app.config['UPLOAD_FOLDER'])

            specimen = Specimen(
                project_id=project_id, species_name=species_name,
                specimen_id_label=specimen_id_label, image_path=image_path,
                notes=notes, created_by=current_user.id
            )
            db.session.add(specimen)
            db.session.flush()

            # Handle DNA markers
            for marker in ['ITS', '18S', 'COI']:
                if request.form.get(f'dna_{marker}'):
                    accession = request.form.get(f'accession_{marker}', '').strip()
                    dna = DNASequence(
                        specimen_id=specimen.id, marker=marker,
                        accession=accession, available=True
                    )
                    db.session.add(dna)

            _log(project_id, f'Added specimen {species_name}')
            db.session.commit()
            flash('Specimen added.', 'success')
            return redirect(url_for('project.view_project', project_id=project_id))

    return render_template('project/add_specimen.html', project=project)


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/synonyms', methods=['POST'])
@login_required
def add_synonym(project_id, specimen_id):
    """Add a synonym to a specimen."""
    _get_project_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    syn = ((request.get_json() or {}).get('synonym') or '').strip()
    if not syn:
        return jsonify({'error': 'Synonym required'}), 400
    syns = list(specimen.synonyms or [])
    if syn in syns:
        return jsonify({'error': 'Already exists'}), 400
    syns.append(syn)
    specimen.synonyms = syns
    db.session.commit()
    return jsonify({'status': 'ok', 'synonyms': syns})


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/synonyms/remove',
                  methods=['POST'])
@login_required
def remove_synonym(project_id, specimen_id):
    """Remove a synonym from a specimen."""
    _get_project_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    syn = ((request.get_json() or {}).get('synonym') or '').strip()
    syns = [s for s in (specimen.synonyms or []) if s != syn]
    specimen.synonyms = syns
    db.session.commit()
    return jsonify({'status': 'ok', 'synonyms': syns})


def _comment_json(c):
    return {
        'id': c.id,
        'body': c.body,
        'author': c.author.username if c.author else '—',
        'author_id': c.created_by,
        'created_at': (c.created_at.strftime('%Y-%m-%d %H:%M')
                       if c.created_at else ''),
    }


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/comments',
                  methods=['GET'])
@login_required
def list_comments(project_id, specimen_id):
    """List all comments on a specimen, oldest first."""
    _get_project_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    return jsonify({'status': 'ok',
                    'comments': [_comment_json(c) for c in specimen.comments]})


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/comments',
                  methods=['POST'])
@login_required
def add_comment(project_id, specimen_id):
    """Add a comment to a specimen."""
    _get_project_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    body = ((request.get_json() or {}).get('body') or '').strip()
    if not body:
        return jsonify({'error': 'Comment text required'}), 400
    c = SpecimenComment(specimen_id=specimen.id, body=body,
                        created_by=current_user.id)
    db.session.add(c)
    db.session.commit()
    return jsonify({'status': 'ok', 'comment': _comment_json(c)})


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/comments/<int:comment_id>',
                  methods=['DELETE'])
@login_required
def delete_comment(project_id, specimen_id, comment_id):
    """Delete a comment. Only the author or an admin may delete."""
    _get_project_or_404(project_id)
    c = SpecimenComment.query.get_or_404(comment_id)
    if c.specimen_id != specimen_id:
        return jsonify({'error': 'Comment does not belong to this specimen'}), 403
    if c.created_by != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Not allowed'}), 403
    db.session.delete(c)
    db.session.commit()
    return jsonify({'status': 'ok'})


@project_bp.route('/api/project/<int:project_id>/specimen/<int:specimen_id>/delete', methods=['DELETE'])
@login_required
def delete_specimen(project_id, specimen_id):
    """Delete a specimen and all its structures, character values, and DNA sequences."""
    project = _get_project_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    if specimen.project_id != project_id:
        return jsonify({'error': 'Specimen does not belong to this project'}), 403

    species_name = specimen.species_name
    db.session.delete(specimen)
    _log(project_id, f'Deleted specimen {species_name}')
    db.session.commit()
    return jsonify({'status': 'ok', 'species': species_name})


@project_bp.route('/api/project/<int:project_id>/readiness_check', methods=['POST'])
@login_required
def readiness_check(project_id):
    """Check which specimens are missing or incomplete for selected structure types."""
    _get_project_or_404(project_id)
    data = request.get_json()
    required_types = data.get('structure_types', [])
    if not required_types:
        return jsonify({'error': 'No structure types selected.'}), 400

    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()
    results = []

    for sp in specimens:
        # When a specimen has duplicate structure types, pick the most complete one
        struct_map = {}
        for st in sp.structures:
            prev = struct_map.get(st.structure_type)
            if prev is None:
                struct_map[st.structure_type] = st
            else:
                # Score: boundary_confirmed(4) > boundary_json(3) > landmarks_confirmed(2) > landmarks_json(1)
                def score(s):
                    return (bool(s.boundary_confirmed)*4 + bool(s.boundary_json)*3 +
                            bool(s.landmarks_confirmed)*2 + bool(s.landmarks_json))
                if score(st) > score(prev):
                    struct_map[st.structure_type] = st
        blocking = []   # prevents processing
        warnings = []   # present but not confirmed
        for stype in required_types:
            st = struct_map.get(stype)
            struct_id = st.id if st else None
            if st is None:
                blocking.append({'structure': stype, 'structure_id': None,
                                 'specimen_id': sp.id, 'problem': 'structure missing'})
            elif not st.landmarks_json:
                blocking.append({'structure': stype, 'structure_id': struct_id,
                                 'specimen_id': sp.id, 'problem': 'no landmarks'})
            elif not st.boundary_json:
                blocking.append({'structure': stype, 'structure_id': struct_id,
                                 'specimen_id': sp.id, 'problem': 'no boundaries'})
            else:
                if not st.landmarks_confirmed:
                    warnings.append({'structure': stype, 'structure_id': struct_id,
                                     'specimen_id': sp.id, 'problem': 'landmarks not confirmed'})
                if not st.boundary_confirmed:
                    warnings.append({'structure': stype, 'structure_id': struct_id,
                                     'specimen_id': sp.id, 'problem': 'boundaries not confirmed'})
        if blocking or warnings:
            results.append({'species': sp.species_name, 'specimen_id': sp.id,
                            'blocking': blocking, 'warnings': warnings})

    n_blocking = sum(1 for r in results if r['blocking'])
    return jsonify({'status': 'ok', 'not_ready': results, 'total': len(specimens),
                    'n_blocking': n_blocking,
                    'n_warnings': len(results) - n_blocking,
                    'n_ready': len(specimens) - len(results)})


@project_bp.route('/api/structure/<int:structure_id>/quick_confirm', methods=['POST'])
@login_required
def quick_confirm(structure_id):
    """Confirm landmarks and/or boundaries without opening the editor."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)
    data = request.get_json()
    confirmed = data.get('confirm', [])
    if 'landmarks' in confirmed and structure.landmarks_json:
        structure.landmarks_confirmed = True
    if 'boundaries' in confirmed and structure.boundary_json:
        structure.boundary_confirmed = True
    _log(specimen.project_id,
         f'Quick-confirmed {", ".join(confirmed)} for {specimen.species_name} {structure.structure_type}')
    db.session.commit()
    return jsonify({'status': 'ok'})


@project_bp.route('/api/structure/<int:structure_id>/delete', methods=['DELETE'])
@login_required
def delete_structure(structure_id):
    """Delete a structure and all its character values."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)
    project = _get_project_or_404(specimen.project_id)

    stype = structure.structure_type
    db.session.delete(structure)
    _log(specimen.project_id, f'Deleted {stype} for {specimen.species_name}')
    db.session.commit()
    return jsonify({'status': 'ok', 'structure_type': stype})


@project_bp.route('/api/structure/<int:structure_id>/toggle_no_image', methods=['POST'])
@login_required
def toggle_no_image(structure_id):
    """Toggle the 'no image available' flag on a structure."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)
    _get_project_or_404(specimen.project_id)
    structure.no_image = not bool(structure.no_image)
    state = 'marked' if structure.no_image else 'cleared'
    if structure.no_image:
        _apply_no_image_unknown(structure, current_user.id)   # code characters as '?'
    else:
        _clear_no_image_unknown(structure)                    # revert the '?'
    _log(specimen.project_id,
         f'{state} no-image for {structure.structure_type} of {specimen.species_name}')
    db.session.commit()
    return jsonify({'status': 'ok', 'no_image': structure.no_image})


@project_bp.route('/api/specimen/<int:specimen_id>/mark_no_image', methods=['POST'])
@login_required
def mark_no_image_new(specimen_id):
    """Create a minimal structure record marked as no-image-available.

    Used when no structure record exists yet for this type.
    """
    specimen = Specimen.query.get_or_404(specimen_id)
    project = _get_project_or_404(specimen.project_id)
    structure_type = (request.get_json() or {}).get('structure_type', '')
    valid = ('hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco')
    if structure_type not in valid:
        return jsonify({'error': 'Invalid structure type'}), 400
    existing = Structure.query.filter_by(
        specimen_id=specimen_id, structure_type=structure_type).first()
    if existing:
        existing.no_image = True
        st = existing
    else:
        st = Structure(specimen_id=specimen_id, structure_type=structure_type, no_image=True)
        db.session.add(st)
    db.session.flush()                         # ensure st.id for character values
    _apply_no_image_unknown(st, current_user.id)
    _log(project.id, f'Marked no-image for {structure_type} of {specimen.species_name}')
    db.session.commit()
    return jsonify({'status': 'ok', 'structure_id': st.id, 'no_image': True})


@project_bp.route('/specimen/<int:specimen_id>/structure/new', methods=['GET', 'POST'])
@login_required
def add_structure(specimen_id):
    specimen = Specimen.query.get_or_404(specimen_id)
    project = Project.query.get_or_404(specimen.project_id)

    if request.method == 'POST':
        structure_type = request.form.get('structure_type')
        if structure_type not in ('hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco'):
            flash('Invalid structure type.', 'error')
        else:
            # Handle optional image upload
            image_path = None
            if 'image' in request.files:
                f = request.files['image']
                if f.filename:
                    image_path = _save_structure_image(f, structure_type)

            from config import Config
            import numpy as np
            from app.geometry import resample_equidistant, suggest_landmark_count
            from app.routes.landmarks import _parse_imagej_csv

            # Handle optional CSV landmark upload
            landmarks_json = None
            landmark_count = Config.LANDMARK_COUNTS.get(structure_type, 100)
            if 'csv_file' in request.files:
                csv_file = request.files['csv_file']
                if csv_file.filename:
                    content = csv_file.read().decode('utf-8', errors='replace')
                    coords = _parse_imagej_csv(content)
                    if coords:
                        coords_arr = np.array(coords)
                        target = Config.LANDMARK_COUNTS.get(structure_type)
                        if target is None:
                            target = suggest_landmark_count(
                                resample_equidistant(coords_arr, 50), structure_type
                            )
                        landmarks_json = resample_equidistant(coords_arr, target).tolist()
                        landmark_count = target
                    else:
                        flash('CSV file had no valid coordinates — structure added without landmarks.', 'info')

            structure = Structure(
                specimen_id=specimen_id,
                structure_type=structure_type,
                image_path=image_path,
                landmarks_json=landmarks_json,
                landmark_count=landmark_count,
            )
            db.session.add(structure)
            _log(specimen.project_id, f'Added {structure_type} for {specimen.species_name}')
            db.session.commit()

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                lm_msg = f' with {landmark_count} landmarks' if landmarks_json else ''
                return jsonify({
                    'status': 'ok',
                    'message': f'{structure_type.replace("_", " ").title()} added{lm_msg}.',
                    'structure_id': structure.id,
                    'landmark_count': landmark_count if landmarks_json else 0,
                })

            lm_msg = f' with {landmark_count} landmarks' if landmarks_json else ''
            flash(f'{structure_type.replace("_", " ").title()} added{lm_msg}.', 'success')
            redirect_to = request.form.get('redirect_to', '').strip()
            if redirect_to and redirect_to.startswith('/'):
                return redirect(redirect_to)
            return redirect(url_for('project.view_project', project_id=specimen.project_id))

    return render_template('project/add_structure.html', specimen=specimen, project=project)


@project_bp.route('/api/structure/<int:structure_id>/upload_image', methods=['POST'])
@login_required
def upload_structure_image(structure_id):
    """Upload or replace the image for an existing structure."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    if 'image' not in request.files or not request.files['image'].filename:
        flash('No image selected.', 'error')
        return redirect(url_for('project.view_project', project_id=specimen.project_id))

    f = request.files['image']
    structure.image_path = _save_structure_image(f, structure.structure_type)
    _log(specimen.project_id, f'Uploaded image for {specimen.species_name} {structure.structure_type}')
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'ok', 'image_url': '/uploads/' + structure.image_path})

    flash(f'Image uploaded for {structure.structure_type.replace("_", " ")}.', 'success')
    redirect_to = request.form.get('redirect_to', '').strip()
    if redirect_to and redirect_to.startswith('/'):
        return redirect(redirect_to)
    return redirect(url_for('project.view_project', project_id=specimen.project_id))


@project_bp.route('/project/<int:project_id>/import', methods=['GET', 'POST'])
@login_required
def bulk_import(project_id):
    project = _get_project_or_404(project_id)

    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file uploaded.', 'error')
        else:
            f = request.files['csv_file']
            content = f.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(content))

            count = 0
            for row in reader:
                species = row.get('species_name', '').strip()
                if not species:
                    continue

                specimen = Specimen(
                    project_id=project_id, species_name=species,
                    specimen_id_label=row.get('specimen_id', '').strip(),
                    notes=row.get('notes', '').strip(),
                    created_by=current_user.id
                )
                db.session.add(specimen)
                db.session.flush()

                # Add structure if specified
                stype = row.get('structure_type', '').strip()
                if stype in ('hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco'):
                    structure = Structure(
                        specimen_id=specimen.id,
                        structure_type=stype,
                    )
                    db.session.add(structure)

                # DNA markers (comma-separated)
                markers = row.get('dna_markers', '').strip()
                if markers:
                    for marker in markers.split(','):
                        marker = marker.strip()
                        if marker:
                            dna = DNASequence(
                                specimen_id=specimen.id, marker=marker, available=True
                            )
                            db.session.add(dna)

                count += 1

            _log(project_id, f'Bulk imported {count} specimens')
            db.session.commit()
            flash(f'Imported {count} specimens.', 'success')
            return redirect(url_for('project.view_project', project_id=project_id))

    return render_template('project/bulk_import.html', project=project)


@project_bp.route('/project/<int:project_id>/members', methods=['POST'])
@login_required
def add_member(project_id):
    """Share the project with another user, looked up by username or email."""
    _get_project_or_404(project_id)
    ident = request.form.get('username', '').strip()
    role = request.form.get('role', 'annotator')
    if role not in ('annotator', 'reviewer', 'admin'):
        role = 'annotator'

    # Case-insensitive, whitespace-tolerant lookup by username OR email, so a
    # share never silently misses because of capitalisation or a stray space.
    from sqlalchemy import func
    user = None
    if ident:
        key = ident.lower()
        user = (User.query.filter(func.lower(User.username) == key).first() or
                User.query.filter(func.lower(User.email) == key).first())

    if not ident:
        flash('Enter a username or email to share with.', 'error')
    elif not user:
        flash(f'No account found for "{ident}". Check the spelling, or ask a site '
              'admin to create the account (Users page) before sharing.', 'error')
    elif ProjectMembership.query.filter_by(user_id=user.id, project_id=project_id).first():
        flash(f'{user.username} already has access to this project.', 'error')
    else:
        membership = ProjectMembership(user_id=user.id, project_id=project_id, role=role)
        db.session.add(membership)
        _log(project_id, f'Shared with {user.username} as {role}')
        db.session.commit()
        flash(f'Project shared with {user.username} ({user.email}) as {role}.', 'success')

    return redirect(url_for('project.view_project', project_id=project_id))


@project_bp.route('/project/<int:project_id>/members/<int:user_id>/remove', methods=['POST'])
@login_required
def remove_member(project_id, user_id):
    """Revoke a user's access. Only the owner or an admin member may do this."""
    project = _get_project_or_404(project_id)

    is_admin = (project.created_by == current_user.id or
                (ProjectMembership.query
                 .filter_by(user_id=current_user.id, project_id=project_id, role='admin')
                 .first() is not None))
    if not is_admin:
        flash('Only the owner or an admin can remove members.', 'error')
        return redirect(url_for('project.view_project', project_id=project_id))

    if user_id == project.created_by:
        flash('The project owner cannot be removed.', 'error')
        return redirect(url_for('project.view_project', project_id=project_id))

    m = ProjectMembership.query.filter_by(user_id=user_id, project_id=project_id).first()
    if not m:
        flash('User is not a member.', 'error')
    else:
        uname = m.user.username if m.user else str(user_id)
        db.session.delete(m)
        _log(project_id, f'Removed member {uname}')
        db.session.commit()
        flash(f'{uname} removed from project.', 'success')

    return redirect(url_for('project.view_project', project_id=project_id))


@project_bp.route('/specimen/<int:specimen_id>/dna', methods=['GET', 'POST'])
@login_required
def edit_dna(specimen_id):
    specimen = Specimen.query.get_or_404(specimen_id)

    if request.method == 'POST':
        # Clear existing and re-add
        DNASequence.query.filter_by(specimen_id=specimen_id).delete()
        for marker in ['ITS', '18S', 'COI', 'other']:
            if request.form.get(f'dna_{marker}'):
                accession = request.form.get(f'accession_{marker}', '').strip()
                dna = DNASequence(
                    specimen_id=specimen_id, marker=marker,
                    accession=accession, available=True
                )
                db.session.add(dna)
        db.session.commit()
        flash('DNA sequences updated.', 'success')
        return redirect(url_for('project.view_project', project_id=specimen.project_id))

    existing = {d.marker: d for d in DNASequence.query.filter_by(specimen_id=specimen_id).all()}
    return render_template('project/edit_dna.html', specimen=specimen, existing=existing)


@project_bp.route('/project/<int:project_id>/import_folders', methods=['GET', 'POST'])
@login_required
def import_folders(project_id):
    """Import specimens with landmarks from local folders.

    Each folder is mapped to a structure type. CSV files in the folder
    become structures with landmarks. Species names are parsed from filenames.
    Multiple folders can be specified — one per structure type or multiple
    folders for the same type.
    """
    project = _get_project_or_404(project_id)

    if request.method == 'POST':
        # Collect folder entries from dynamic form
        folders = []
        i = 0
        while f'folder_path_{i}' in request.form:
            path = request.form.get(f'folder_path_{i}', '').strip()
            stype = request.form.get(f'folder_type_{i}', '').strip()
            if path and stype:
                folders.append({'path': path, 'structure_type': stype})
            i += 1

        if not folders:
            flash('No folders specified.', 'error')
            return redirect(url_for('project.import_folders', project_id=project_id))

        total_imported = 0
        total_skipped = 0
        errors = []

        for entry in folders:
            folder_path = entry['path']
            structure_type = entry['structure_type']

            if not os.path.isdir(folder_path):
                errors.append(f'Not a directory: {folder_path}')
                continue

            # Find all CSV files
            csv_files = sorted(glob.glob(os.path.join(folder_path, '*.csv')))
            if not csv_files:
                errors.append(f'No CSV files in: {folder_path}')
                continue

            # In-session cache: (specimen_id, structure_type) -> Structure
            # Prevents duplicate creation inside no_autoflush where DB queries
            # can't see unflushed objects added earlier in the same loop.
            _struct_cache = {}

            with db.session.no_autoflush:
                for csv_path in csv_files:
                    filename = os.path.basename(csv_path)
                    base = os.path.splitext(filename)[0]

                    # Parse species name and optional accession from filename
                    species_name, accession = _parse_species_from_filename(base)

                    if not species_name:
                        errors.append(f'Could not parse species from: {filename}')
                        total_skipped += 1
                        continue

                    # Load landmarks from CSV
                    landmarks = _load_landmarks_csv(csv_path)
                    if not landmarks:
                        errors.append(f'No valid coordinates in: {filename}')
                        total_skipped += 1
                        continue

                    # Find or create specimen (match by species name in this project)
                    specimen = Specimen.query.filter_by(
                        project_id=project_id, species_name=species_name
                    ).first()

                    if not specimen:
                        specimen = Specimen(
                            project_id=project_id,
                            species_name=species_name,
                            specimen_id_label=accession or '',
                            created_by=current_user.id,
                        )
                        db.session.add(specimen)
                        db.session.flush()

                        # If we found an accession, add as DNA sequence
                        if accession:
                            dna = DNASequence(
                                specimen_id=specimen.id,
                                marker='ITS',  # default; user can change later
                                accession=accession,
                                available=True,
                            )
                            db.session.add(dna)

                    # Check cache first, then DB, to avoid duplicates inside no_autoflush
                    cache_key = (specimen.id, structure_type)
                    existing_struct = _struct_cache.get(cache_key) or Structure.query.filter_by(
                        specimen_id=specimen.id, structure_type=structure_type
                    ).first()

                    if existing_struct:
                        # Update landmarks on existing structure
                        existing_struct.landmarks_json = landmarks
                        existing_struct.landmark_count = len(landmarks)
                        existing_struct.landmarks_confirmed = True
                        _struct_cache[cache_key] = existing_struct
                    else:
                        structure = Structure(
                            specimen_id=specimen.id,
                            structure_type=structure_type,
                            landmarks_json=landmarks,
                            landmark_count=len(landmarks),
                            landmarks_confirmed=True,
                        )
                        db.session.add(structure)
                        _struct_cache[cache_key] = structure

                    total_imported += 1

        _log(project_id, f'Folder import: {total_imported} structures from {len(folders)} folders')
        db.session.commit()

        msg = f'Imported {total_imported} structures.'
        if total_skipped:
            msg += f' Skipped {total_skipped}.'
        if errors:
            msg += f' Errors: {"; ".join(errors[:5])}'
            if len(errors) > 5:
                msg += f' ...and {len(errors) - 5} more'

        flash(msg, 'success' if not errors else 'info')
        return redirect(url_for('project.view_project', project_id=project_id))

    return render_template('project/import_folders.html', project=project)


@project_bp.route('/api/project/<int:project_id>/scan_folder', methods=['POST'])
@login_required
def scan_folder(project_id):
    """Preview CSV files in a folder before importing."""
    _get_project_or_404(project_id)
    data = request.get_json()
    folder_path = data.get('path', '').strip()

    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'error': 'Invalid directory path', 'files': []})

    csv_files = sorted(glob.glob(os.path.join(folder_path, '*.csv')))
    previews = []
    for f in csv_files[:50]:  # cap preview at 50
        base = os.path.splitext(os.path.basename(f))[0]
        species, accession = _parse_species_from_filename(base)
        landmarks = _load_landmarks_csv(f)
        previews.append({
            'filename': os.path.basename(f),
            'species': species or '(could not parse)',
            'accession': accession or '',
            'landmarks': len(landmarks) if landmarks else 0,
        })

    return jsonify({
        'path': folder_path,
        'count': len(csv_files),
        'files': previews,
    })


@project_bp.route('/project/<int:project_id>/import_boundaries', methods=['POST'])
@login_required
def import_boundaries(project_id):
    """Import part boundary definitions from JSON files.

    Each JSON file maps specimen names to part boundary indices:
    {
        "Gyrodactylus_emembranatus": {
            "Point": [1, 2, 3, ...],
            "Shaft": [6, 7, 8, ...],
            ...
        }
    }
    """
    project = _get_project_or_404(project_id)

    # Collect JSON file entries from dynamic form
    json_files = []
    i = 0
    while f'json_path_{i}' in request.form:
        path = request.form.get(f'json_path_{i}', '').strip()
        stype = request.form.get(f'json_type_{i}', '').strip()
        if path and stype:
            json_files.append({'path': path, 'structure_type': stype})
        i += 1

    if not json_files:
        flash('No JSON files specified.', 'error')
        return redirect(url_for('project.import_folders', project_id=project_id))

    total_matched = 0
    total_unmatched = 0
    errors = []

    for entry in json_files:
        json_path = entry['path']
        structure_type = entry['structure_type']

        if not os.path.isfile(json_path):
            errors.append(f'File not found: {json_path}')
            continue

        try:
            with open(json_path, 'r') as f:
                boundary_data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            errors.append(f'Error reading {json_path}: {e}')
            continue

        if not isinstance(boundary_data, dict):
            errors.append(f'Invalid format in {json_path}: expected object')
            continue

        for specimen_key, parts in boundary_data.items():
            if not isinstance(parts, dict):
                continue

            # Parse species name from the JSON key (same format as filenames)
            species_name, accession = _parse_species_from_filename(specimen_key)
            if not species_name:
                total_unmatched += 1
                continue

            # Find or create specimen in project
            specimen = Specimen.query.filter_by(
                project_id=project_id, species_name=species_name
            ).first()

            if not specimen:
                specimen = Specimen(
                    project_id=project_id,
                    species_name=species_name,
                    specimen_id_label=accession or '',
                    created_by=current_user.id,
                )
                db.session.add(specimen)
                db.session.flush()

                if accession:
                    dna = DNASequence(
                        specimen_id=specimen.id,
                        marker='ITS',
                        accession=accession,
                        available=True,
                    )
                    db.session.add(dna)

            # Find matching structure
            structure = Structure.query.filter_by(
                specimen_id=specimen.id, structure_type=structure_type
            ).first()

            if not structure:
                # Create structure if specimen exists but structure doesn't
                structure = Structure(
                    specimen_id=specimen.id,
                    structure_type=structure_type,
                    landmarks_confirmed=False,
                )
                db.session.add(structure)
                db.session.flush()

            # Build boundary dict (exclude 'coordinates' key if present)
            # Convert 1-based indices from JSON to 0-based for internal use
            boundary = {}
            for k, v in parts.items():
                if k == 'coordinates' or not isinstance(v, list):
                    continue
                boundary[k] = [idx - 1 for idx in v if isinstance(idx, int) and idx >= 1]

            if boundary:
                structure.boundary_json = boundary
                structure.boundary_confirmed = False
                total_matched += 1

    _log(project_id, f'Boundary import: {total_matched} structures from {len(json_files)} files')
    db.session.commit()

    # Auto-compute characters with Procrustes alignment
    from app.characters import compute_batch_with_procrustes, initialize_project_characters
    from app.models import CharacterDefinition
    if not CharacterDefinition.query.filter_by(project_id=project_id).first():
        initialize_project_characters(project_id)

    chars_computed = compute_batch_with_procrustes(project_id)

    msg = f'Imported boundaries for {total_matched} structures.'
    if chars_computed:
        msg += f' Characters computed for {chars_computed} structures.'
    if total_unmatched:
        msg += f' {total_unmatched} specimens not matched.'
    if errors:
        msg += f' Errors: {"; ".join(errors[:5])}'

    flash(msg, 'success' if not errors else 'info')
    return redirect(url_for('project.view_project', project_id=project_id))


@project_bp.route('/project/<int:project_id>/compute_all_characters', methods=['POST'])
@login_required
def compute_all_characters_route(project_id):
    """Batch-compute geometric characters for all ready structures in the project."""
    project = _get_project_or_404(project_id)

    from app.characters import compute_batch_with_procrustes, initialize_project_characters
    from app.models import CharacterDefinition

    # Ensure project has character definitions
    if not CharacterDefinition.query.filter_by(project_id=project_id).first():
        initialize_project_characters(project_id)

    computed = compute_batch_with_procrustes(project_id)

    _log(project_id, f'Batch character computation with GPA: {computed} structures')
    flash(f'Characters computed for {computed} structures (Procrustes-aligned).', 'success')
    return redirect(url_for('project.view_project', project_id=project_id))


@project_bp.route('/api/project/<int:project_id>/scan_json', methods=['POST'])
@login_required
def scan_json(project_id):
    """Preview a boundary JSON file before importing."""
    project = _get_project_or_404(project_id)
    data = request.get_json()
    json_path = data.get('path', '').strip()

    if not json_path or not os.path.isfile(json_path):
        return jsonify({'error': 'File not found', 'specimens': []})

    try:
        with open(json_path, 'r') as f:
            boundary_data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return jsonify({'error': 'Could not parse JSON file', 'specimens': []})

    if not isinstance(boundary_data, dict):
        return jsonify({'error': 'Invalid format: expected object', 'specimens': []})

    previews = []
    for specimen_key, parts in list(boundary_data.items())[:50]:
        if not isinstance(parts, dict):
            continue
        species_name, accession = _parse_species_from_filename(specimen_key)
        part_names = [k for k in parts.keys() if k != 'coordinates']

        # Check if specimen exists in project
        matched = False
        if species_name:
            matched = Specimen.query.filter_by(
                project_id=project_id, species_name=species_name
            ).first() is not None

        previews.append({
            'key': specimen_key,
            'species': species_name or '(could not parse)',
            'parts': part_names,
            'matched': matched,
        })

    return jsonify({
        'path': json_path,
        'count': len(boundary_data),
        'specimens': previews,
    })


def _specimen_epithet_map(project_id):
    """Return {epithet_key: Specimen} for all specimens in the project."""
    result = {}
    for s in Specimen.query.filter_by(project_id=project_id).all():
        k = _epithet_key(s.species_name)
        if k and k not in result:
            result[k] = s
    return result


def _species_from_stem(stem):
    """Parse a human-readable species name from a file stem using _parse_species_from_filename,
    falling back to _epithet_key on the raw stem."""
    parsed, _ = _parse_species_from_filename(stem)
    return parsed or stem.replace('_', ' ').strip()


@project_bp.route('/project/<int:project_id>/import_images', methods=['POST'])
@login_required
def import_images(project_id):
    """Import images from a local folder, matching to existing specimens by species name."""
    project = _get_project_or_404(project_id)
    folder_path = request.form.get('image_folder', '').strip()
    structure_type = request.form.get('image_type', 'hook')

    if not folder_path or not os.path.isdir(folder_path):
        flash('Invalid folder path.', 'danger')
        return redirect(url_for('project.import_folders', project_id=project_id))

    upload_root = current_app.config['UPLOAD_FOLDER']

    IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff'}
    imported = 0
    skipped = 0

    epithet_map = _specimen_epithet_map(project_id)

    # ── Pass 1: read-only — identify (specimen, existing_structure, src_path, fname) ──
    pending = []
    for fname in sorted(os.listdir(folder_path)):
        if os.path.splitext(fname)[1].lower() not in IMAGE_EXTS:
            continue
        parsed = _species_from_stem(os.path.splitext(fname)[0])
        specimen = epithet_map.get(_epithet_key(parsed))
        if not specimen:
            skipped += 1
            continue
        structure = Structure.query.filter_by(
            specimen_id=specimen.id, structure_type=structure_type
        ).first()
        pending.append((specimen, structure, os.path.join(folder_path, fname), fname))

    # ── Pass 2: create any missing structures in one short commit to get IDs ──
    new_structs = {}   # specimen_id → new Structure
    for specimen, structure, _src, _fname in pending:
        if structure is None and specimen.id not in new_structs:
            st = Structure(specimen_id=specimen.id, structure_type=structure_type)
            db.session.add(st)
            new_structs[specimen.id] = st
    if new_structs:
        db.session.commit()   # short write — releases the lock immediately

    # ── Pass 3: copy files (pure I/O, no DB transaction held) ──
    file_results = []   # [(structure, rel_path)]
    for specimen, structure, src_abs, fname in pending:
        if structure is None:
            structure = new_structs.get(specimen.id)
        if structure is None:
            skipped += 1
            continue
        try:
            with open(src_abs, 'rb') as _fh:
                rel_path = _store_structure_bytes(_fh.read(), fname,
                                                  structure.structure_type)
            file_results.append((structure, rel_path))
        except OSError:
            skipped += 1

    # ── Pass 4: update image_path for all copied files in one short commit ──
    for structure, rel_path in file_results:
        structure.image_path = rel_path
        imported += 1
    if file_results:
        db.session.commit()

    _log(project_id, f'Imported {imported} images from {folder_path}')
    db.session.commit()
    flash(f'Imported {imported} images ({skipped} unmatched).', 'success')
    return redirect(url_for('project.view_project', project_id=project_id))


@project_bp.route('/api/project/<int:project_id>/scan_images', methods=['POST'])
@login_required
def scan_images(project_id):
    """Preview images in a folder and show which match existing specimens."""
    project = _get_project_or_404(project_id)
    data = request.get_json()
    folder_path = data.get('path', '').strip()

    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'error': 'Folder not found'})

    IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff'}
    epithet_map = _specimen_epithet_map(project_id)

    files = []
    for fname in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in IMAGE_EXTS:
            continue
        stem = os.path.splitext(fname)[0]
        parsed = _species_from_stem(stem)
        matched = _epithet_key(parsed) in epithet_map
        files.append({
            'filename': fname,
            'species': parsed,   # show the parsed name, not the raw stem
            'matched': matched,
        })

    return jsonify({'count': len(files), 'files': files})


def _parse_species_from_filename(basename: str) -> tuple:
    """Parse species name and optional accession from a CSV filename.

    Handles formats like:
        - "Gyrodactylus_salaris" -> ("Gyrodactylus salaris", None)
        - "AB063294Gyrodactylusanguillae:1-1253" -> ("Gyrodactylus anguillae", "AB063294")
        - "AF484529Gyrodactyluscernuae:1-1236" -> ("Gyrodactylus cernuae", "AF484529")
        - "Afrogyrodactylus_girgifae" -> ("Afrogyrodactylus girgifae", None)
        - "Diplogyrodactylus_martini-anchor" -> ("Diplogyrodactylus martini", None)
    """
    name = basename.strip()

    # Handle pipe-separated format: "JF836137.1|Gyrocerviceanseris_passamaquoddyensis"
    accession = None
    if '|' in name:
        parts_pipe = name.split('|', 1)
        accession = parts_pipe[0].strip()
        name = parts_pipe[1].strip()

    # Remove trailing suffixes like "-anchor", "-hooks", "-bar", "-2"
    name = re.sub(r'-(anchors?|hooks?|bars?|mco|[0-9]+)$', '', name, flags=re.IGNORECASE)

    # Remove trailing content after colon (e.g., ":1-1253")
    name = re.sub(r':.*$', '', name)

    # Check for leading accession number (e.g., "AB063294", "AF484529", "JF836137.1")
    # Only if not already extracted from pipe format
    if not accession:
        acc_match = re.match(r'^([A-Z]{1,3}\d{5,8}(?:\.\d+)?)', name)
        if acc_match:
            accession = acc_match.group(1)
            name = name[len(accession):]

    # Now split genus+species from concatenated form
    # "Gyrodactylusanguillae" -> "Gyrodactylus anguillae"
    if '_' in name:
        # Already underscore-separated: "Gyrodactylus_salaris" or "Gyrodactylusbubyri_1-1189"
        parts = name.split('_')
        # First part might be concatenated genus+species
        first = parts[0]
        # Drop numeric-only trailing parts (e.g., "1-1189")
        text_parts = [p for p in parts if p and not re.match(r'^[\d\-]+$', p)]
        if len(text_parts) == 1:
            # Single text part, possibly concatenated: "Gyrodactylusbubyri"
            name = text_parts[0]
            # Fall through to concatenated splitting below
        else:
            # Multiple text parts: "Gyrodactylus_salaris"
            species = ' '.join(text_parts)
            species = species.strip()
            return (species, accession) if species else (None, accession)

    # Concatenated: "Gyrodactylusanguillae"
    # Known genus prefixes in Gyrodactylidae (try longest match first)
    known_genera = [
        'Acanthoplacatus', 'Afrogyrodactylus', 'Archigyrodactylus',
        'Citharodactylus', 'Diechodactylus', 'Diplogyrodactylus',
        'Fundulotrema', 'Gyrdicotylus', 'Gyrocerviceanseris',
        'Gyrodactyloides', 'Gyrodactylus', 'Ieredactylus',
        'Lamniscus', 'Macrogyrodactylus', 'Mormyrogyrodactylus',
        'Paragyrodactylus', 'Polyclithrum', 'Rysavyius',
        'Scleroductus', 'Swingleus', 'Tresuncinidactylus',
    ]
    for genus in sorted(known_genera, key=len, reverse=True):
        if name.startswith(genus) and len(name) > len(genus):
            epithet = name[len(genus):]
            species = f'{genus} {epithet}'
            return (species.strip(), accession)

    # Fallback: split at transition from uppercase-starting word to lowercase
    # "SomegenusSpeciesname" is ambiguous, try first capital-lowercase boundary
    # after at least 3 lowercase chars
    m = re.match(r'^([A-Z][a-z]{2,})([a-z]+)$', name)
    if m:
        species = f'{m.group(1)} {m.group(2)}'
        return (species.strip(), accession)

    species = name

    species = species.strip()
    if not species:
        return None, accession

    return species, accession


def _load_landmarks_csv(csv_path: str) -> list:
    """Load landmark coordinates from a CSV file.

    Handles format: optional index column, X, Y columns.
    Returns list of [x, y] pairs.
    """
    coords = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                try:
                    vals = [float(v) for v in row if v.strip()]
                    if len(vals) >= 2:
                        coords.append([vals[-2], vals[-1]])  # last two columns as X, Y
                except ValueError:
                    continue
    except Exception:
        return []

    return coords if len(coords) >= 3 else []


@project_bp.route('/project/<int:project_id>/macro/<structure_type>')
@login_required
def download_macro(project_id, structure_type):
    """Serve an ImageJ macro pre-configured with project directories."""
    from flask import send_file, abort
    project = Project.query.get_or_404(project_id)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    macro_map = {
        'hook': 'macrogyrolandmark_v5.5.ijm',
        'anchor': 'macrogyrolandmark_v5_anchors.ijm',
    }
    if structure_type not in macro_map:
        abort(404)

    macro_path = os.path.join(base_dir, 'macros', macro_map[structure_type])
    if not os.path.exists(macro_path):
        abort(404)

    with open(macro_path, 'r') as f:
        content = f.read()

    # Build suggested directories using the project's upload folder
    upload_dir = current_app.config['UPLOAD_FOLDER']
    img_dir = os.path.join(upload_dir, f'project_{project_id}', structure_type + 's')
    csv_dir = os.path.join(upload_dir, f'project_{project_id}', 'landmarks', structure_type + 's')

    # Replace default directories in the macro
    # The macros have two Dialog.addString lines for input and output dirs
    import re as _re
    # Replace input directory default
    content = _re.sub(
        r'(Dialog\.addString\("Input directory:",\s*\n\s*")([^"]+)(")',
        lambda m: m.group(1) + img_dir + m.group(3),
        content
    )
    # Replace output directory default
    content = _re.sub(
        r'(Dialog\.addString\("Output directory:",\s*\n\s*")([^"]+)(")',
        lambda m: m.group(1) + csv_dir + m.group(3),
        content
    )

    # Serve as downloadable file
    out = io.BytesIO(content.encode('utf-8'))
    out.seek(0)
    filename = f'{project.name.replace(" ", "_")}_{structure_type}_landmark.ijm'
    return send_file(out, mimetype='text/plain', as_attachment=True, download_name=filename)


@project_bp.route('/api/specimen/<int:specimen_id>', methods=['PATCH'])
@login_required
def update_specimen(specimen_id):
    specimen = Specimen.query.get_or_404(specimen_id)
    _get_project_or_404(specimen.project_id)
    data = request.get_json(force=True)
    name = (data.get('species_name') or '').strip()
    if not name:
        return jsonify(error='Species name is required'), 400
    specimen.species_name      = name
    specimen.specimen_id_label = (data.get('specimen_id_label') or '').strip() or None
    specimen.notes             = (data.get('notes') or '').strip() or None
    db.session.commit()
    return jsonify(status='ok', species_name=specimen.species_name,
                   specimen_id_label=specimen.specimen_id_label,
                   notes=specimen.notes)


@project_bp.route('/specimen/<int:specimen_id>/detail')
@login_required
def specimen_detail(specimen_id):
    specimen = Specimen.query.get_or_404(specimen_id)
    project  = _get_project_or_404(specimen.project_id)
    from config import Config

    _all_types = ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']
    STRUCTURE_TYPES = ['mco'] if 'MCO' in project.name.upper() else _all_types

    # Build a dict: type → best structure (or None)
    all_structs = Structure.query.filter_by(specimen_id=specimen_id).all()

    def _score(s):
        return (bool(s.landmarks_json) * 4 +
                bool(s.landmarks_confirmed) * 2 +
                bool(s.boundary_json) * 2 +
                bool(s.image_path))

    best = {}
    for st in all_structs:
        t = st.structure_type
        if t not in best or _score(st) > _score(best[t]):
            best[t] = st

    structures = {t: best.get(t) for t in STRUCTURE_TYPES}

    # Previous / next specimen in alphabetical order
    ordered = (Specimen.query
               .filter_by(project_id=specimen.project_id)
               .order_by(Specimen.species_name, Specimen.id)
               .all())
    ids = [s.id for s in ordered]
    idx = ids.index(specimen_id) if specimen_id in ids else -1
    prev_id = ids[idx - 1] if idx > 0 else None
    next_id = ids[idx + 1] if idx >= 0 and idx < len(ids) - 1 else None

    return render_template(
        'project/specimen_detail.html',
        project=project,
        specimen=specimen,
        structures=structures,
        structure_types=STRUCTURE_TYPES,
        structure_parts=Config.STRUCTURE_PARTS,
        prev_specimen_id=prev_id,
        next_specimen_id=next_id,
        specimen_index=idx + 1,
        specimen_total=len(ids),
    )


@project_bp.route('/project/<int:project_id>/coverage')
@login_required
def coverage(project_id):
    project = _get_project_or_404(project_id)
    return render_template('project/coverage.html', project=project)


@project_bp.route('/api/project/<int:project_id>/coverage')
@login_required
def coverage_api(project_id):
    """Return per-specimen coverage of images, landmarks, boundaries and DNA."""
    project = _get_project_or_404(project_id)

    STRUCTURE_TYPES = ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']

    specimens = (Specimen.query
                 .filter_by(project_id=project_id)
                 .order_by(Specimen.species_name)
                 .all())

    all_dna = (DNASequence.query
               .filter(DNASequence.specimen_id.in_([s.id for s in specimens]))
               .all())
    markers = sorted({d.marker for d in all_dna})

    summary = {
        'total': len(specimens),
        'specimen_image': 0,
        'dna': {m: 0 for m in markers},
        'structures': {st: {
            'exists': 0, 'image': 0,
            'landmarks': 0, 'lm_confirmed': 0,
            'boundaries': 0, 'bnd_confirmed': 0,
        } for st in STRUCTURE_TYPES},
    }

    rows = []
    for sp in specimens:
        dna_map = {}
        for d in sp.dna_sequences:
            if d.available:
                dna_map[d.marker] = d.accession or 'yes'
        for m in markers:
            if m in dna_map:
                summary['dna'][m] += 1

        has_spec_img = bool(sp.image_path)
        if has_spec_img:
            summary['specimen_image'] += 1

        struct_map = {st: None for st in STRUCTURE_TYPES}
        for st_obj in sp.structures:
            st = st_obj.structure_type
            if st not in struct_map:
                continue
            has_lm  = bool(st_obj.landmarks_json)
            has_bnd = bool(st_obj.boundary_json)
            struct_map[st] = {
                'structure_id': st_obj.id,
                'image':        bool(st_obj.image_path),
                'landmarks':    has_lm,
                'lm_confirmed': bool(st_obj.landmarks_confirmed),
                'boundaries':   has_bnd,
                'bnd_confirmed': bool(st_obj.boundary_confirmed),
            }
            s = summary['structures'][st]
            s['exists'] += 1
            if st_obj.image_path:            s['image']       += 1
            if has_lm:                        s['landmarks']    += 1
            if st_obj.landmarks_confirmed:    s['lm_confirmed'] += 1
            if has_bnd:                       s['boundaries']   += 1
            if st_obj.boundary_confirmed:     s['bnd_confirmed'] += 1

        rows.append({
            'specimen_id': sp.id,
            'species':     sp.species_name,
            'label':       sp.specimen_id_label or '',
            'specimen_image': has_spec_img,
            'dna':         dna_map,
            'structures':  struct_map,
        })

    return jsonify({
        'structure_types': STRUCTURE_TYPES,
        'markers':         markers,
        'summary':         summary,
        'rows':            rows,
        'project_id':      project_id,
    })


@project_bp.route('/api/project/<int:project_id>/coverage/export')
@login_required
def coverage_export(project_id):
    """Download the coverage report as a CSV file."""
    from flask import send_file
    project = _get_project_or_404(project_id)

    STRUCTURE_TYPES = ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']

    specimens = (Specimen.query
                 .filter_by(project_id=project_id)
                 .order_by(Specimen.species_name)
                 .all())
    all_dna = (DNASequence.query
               .filter(DNASequence.specimen_id.in_([s.id for s in specimens]))
               .all())
    markers = sorted({d.marker for d in all_dna})

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    header = ['Species', 'Specimen ID', 'Specimen Image']
    for m in markers:
        header.append(f'DNA:{m}')
    for st in STRUCTURE_TYPES:
        lbl = st.replace('_', ' ').title()
        header += [f'{lbl}: Exists', f'{lbl}: Image', f'{lbl}: Landmarks',
                   f'{lbl}: LM Confirmed', f'{lbl}: Boundaries', f'{lbl}: BND Confirmed']
    writer.writerow(header)

    for sp in specimens:
        dna_map = {d.marker: (d.accession or 'yes') for d in sp.dna_sequences if d.available}
        struct_map = {st_obj.structure_type: st_obj for st_obj in sp.structures
                      if st_obj.structure_type in STRUCTURE_TYPES}

        row = [sp.species_name, sp.specimen_id_label or '', 'yes' if sp.image_path else 'no']
        for m in markers:
            row.append(dna_map.get(m, ''))
        for st in STRUCTURE_TYPES:
            obj = struct_map.get(st)
            if obj is None:
                row += ['no', '', '', '', '', '']
            else:
                row += [
                    'yes',
                    'yes' if obj.image_path else 'no',
                    'yes' if obj.landmarks_json else 'no',
                    'yes' if obj.landmarks_confirmed else 'no',
                    'yes' if obj.boundary_json else 'no',
                    'yes' if obj.boundary_confirmed else 'no',
                ]
        writer.writerow(row)

    output.seek(0)
    filename = f'{project.name.replace(" ", "_")}_coverage.csv'
    buf = io.BytesIO(output.getvalue().encode('utf-8'))
    return send_file(buf, mimetype='text/csv', as_attachment=True, download_name=filename)


@project_bp.route('/api/project/<int:project_id>/import_from_project/projects')
@login_required
def import_from_project_list(project_id):
    """Return projects the current user can read from (excludes current project)."""
    owned = Project.query.filter_by(created_by=current_user.id).all()
    member_ids = [m.project_id for m in
                  ProjectMembership.query.filter_by(user_id=current_user.id).all()]
    member_projects = Project.query.filter(Project.id.in_(member_ids)).all() if member_ids else []
    all_projects = list({p.id: p for p in owned + member_projects}.values())
    return jsonify([
        {'id': p.id, 'name': p.name}
        for p in sorted(all_projects, key=lambda p: p.name)
        if p.id != project_id
    ])


def _epithet_key(name: str) -> str:
    """Normalise a species name to lowercase 'genus epithet' for fuzzy matching.

    Handles:
    - Pipe format          'KX981461.1|Gyrodactylus_salaris'  → 'gyrodactylus salaris'
    - Semicolon/colon      'Gyrodactylusanguillae:1-1253'     → 'gyrodactylus anguillae'
    - Leading accession    'AB063294Gyrodactylusanguillae'    → 'gyrodactylus anguillae'
    - Trailing accession   'Gyrodactylus salaris KX123456'    → 'gyrodactylus salaris'
    - Underscore separator 'Gyrodactylus_salaris'             → 'gyrodactylus salaris'
    - Concatenated name    'Gyrodactylusanguillae'            → 'gyrodactylus anguillae'
    - Extra labels         'Gyrodactylus salaris isolate 3'   → 'gyrodactylus salaris'
    """
    name = name.strip()

    # Pipe format: take everything after the last pipe
    if '|' in name:
        name = name.split('|')[-1].strip()

    # Truncate at first ; or :
    name = re.split(r'[;:]', name)[0].strip()

    # Underscores → spaces
    name = name.replace('_', ' ')

    # Strip a leading accession number (1–3 letters + 5–8 digits, optional .N)
    name = re.sub(r'^[A-Za-z]{1,3}\d{5,8}(?:\.\d+)?\s*', '', name)

    # Split into tokens; stop at first token that starts with a digit or looks
    # like an accession (letters immediately followed by digits, e.g. KX123, AB06)
    _acc_re = re.compile(r'^[A-Za-z]{1,3}\d', re.IGNORECASE)
    clean = []
    for tok in name.split():
        if tok[0].isdigit() or _acc_re.match(tok):
            break
        clean.append(tok)

    if not clean:
        return ''

    # If only one token remains it may be a concatenated 'GenusSpecies' string.
    # Try to split it using the known Gyrodactylidae genera (longest first).
    if len(clean) == 1:
        word = clean[0]
        _KNOWN_GENERA = [
            'Acanthoplacatus', 'Afrogyrodactylus', 'Archigyrodactylus',
            'Citharodactylus', 'Diechodactylus', 'Diplogyrodactylus',
            'Fundulotrema', 'Gyrdicotylus', 'Gyrocerviceanseris',
            'Gyrodactyloides', 'Gyrodactylus', 'Ieredactylus',
            'Lamniscus', 'Macrogyrodactylus', 'Mormyrogyrodactylus',
            'Paragyrodactylus', 'Polyclithrum', 'Rysavyius',
            'Scleroductus', 'Swingleus', 'Tresuncinidactylus',
        ]
        for genus in sorted(_KNOWN_GENERA, key=len, reverse=True):
            if word.lower().startswith(genus.lower()) and len(word) > len(genus):
                return f'{genus.lower()} {word[len(genus):].lower()}'
        # Generic CamelCase fallback: UpperLower → 'upper lower'
        m = re.match(r'^([A-Z][a-z]{2,})([a-z]+)$', word)
        if m:
            return f'{m.group(1).lower()} {m.group(2).lower()}'
        return word.lower()

    genus   = clean[0].lower()
    epithet = clean[1].lower()
    # Strip structure-type suffixes and anything after the first hyphen
    # e.g. 'martini-hooks' → 'martini', 'salaris-2' → 'salaris'
    epithet = re.sub(r'-(anchors?|hooks?|bars?|mco|\d+)$', '', epithet, flags=re.IGNORECASE)
    epithet = epithet.split('-')[0]
    return f'{genus} {epithet}'


def _import_from_project_match(project_id, source_project_id, structure_types, what, overwrite):
    """
    Core matching logic shared by preview and import.
    Matches specimens by genus+epithet (first two words) so that names with
    extra tokens (accession numbers, isolate labels, etc.) still pair up.
    Returns a list of match records:
      { tgt_specimen, src_specimen, structure_type,
        src_struct, tgt_struct (may be None),
        will_copy_lm, will_copy_bnd, will_copy_img }
    """
    # Build epithet-key → specimen maps (first specimen wins on collision)
    tgt_by_epithet: dict = {}
    for s in Specimen.query.filter_by(project_id=project_id).all():
        k = _epithet_key(s.species_name)
        if k and k not in tgt_by_epithet:
            tgt_by_epithet[k] = s

    src_by_epithet: dict = {}
    for s in Specimen.query.filter_by(project_id=source_project_id).all():
        k = _epithet_key(s.species_name)
        if k and k not in src_by_epithet:
            src_by_epithet[k] = s

    matched_norms = sorted(set(tgt_by_epithet) & set(src_by_epithet))

    copy_lm  = 'landmarks'  in what
    copy_bnd = 'boundaries' in what
    copy_img = 'images'     in what

    matched_src_sp_ids = [src_by_epithet[n].id for n in matched_norms]
    matched_tgt_sp_ids = [tgt_by_epithet[n].id for n in matched_norms]

    src_structs_all = Structure.query.filter(
        Structure.specimen_id.in_(matched_src_sp_ids)).all() if matched_src_sp_ids else []
    tgt_structs_all = Structure.query.filter(
        Structure.specimen_id.in_(matched_tgt_sp_ids)).all() if matched_tgt_sp_ids else []

    src_struct_map = {}
    for st in src_structs_all:
        if not structure_types or st.structure_type in structure_types:
            src_struct_map[(st.specimen_id, st.structure_type)] = st
    tgt_struct_map = {}
    for st in tgt_structs_all:
        tgt_struct_map[(st.specimen_id, st.structure_type)] = st

    matches = []
    stypes = structure_types or ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']
    for norm in matched_norms:
        src_sp = src_by_epithet[norm]
        tgt_sp = tgt_by_epithet[norm]
        for stype in stypes:
            src_st = src_struct_map.get((src_sp.id, stype))
            if not src_st:
                continue
            tgt_st = tgt_struct_map.get((tgt_sp.id, stype))

            will_copy_lm = (copy_lm and bool(src_st.landmarks_json) and
                            (overwrite or not (tgt_st and tgt_st.landmarks_json)))
            will_copy_bnd = (copy_bnd and bool(src_st.boundary_json) and
                             (overwrite or not (tgt_st and tgt_st.boundary_json)))
            will_copy_img = (copy_img and bool(src_st.image_path) and
                             (overwrite or not (tgt_st and tgt_st.image_path)))

            if not will_copy_lm and not will_copy_bnd and not will_copy_img:
                continue

            matches.append({
                'tgt_specimen': tgt_sp,
                'src_struct': src_st,
                'tgt_struct': tgt_st,
                'structure_type': stype,
                'will_copy_lm': will_copy_lm,
                'will_copy_bnd': will_copy_bnd,
                'will_copy_img': will_copy_img,
            })
    return matches


@project_bp.route('/api/project/<int:project_id>/import_from_project/preview')
@login_required
def import_from_project_preview(project_id):
    source_project_id = request.args.get('source_project_id', type=int)
    what = [x.strip() for x in request.args.get('what', 'landmarks,images').split(',')]
    structure_types = [x.strip() for x in request.args.get('structure_types', '').split(',') if x.strip()]
    overwrite = request.args.get('overwrite', '0') == '1'

    if not source_project_id:
        return jsonify({'error': 'source_project_id required'}), 400
    if source_project_id == project_id:
        return jsonify({'error': 'Source and target must be different projects'}), 400

    source_project = Project.query.get_or_404(source_project_id)
    is_member = (source_project.created_by == current_user.id or
                 ProjectMembership.query.filter_by(
                     user_id=current_user.id, project_id=source_project_id).first())
    if not is_member:
        return jsonify({'error': 'No access to source project'}), 403

    matches = _import_from_project_match(
        project_id, source_project_id, structure_types, what, overwrite)

    n_lm  = sum(1 for m in matches if m['will_copy_lm'])
    n_bnd = sum(1 for m in matches if m['will_copy_bnd'])
    n_img = sum(1 for m in matches if m['will_copy_img'])

    species_touched = sorted({m['tgt_specimen'].species_name for m in matches})
    return jsonify({
        'source_project': source_project.name,
        'n_matched_specimens': len(species_touched),
        'matched_species': species_touched[:15],
        'n_more_species': max(0, len(species_touched) - 15),
        'n_landmarks': n_lm,
        'n_boundaries': n_bnd,
        'n_images': n_img,
        'n_total': n_lm + n_bnd + n_img,
    })


@project_bp.route('/api/project/<int:project_id>/import_from_project', methods=['POST'])
@login_required
def import_from_project(project_id):
    """Copy landmarks and/or images from another project where species names match."""
    data = request.get_json() or {}
    source_project_id = data.get('source_project_id')
    what = data.get('what', ['landmarks', 'images'])  # list
    structure_types = data.get('structure_types', [])  # [] = all
    overwrite = data.get('overwrite', False)

    if not source_project_id:
        return jsonify({'error': 'source_project_id required'}), 400
    if source_project_id == project_id:
        return jsonify({'error': 'Source and target must be different projects'}), 400

    source_project = Project.query.get_or_404(source_project_id)
    is_member = (source_project.created_by == current_user.id or
                 ProjectMembership.query.filter_by(
                     user_id=current_user.id, project_id=source_project_id).first())
    if not is_member:
        return jsonify({'error': 'No access to source project'}), 403

    matches = _import_from_project_match(
        project_id, source_project_id, structure_types, what, overwrite)

    upload_folder = current_app.config['UPLOAD_FOLDER']
    imported_lm = 0
    imported_bnd = 0
    imported_img = 0

    for m in matches:
        tgt_sp = m['tgt_specimen']
        src_st = m['src_struct']
        tgt_st = m['tgt_struct']

        if tgt_st is None:
            tgt_st = Structure(
                specimen_id=tgt_sp.id,
                structure_type=m['structure_type'],
            )
            db.session.add(tgt_st)
            db.session.flush()
            m['tgt_struct'] = tgt_st

        if m['will_copy_lm']:
            tgt_st.landmarks_json = src_st.landmarks_json
            tgt_st.landmark_count = src_st.landmark_count
            tgt_st.landmarks_confirmed = False
            imported_lm += 1

        if m['will_copy_bnd']:
            tgt_st.boundary_json = src_st.boundary_json
            tgt_st.boundary_confirmed = False
            imported_bnd += 1

        if m['will_copy_img'] and src_st.image_path:
            src_abs = os.path.join(upload_folder, src_st.image_path)
            if os.path.exists(src_abs):
                # Images are consolidated + deduplicated under structures/;
                # share the single copy rather than duplicating it.
                tgt_st.image_path = src_st.image_path
                imported_img += 1

    db.session.commit()
    _log(project_id,
         f'Imported from project {source_project.name}: '
         f'{imported_lm} landmark set(s), {imported_bnd} boundary set(s), {imported_img} image(s)')

    parts = []
    if imported_lm:
        parts.append(f'{imported_lm} landmark set(s)')
    if imported_bnd:
        parts.append(f'{imported_bnd} boundary set(s)')
    if imported_img:
        parts.append(f'{imported_img} image(s)')
    return jsonify({
        'status': 'ok',
        'imported_lm': imported_lm,
        'imported_bnd': imported_bnd,
        'imported_img': imported_img,
        'message': 'Imported ' + ', '.join(parts) + '.' if parts else 'Nothing to import.',
    })


def _get_project_or_404(project_id):
    project = Project.query.get_or_404(project_id)
    # Check membership
    is_member = (project.created_by == current_user.id or
                 ProjectMembership.query.filter_by(
                     user_id=current_user.id, project_id=project_id).first())
    if not is_member:
        from flask import abort
        abort(403)
    return project


def _log(project_id, action, details=None):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id,
        action=action, details=details
    )
    db.session.add(log)
