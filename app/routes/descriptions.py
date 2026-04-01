from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from app.models import Project, Specimen, TaxonomicGroup
from app.descriptions import generate_species_description, generate_group_diagnosis

descriptions_bp = Blueprint('descriptions', __name__)


@descriptions_bp.route('/project/<int:project_id>/descriptions')
@login_required
def species_descriptions(project_id):
    project = Project.query.get_or_404(project_id)
    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()
    return render_template('descriptions/species_list.html',
                           project=project, specimens=specimens)


@descriptions_bp.route('/project/<int:project_id>/descriptions/<int:specimen_id>')
@login_required
def view_description(project_id, specimen_id):
    project = Project.query.get_or_404(project_id)
    specimen = Specimen.query.get_or_404(specimen_id)
    description = generate_species_description(specimen_id, project_id)
    return render_template('descriptions/view_description.html',
                           project=project, specimen=specimen, description=description)


@descriptions_bp.route('/api/project/<int:project_id>/descriptions/<int:specimen_id>/regenerate', methods=['POST'])
@login_required
def regenerate_description(project_id, specimen_id):
    description = generate_species_description(specimen_id, project_id)
    return jsonify({'description': description})


@descriptions_bp.route('/project/<int:project_id>/diagnoses')
@login_required
def diagnoses(project_id):
    project = Project.query.get_or_404(project_id)
    groups = TaxonomicGroup.query.filter_by(project_id=project_id).order_by(TaxonomicGroup.rank, TaxonomicGroup.name).all()
    return render_template('descriptions/diagnoses.html', project=project, groups=groups)


@descriptions_bp.route('/project/<int:project_id>/diagnoses/new', methods=['GET', 'POST'])
@login_required
def new_group(project_id):
    project = Project.query.get_or_404(project_id)
    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()
    all_species = sorted(set(s.species_name for s in specimens))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        rank = request.form.get('rank', '').strip()
        species_list = request.form.getlist('species')

        if not name:
            flash('Group name is required.', 'error')
        else:
            group = TaxonomicGroup(
                project_id=project_id,
                name=name,
                rank=rank,
                included_species=species_list,
            )
            db.session.add(group)
            db.session.commit()

            # Auto-generate diagnosis
            diagnosis = generate_group_diagnosis(group.id, project_id)
            group.diagnosis_text = diagnosis
            group.diagnosis_generated_at = datetime.now(timezone.utc)
            db.session.commit()

            flash(f'Group "{name}" created with diagnosis.', 'success')
            return redirect(url_for('descriptions.diagnoses', project_id=project_id))

    return render_template('descriptions/new_group.html',
                           project=project, all_species=all_species)


@descriptions_bp.route('/project/<int:project_id>/diagnoses/<int:group_id>')
@login_required
def view_diagnosis(project_id, group_id):
    project = Project.query.get_or_404(project_id)
    group = TaxonomicGroup.query.get_or_404(group_id)
    return render_template('descriptions/view_diagnosis.html',
                           project=project, group=group)


@descriptions_bp.route('/api/project/<int:project_id>/diagnoses/<int:group_id>/regenerate', methods=['POST'])
@login_required
def regenerate_diagnosis(project_id, group_id):
    group = TaxonomicGroup.query.get_or_404(group_id)
    diagnosis = generate_group_diagnosis(group_id, project_id)
    group.diagnosis_text = diagnosis
    group.diagnosis_generated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'diagnosis': diagnosis})


@descriptions_bp.route('/api/project/<int:project_id>/diagnoses/<int:group_id>/save', methods=['POST'])
@login_required
def save_diagnosis(project_id, group_id):
    group = TaxonomicGroup.query.get_or_404(group_id)
    data = request.get_json()
    group.diagnosis_text = data.get('text', '')
    db.session.commit()
    return jsonify({'status': 'ok'})
