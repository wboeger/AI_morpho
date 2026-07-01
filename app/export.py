"""Export character matrices in CSV, NEXUS, TNT, and JSON formats."""
import csv
import io
import json
from collections import defaultdict
from app.models import (
    Project, Specimen, Structure, CharacterDefinition, CharacterValue,
    DNASequence, TaxonomicGroup, CorrectionHistory
)
from app.descriptions import generate_species_description, generate_group_diagnosis


def build_matrix(project_id: int, structure_type: str = None, dna_only: bool = False) -> dict:
    """Build the complete character matrix.

    Returns dict with:
        species: list of species names
        characters: list of {code, name, structure_type, states}
        matrix: dict of species_name -> {char_code -> state}
        detailed: dict of species_name -> {char_code -> {state, raw_value, confidence}}
    """
    characters = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code).all()

    if structure_type:
        characters = [c for c in characters if c.structure_type == structure_type]

    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()

    if dna_only:
        ids_with_dna = {s.specimen_id for s in
                        DNASequence.query.filter(
                            DNASequence.specimen_id.in_([sp.id for sp in specimens]),
                            DNASequence.available == True
                        ).all()}
        specimens = [s for s in specimens if s.id in ids_with_dna]

    matrix = {}
    detailed = {}

    for specimen in specimens:
        structures = Structure.query.filter_by(specimen_id=specimen.id).all()
        row = {}
        row_detailed = {}

        for char in characters:
            struct = next((s for s in structures if s.structure_type == char.structure_type), None)
            if struct:
                val = CharacterValue.query.filter_by(
                    structure_id=struct.id, character_id=char.id
                ).first()
                if val:
                    row[char.code] = val.state or '?'
                    row_detailed[char.code] = {
                        'state': val.state or '?',
                        'raw_value': val.raw_value,
                        'confidence': val.confidence,
                    }
                else:
                    row[char.code] = '?'
                    row_detailed[char.code] = {'state': '?', 'raw_value': None, 'confidence': 0}
            else:
                row[char.code] = '?'
                row_detailed[char.code] = {'state': '?', 'raw_value': None, 'confidence': 0}

        matrix[specimen.species_name] = row
        detailed[specimen.species_name] = row_detailed

    return {
        'species': [s.species_name for s in specimens],
        'characters': [{
            'code': c.code,
            'name': c.name,
            'structure_type': c.structure_type,
            'states': c.states_json,
        } for c in characters],
        'matrix': matrix,
        'detailed': detailed,
    }


def export_csv(project_id: int, **kwargs) -> str:
    """Export matrix as CSV."""
    data = build_matrix(project_id, **kwargs)
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    char_codes = [c['code'] for c in data['characters']]
    writer.writerow(['Species'] + [f"{c['code']}_{c['name']}" for c in data['characters']])

    # Rows
    for species in data['species']:
        row = [species]
        for code in char_codes:
            row.append(data['matrix'][species].get(code, '?'))
        writer.writerow(row)

    return output.getvalue()


def export_csv_detailed(project_id: int, **kwargs) -> str:
    """Export matrix as CSV with confidence scores."""
    data = build_matrix(project_id, **kwargs)
    output = io.StringIO()
    writer = csv.writer(output)

    char_codes = [c['code'] for c in data['characters']]
    header = ['Species']
    for c in data['characters']:
        header.extend([f"{c['code']}_state", f"{c['code']}_raw", f"{c['code']}_conf"])
    writer.writerow(header)

    for species in data['species']:
        row = [species]
        for code in char_codes:
            d = data['detailed'][species].get(code, {})
            row.extend([d.get('state', '?'), d.get('raw_value', ''), d.get('confidence', '')])
        writer.writerow(row)

    return output.getvalue()


def export_nexus(project_id: int, **kwargs) -> str:
    """Export matrix in NEXUS format (STANDARD datatype) for PAUP*/TNT/etc."""
    data = build_matrix(project_id, **kwargs)
    n_taxa = len(data['species'])
    n_chars = len(data['characters'])
    char_codes = [c['code'] for c in data['characters']]

    # Find max state per character for symbols
    max_state = 0
    for species in data['species']:
        for code in char_codes:
            state = data['matrix'][species].get(code, '?')
            if state not in ('?', '-'):
                try:
                    max_state = max(max_state, int(state))
                except ValueError:
                    pass

    symbols = ''.join(str(i) for i in range(max_state + 1))

    lines = ['#NEXUS', '', 'BEGIN DATA;']
    lines.append(f'  DIMENSIONS NTAX={n_taxa} NCHAR={n_chars};')
    lines.append(f'  FORMAT DATATYPE=STANDARD SYMBOLS="{symbols}" MISSING=? GAP=-;')
    lines.append('')

    # Character labels as comments
    lines.append('  [Character labels:]')
    for i, c in enumerate(data['characters']):
        lines.append(f'  [{i+1}. {c["code"]}: {c["name"]}]')
    lines.append('')

    lines.append('  MATRIX')

    # Pad species names for alignment
    max_name = max(len(s.replace(' ', '_')) for s in data['species']) if data['species'] else 10
    for species in data['species']:
        name = species.replace(' ', '_')
        states = ''
        for code in char_codes:
            s = data['matrix'][species].get(code, '?')
            states += s
        lines.append(f'    {name:<{max_name + 2}} {states}')

    lines.append('  ;')
    lines.append('END;')
    lines.append('')

    return '\n'.join(lines)


def export_tnt(project_id: int, **kwargs) -> str:
    """Export matrix in TNT format for parsimony analysis."""
    data = build_matrix(project_id, **kwargs)
    n_taxa = len(data['species'])
    n_chars = len(data['characters'])
    char_codes = [c['code'] for c in data['characters']]

    lines = ['nstates 32;', f'xread', f'{n_chars} {n_taxa}']

    # Character names as comments
    for i, c in enumerate(data['characters']):
        lines.append(f'& [{i}] {c["code"]}_{c["name"]}')
    lines.append('')

    for species in data['species']:
        name = species.replace(' ', '_')
        states = ''
        for code in char_codes:
            s = data['matrix'][species].get(code, '?')
            states += s
        lines.append(f'{name} {states}')

    lines.append(';')
    lines.append('proc /;')

    return '\n'.join(lines)


def export_json_full(project_id: int) -> str:
    """Export full project data as JSON for reproducibility."""
    project = Project.query.get(project_id)
    data = build_matrix(project_id)

    # Collect all landmarks and boundaries
    specimens_data = []
    for specimen in Specimen.query.filter_by(project_id=project_id).all():
        spec_data = {
            'species_name': specimen.species_name,
            'specimen_id': specimen.specimen_id_label,
            'notes': specimen.notes,
            'dna_sequences': [{
                'marker': d.marker,
                'accession': d.accession,
                'available': d.available,
            } for d in DNASequence.query.filter_by(specimen_id=specimen.id).all()],
            'structures': [],
        }
        for struct in Structure.query.filter_by(specimen_id=specimen.id).all():
            spec_data['structures'].append({
                'type': struct.structure_type,
                'landmarks': struct.landmarks_json,
                'boundaries': struct.boundary_json,
                'landmarks_confirmed': struct.landmarks_confirmed,
                'boundary_confirmed': struct.boundary_confirmed,
            })
        specimens_data.append(spec_data)

    # Character definitions
    chars_data = []
    for char in CharacterDefinition.query.filter_by(project_id=project_id).all():
        chars_data.append({
            'code': char.code,
            'name': char.name,
            'structure_type': char.structure_type,
            'computation_type': char.computation_type,
            'parts_involved': char.parts_involved,
            'geometric_operation': char.geometric_operation,
            'formula': char.formula,
            'states': char.states_json,
            'dependencies': char.dependencies_json,
            'active': char.active,
            'history': char.history_json,
        })

    # Corrections
    corrections = [{
        'character': CorrectionHistory.query.get(c.id).character.code if c.character else None,
        'old_state': c.old_state,
        'new_state': c.new_state,
        'reason': c.reason,
        'timestamp': c.timestamp.isoformat() if c.timestamp else None,
    } for c in CorrectionHistory.query.filter_by(project_id=project_id).all()]

    export = {
        'project': {
            'name': project.name,
            'description': project.description,
        },
        'specimens': specimens_data,
        'character_definitions': chars_data,
        'matrix': data['matrix'],
        'detailed_matrix': data['detailed'],
        'correction_history': corrections,
    }

    return json.dumps(export, indent=2, default=str)


def export_descriptions_text(project_id: int) -> str:
    """Export all species descriptions as plain text."""
    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()
    descriptions = []
    for specimen in specimens:
        desc = generate_species_description(specimen.id, project_id)
        descriptions.append(desc)
    return '\n\n---\n\n'.join(descriptions)


def export_diagnoses_text(project_id: int) -> str:
    """Export all group diagnoses as plain text."""
    groups = TaxonomicGroup.query.filter_by(project_id=project_id).order_by(TaxonomicGroup.rank, TaxonomicGroup.name).all()
    texts = []
    for group in groups:
        if group.diagnosis_text:
            texts.append(group.diagnosis_text)
        else:
            texts.append(generate_group_diagnosis(group.id, project_id))
    return '\n\n---\n\n'.join(texts)
