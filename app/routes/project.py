import os
import re
import csv
import io
import json
import glob
import shutil
import numpy as np
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from app.models import (
    Project, ProjectMembership, Specimen, Structure, DNASequence, ActivityLog, User
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

    return render_template('project/view_project.html',
                           project=project, specimens=specimens,
                           members=members, stats=stats, dna_only=dna_only)


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
                                              str(project_id))
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
            image_path = None
            if 'image' in request.files:
                f = request.files['image']
                if f.filename:
                    filename = secure_filename(f.filename)
                    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'],
                                              str(specimen.project_id), 'structures')
                    os.makedirs(upload_dir, exist_ok=True)
                    filepath = os.path.join(upload_dir, filename)
                    f.save(filepath)
                    image_path = os.path.relpath(filepath, current_app.config['UPLOAD_FOLDER'])

            from config import Config
            landmark_count = Config.LANDMARK_COUNTS.get(structure_type, 100)

            structure = Structure(
                specimen_id=specimen_id,
                structure_type=structure_type,
                image_path=image_path,
                landmark_count=landmark_count,
            )
            db.session.add(structure)
            _log(specimen.project_id, f'Added {structure_type} for {specimen.species_name}')
            db.session.commit()
            flash(f'{structure_type.replace("_", " ").title()} added.', 'success')
            return redirect(url_for('project.view_project', project_id=specimen.project_id))

    return render_template('project/add_structure.html', specimen=specimen, project=project)


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
    _get_project_or_404(project_id)
    username = request.form.get('username', '').strip()
    role = request.form.get('role', 'annotator')

    user = User.query.filter_by(username=username).first()
    if not user:
        flash('User not found.', 'error')
    elif ProjectMembership.query.filter_by(user_id=user.id, project_id=project_id).first():
        flash('User is already a member.', 'error')
    else:
        membership = ProjectMembership(user_id=user.id, project_id=project_id, role=role)
        db.session.add(membership)
        _log(project_id, f'Added member {username} as {role}')
        db.session.commit()
        flash(f'{username} added as {role}.', 'success')

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

                # Check if this structure type already exists for this specimen
                existing_struct = Structure.query.filter_by(
                    specimen_id=specimen.id, structure_type=structure_type
                ).first()

                if existing_struct:
                    # Update landmarks on existing structure
                    existing_struct.landmarks_json = landmarks
                    existing_struct.landmark_count = len(landmarks)
                    existing_struct.landmarks_confirmed = True
                else:
                    structure = Structure(
                        specimen_id=specimen.id,
                        structure_type=structure_type,
                        landmarks_json=landmarks,
                        landmark_count=len(landmarks),
                        landmarks_confirmed=True,
                    )
                    db.session.add(structure)

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

    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_dir, exist_ok=True)

    IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff'}
    imported = 0
    skipped = 0

    with db.session.no_autoflush:
        for fname in os.listdir(folder_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue

            species_name = os.path.splitext(fname)[0].replace('_', ' ').strip()

            # Find matching specimen
            specimen = Specimen.query.filter_by(
                project_id=project_id, species_name=species_name
            ).first()
            if not specimen:
                skipped += 1
                continue

            # Find or create structure of this type
            structure = Structure.query.filter_by(
                specimen_id=specimen.id, structure_type=structure_type
            ).first()
            if not structure:
                structure = Structure(
                    specimen_id=specimen.id,
                    structure_type=structure_type,
                )
                db.session.add(structure)
                db.session.flush()

            # Copy image to uploads
            dest_name = f'{structure.id}_{secure_filename(fname)}'
            dest_path = os.path.join(upload_dir, dest_name)
            shutil.copy2(os.path.join(folder_path, fname), dest_path)
            structure.image_path = dest_name
            imported += 1

    db.session.commit()
    _log(project_id, f'Imported {imported} images from {folder_path}')
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
    files = []
    for fname in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in IMAGE_EXTS:
            continue
        species_name = os.path.splitext(fname)[0].replace('_', ' ').strip()
        matched = Specimen.query.filter_by(
            project_id=project_id, species_name=species_name
        ).first() is not None
        files.append({
            'filename': fname,
            'species': species_name,
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
