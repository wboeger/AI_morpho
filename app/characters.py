"""Character assignment engine and default character library."""
import numpy as np
from app import db
from app.geometry import (
    arc_length, chord_length, sinuosity, mean_curvature, max_curvature,
    junction_angle, direction_vector, angle_between_vectors,
    relative_vertical_position, circularity,
)


# ---------------------------------------------------------------------------
# Geometric operation registry
# ---------------------------------------------------------------------------

GEOMETRIC_OPERATIONS = {
    'ratio_arc_length': {
        'label': 'Ratio of arc lengths',
        'formula_template': 'arc_length({part_a}) / arc_length({part_b})',
        'requires_parts': 2,
    },
    'sinuosity': {
        'label': 'Sinuosity of part',
        'formula_template': 'arc_length({part_a}) / chord_length({part_a})',
        'requires_parts': 1,
    },
    'mean_curvature': {
        'label': 'Mean curvature of part',
        'formula_template': 'mean(|local_curvature({part_a})|)',
        'requires_parts': 1,
    },
    'max_curvature': {
        'label': 'Max curvature of part',
        'formula_template': 'max(|local_curvature({part_a})|)',
        'requires_parts': 1,
    },
    'junction_angle': {
        'label': 'Junction angle between parts',
        'formula_template': 'junction_angle({part_a}, {part_b})',
        'requires_parts': 2,
    },
    'direction_angle': {
        'label': 'Direction angle between parts',
        'formula_template': 'angle(direction({part_a}), direction({part_b}))',
        'requires_parts': 2,
    },
    'relative_position': {
        'label': 'Relative vertical position',
        'formula_template': 'vertical_displacement({part_a}, {part_b})',
        'requires_parts': 2,
    },
    'presence_threshold': {
        'label': 'Presence/absence by arc length fraction',
        'formula_template': 'arc_length({part_a}) / total_arc_length',
        'requires_parts': 1,
    },
    'sinuosity_with_direction': {
        'label': 'Sinuosity with inward/outward direction',
        'formula_template': 'sinuosity_with_direction({part_a})',
        'requires_parts': 1,
    },
    'angle_between_parts': {
        'label': 'Angle between two parts at fork',
        'formula_template': 'angle(direction({part_a}), direction({part_b}))',
        'requires_parts': 2,
    },
    'custom': {
        'label': 'Custom formula',
        'formula_template': '',
        'requires_parts': 0,
    },
}


def extract_part_coords(landmarks: list, boundary: dict, part_name: str) -> np.ndarray:
    """Extract coordinates for a named anatomical part.

    Args:
        landmarks: list of [x, y] coordinates
        boundary: dict mapping part_name -> list of landmark indices
        part_name: the part to extract
    """
    if part_name not in boundary:
        return np.array([])
    coords = np.array(landmarks)
    indices = [i for i in boundary[part_name] if 0 <= i < len(coords)]
    if not indices:
        return np.array([])
    return coords[indices]


def compute_geometric_value(operation: str, parts_coords: dict,
                            all_landmarks: np.ndarray, formula: str = None) -> float:
    """Compute a raw geometric value given an operation and part coordinates.

    Args:
        operation: one of GEOMETRIC_OPERATIONS keys
        parts_coords: dict mapping part name -> np.ndarray of coords
        all_landmarks: full landmark array
        formula: custom formula string (for 'custom' operation)
    """
    part_names = list(parts_coords.keys())

    if operation == 'ratio_arc_length' and len(part_names) >= 2:
        a = arc_length(parts_coords[part_names[0]])
        b = arc_length(parts_coords[part_names[1]])
        return a / b if b > 1e-10 else 0.0

    elif operation == 'sinuosity' and len(part_names) >= 1:
        return sinuosity(parts_coords[part_names[0]])

    elif operation == 'mean_curvature' and len(part_names) >= 1:
        return mean_curvature(parts_coords[part_names[0]])

    elif operation == 'max_curvature' and len(part_names) >= 1:
        return max_curvature(parts_coords[part_names[0]])

    elif operation == 'junction_angle' and len(part_names) >= 2:
        return junction_angle(parts_coords[part_names[0]], parts_coords[part_names[1]])

    elif operation == 'direction_angle' and len(part_names) >= 2:
        v1 = direction_vector(parts_coords[part_names[0]], end='end')
        v2 = direction_vector(parts_coords[part_names[1]], end='start')
        return angle_between_vectors(v1, v2)

    elif operation == 'relative_position' and len(part_names) >= 2:
        return relative_vertical_position(parts_coords[part_names[0]],
                                          parts_coords[part_names[1]])

    elif operation == 'presence_threshold' and len(part_names) >= 1:
        part_len = arc_length(parts_coords[part_names[0]])
        total_len = arc_length(all_landmarks)
        return part_len / total_len if total_len > 1e-10 else 0.0

    elif operation == 'sinuosity_with_direction' and len(part_names) >= 1:
        # Returns signed sinuosity: positive = curves outward, negative = inward
        coords = parts_coords[part_names[0]]
        s = sinuosity(coords)
        # Determine direction using cross product of chord with midpoint offset
        if len(coords) >= 3:
            chord = coords[-1] - coords[0]
            mid = coords[len(coords) // 2]
            chord_mid = mid - coords[0]
            cross = chord[0] * chord_mid[1] - chord[1] * chord_mid[0]
            return s * np.sign(cross) if abs(cross) > 1e-10 else s
        return s

    elif operation == 'angle_between_parts' and len(part_names) >= 2:
        v1 = direction_vector(parts_coords[part_names[0]], end='start')
        v2 = direction_vector(parts_coords[part_names[1]], end='start')
        return angle_between_vectors(v1, v2)

    return 0.0


def map_value_to_state(raw_value: float, states: list) -> tuple[str, float]:
    """Map a raw continuous value to a discrete state using thresholds.

    Returns (state_code, confidence).
    """
    best_state = '?'
    best_confidence = 0.0

    for state in states:
        t_min = state.get('threshold_min')
        t_max = state.get('threshold_max')

        in_range = True
        if t_min is not None and raw_value < t_min:
            in_range = False
        if t_max is not None and raw_value >= t_max:
            in_range = False

        if in_range:
            # Compute confidence: distance from nearest threshold
            distances = []
            if t_min is not None:
                distances.append(abs(raw_value - t_min))
            if t_max is not None:
                distances.append(abs(raw_value - t_max))

            if distances:
                min_dist = min(distances)
                # Normalize: farther from threshold = higher confidence
                span = (t_max or raw_value + 1) - (t_min or raw_value - 1)
                confidence = min(1.0, min_dist / max(span * 0.5, 1e-10))
            else:
                confidence = 1.0

            if confidence > best_confidence:
                best_state = state['code']
                best_confidence = confidence

    # If no thresholds matched, find closest state
    if best_state == '?' and states:
        best_state = states[0]['code']
        best_confidence = 0.3

    return best_state, best_confidence


def check_dependencies(character_def, structure, project_id) -> bool:
    """Check if a character is inapplicable due to dependencies.

    Returns True if the character IS inapplicable.
    """
    deps = character_def.dependencies_json
    if not deps:
        return False

    from app.models import CharacterDefinition, CharacterValue

    for dep in deps:
        dep_char = CharacterDefinition.query.filter_by(
            project_id=project_id, code=dep['if_character']
        ).first()
        if dep_char:
            dep_val = CharacterValue.query.filter_by(
                structure_id=structure.id, character_id=dep_char.id
            ).first()
            if dep_val and dep_val.state == dep['if_state']:
                return True
    return False


def assign_character(structure, character_def, project_id,
                     aligned_landmarks=None) -> dict:
    """Compute and assign a single geometric character for a structure.

    Args:
        structure: Structure model instance
        character_def: CharacterDefinition model instance
        project_id: project ID
        aligned_landmarks: optional Procrustes-aligned landmarks (list of [x,y]).
                          If None, uses structure.landmarks_json (raw).

    Returns dict with raw_value, state, confidence, auto_assigned.
    """
    if character_def.computation_type != 'geometric':
        return {'raw_value': None, 'state': '?', 'confidence': 0.0, 'auto_assigned': False}

    landmarks_data = aligned_landmarks if aligned_landmarks is not None else structure.landmarks_json
    if not landmarks_data or not structure.boundary_json:
        return {'raw_value': None, 'state': '?', 'confidence': 0.0, 'auto_assigned': False}

    # Check dependencies
    if check_dependencies(character_def, structure, project_id):
        return {'raw_value': None, 'state': '-', 'confidence': 1.0, 'auto_assigned': True}

    landmarks = np.array(landmarks_data)
    boundary = structure.boundary_json
    parts = character_def.parts_involved or []

    parts_coords = {}
    for part_name in parts:
        coords = extract_part_coords(landmarks_data, boundary, part_name)
        if len(coords) > 0:
            parts_coords[part_name] = coords

    if not parts_coords:
        return {'raw_value': None, 'state': '?', 'confidence': 0.0, 'auto_assigned': False}

    raw_value = compute_geometric_value(
        character_def.geometric_operation, parts_coords, landmarks, character_def.formula
    )

    states = character_def.states_json or []
    state, confidence = map_value_to_state(raw_value, states)

    return {
        'raw_value': raw_value,
        'state': state,
        'confidence': confidence,
        'auto_assigned': True,
    }


def compute_all_characters(structure, project_id, force_recompute=False,
                           aligned_landmarks=None):
    """Compute all active geometric characters for a structure.

    Args:
        structure: Structure model instance
        project_id: project ID
        force_recompute: if True, overwrite manual overrides
        aligned_landmarks: optional Procrustes-aligned landmarks.
                          If None, uses raw landmarks.

    Preserves manual overrides unless force_recompute is True.
    """
    from app.models import CharacterDefinition, CharacterValue

    characters = CharacterDefinition.query.filter_by(
        project_id=project_id,
        structure_type=structure.structure_type,
        active=True
    ).all()

    results = []
    for char_def in characters:
        existing = CharacterValue.query.filter_by(
            structure_id=structure.id, character_id=char_def.id
        ).first()

        # Skip if manually overridden and not force recomputing
        if existing and existing.override_by and not force_recompute:
            results.append(existing)
            continue

        if char_def.computation_type == 'geometric':
            result = assign_character(structure, char_def, project_id,
                                      aligned_landmarks=aligned_landmarks)

            if existing:
                existing.raw_value = result['raw_value']
                existing.state = result['state']
                existing.confidence = result['confidence']
                existing.auto_assigned = result['auto_assigned']
            else:
                existing = CharacterValue(
                    structure_id=structure.id,
                    character_id=char_def.id,
                    **result
                )
                db.session.add(existing)

            results.append(existing)
        else:
            # Manual character — create placeholder if doesn't exist
            if not existing:
                existing = CharacterValue(
                    structure_id=structure.id,
                    character_id=char_def.id,
                    state='?',
                    confidence=0.0,
                    auto_assigned=False,
                )
                db.session.add(existing)
            results.append(existing)

    db.session.commit()
    return results


def compute_batch_with_procrustes(project_id, structure_type=None,
                                  force_recompute=False):
    """Batch-compute characters using Generalized Procrustes Analysis.

    Runs GPA on all structures of each type, then computes characters
    from the aligned coordinates. This ensures scale- and orientation-
    independent character states.

    Args:
        project_id: project ID
        structure_type: optional filter for a single structure type
        force_recompute: if True, overwrite manual overrides

    Returns:
        number of structures computed
    """
    from app.models import Specimen, Structure
    from app.procrustes import generalized_procrustes

    structure_types = [structure_type] if structure_type else \
        ['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco']

    total_computed = 0

    for stype in structure_types:
        # Get all structures of this type with landmarks and boundaries
        structures = (Structure.query
                      .join(Specimen)
                      .filter(
                          Specimen.project_id == project_id,
                          Structure.structure_type == stype,
                          Structure.landmarks_json.isnot(None),
                          Structure.boundary_json.isnot(None),
                      ).all())

        if not structures:
            continue

        # Group by landmark count (GPA requires same number of landmarks)
        by_count = {}
        for st in structures:
            n = len(st.landmarks_json)
            by_count.setdefault(n, []).append(st)

        for count, group in by_count.items():
            if len(group) < 2:
                # Only one specimen — no GPA needed, use raw (scale to unit)
                from app.procrustes import scale_to_unit
                for st in group:
                    aligned = scale_to_unit(np.array(st.landmarks_json))
                    compute_all_characters(st, project_id,
                                           force_recompute=force_recompute,
                                           aligned_landmarks=aligned.tolist())
                    total_computed += 1
                continue

            # Run GPA
            raw_arrays = [np.array(st.landmarks_json) for st in group]
            aligned_arrays, mean_shape = generalized_procrustes(raw_arrays)

            # Compute characters from aligned coordinates
            for st, aligned in zip(group, aligned_arrays):
                compute_all_characters(st, project_id,
                                       force_recompute=force_recompute,
                                       aligned_landmarks=aligned.tolist())
                total_computed += 1

    return total_computed


# ---------------------------------------------------------------------------
# Default character library
# ---------------------------------------------------------------------------

def get_default_characters() -> list[dict]:
    """Return the full default character library for project initialization."""
    return (
        _hook_characters() +
        _anchor_characters() +
        _superficial_bar_characters() +
        _deep_bar_characters() +
        _mco_characters()
    )


def _hook_characters():
    return [
        {
            'code': 'C01', 'name': 'Point length',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Point', 'Shaft'],
            'geometric_operation': 'ratio_arc_length',
            'formula': 'arc_length(Point) / arc_length(Shaft)',
            'states_json': [
                {'code': '0', 'name': 'moderate', 'description': 'Point approximately half shaft length', 'threshold_min': 0.49, 'threshold_max': 0.84},
                {'code': '1', 'name': 'long', 'description': 'Point longer than shaft', 'threshold_min': 1.08, 'threshold_max': None},
                {'code': '2', 'name': 'subequal', 'description': 'Point and shaft approximately equal', 'threshold_min': 0.84, 'threshold_max': 1.08},
                {'code': '3', 'name': 'very short', 'description': 'Point much shorter than shaft', 'threshold_min': None, 'threshold_max': 0.49},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C02', 'name': 'Point curvature',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Point', 'Shaft'],
            'geometric_operation': 'junction_angle',
            'formula': 'angle at Shaft to Point junction',
            'states_json': [
                {'code': '0', 'name': 'evenly curved', 'description': 'Smooth transition, no abrupt bend', 'threshold_min': 140, 'threshold_max': None},
                {'code': '1', 'name': 'recurved', 'description': 'Point curves back sharply', 'threshold_min': None, 'threshold_max': 80},
                {'code': '2', 'name': 'approximately 90 degrees', 'description': 'Near right-angle junction', 'threshold_min': 80, 'threshold_max': 140},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C03', 'name': 'Point waviness',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Point'],
            'geometric_operation': 'sinuosity',
            'formula': 'arc_length(Point) / chord_length(Point)',
            'states_json': [
                {'code': '0', 'name': 'gently curved', 'description': 'Low sinuosity (tight arc)', 'threshold_min': None, 'threshold_max': 18},
                {'code': '1', 'name': 'moderately curved', 'description': 'Moderate sinuosity', 'threshold_min': 18, 'threshold_max': 35},
                {'code': '2', 'name': 'strongly curved', 'description': 'High sinuosity (long winding point)', 'threshold_min': 35, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C04', 'name': 'Point vs Toe level',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Point', 'Toe'],
            'geometric_operation': 'relative_position',
            'formula': 'vertical displacement between Point tip and Toe tip, normalized',
            'states_json': [
                {'code': '0', 'name': 'point well above toe', 'description': 'Point tip far above toe level', 'threshold_min': None, 'threshold_max': -0.85},
                {'code': '1', 'name': 'point above toe', 'description': 'Point tip moderately above toe', 'threshold_min': -0.85, 'threshold_max': -0.70},
                {'code': '2', 'name': 'point slightly above toe', 'description': 'Point and toe at similar height', 'threshold_min': -0.70, 'threshold_max': -0.40},
                {'code': '3', 'name': 'approximately level', 'description': 'Point near or below toe level', 'threshold_min': -0.40, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C05', 'name': 'Shaft curvature',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Shaft'],
            'geometric_operation': 'mean_curvature',
            'formula': 'mean local curvature along Shaft',
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Shaft nearly straight', 'threshold_min': None, 'threshold_max': 11},
                {'code': '1', 'name': 'slightly curved', 'description': 'Gentle curvature', 'threshold_min': 11, 'threshold_max': 16},
                {'code': '2', 'name': 'curved', 'description': 'Conspicuous curvature', 'threshold_min': 16, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C06', 'name': 'Shaft angle',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Shaft', 'Base'],
            'geometric_operation': 'direction_angle',
            'formula': 'angle between Shaft direction vector and Base direction vector',
            'states_json': [
                {'code': '0', 'name': 'strongly divergent', 'description': 'Shaft nearly opposite base direction', 'threshold_min': 155, 'threshold_max': None},
                {'code': '1', 'name': 'moderately divergent', 'description': 'Shaft angled away from base', 'threshold_min': 130, 'threshold_max': 155},
                {'code': '2', 'name': 'weakly divergent', 'description': 'Shaft at near right-angle to base', 'threshold_min': None, 'threshold_max': 130},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C07', 'name': 'Shelf profile',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Shelf'],
            'geometric_operation': 'sinuosity',
            'formula': 'arc_length(Shelf) / chord_length(Shelf)',
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Shelf outline straight', 'threshold_min': None, 'threshold_max': 1.05},
                {'code': '1', 'name': 'slightly wavy', 'description': 'Minor undulation', 'threshold_min': 1.05, 'threshold_max': 1.15},
                {'code': '2', 'name': 'wavy', 'description': 'Conspicuously wavy shelf', 'threshold_min': 1.15, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C08', 'name': 'Base profile',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Base'],
            'geometric_operation': 'sinuosity',
            'formula': 'arc_length(Base) / chord_length(Base)',
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Base outline straight', 'threshold_min': None, 'threshold_max': 1.05},
                {'code': '1', 'name': 'slightly wavy', 'description': 'Minor undulation', 'threshold_min': 1.05, 'threshold_max': 1.15},
                {'code': '2', 'name': 'wavy', 'description': 'Conspicuously wavy base', 'threshold_min': 1.15, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C09', 'name': 'Base-Heel ratio',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Base', 'Heel'],
            'geometric_operation': 'ratio_arc_length',
            'formula': 'arc_length(Base) / arc_length(Heel)',
            'states_json': [
                {'code': '0', 'name': 'base much longer', 'description': 'Base dominates', 'threshold_min': 1.5, 'threshold_max': None},
                {'code': '1', 'name': 'subequal', 'description': 'Base and heel approximately equal', 'threshold_min': 0.67, 'threshold_max': 1.5},
                {'code': '2', 'name': 'heel longer', 'description': 'Heel dominates', 'threshold_min': None, 'threshold_max': 0.67},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C10', 'name': 'Heel conspicuousness',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Heel'],
            'geometric_operation': 'presence_threshold',
            'formula': 'arc_length(Heel) / total_arc_length',
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No discernible heel', 'threshold_min': None, 'threshold_max': 0.03},
                {'code': '1', 'name': 'conspicuous', 'description': 'Heel clearly present', 'threshold_min': 0.03, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'C11', 'name': 'Heel profile',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Heel'],
            'geometric_operation': 'sinuosity',
            'formula': 'arc_length(Heel) / chord_length(Heel)',
            'states_json': [
                {'code': '0', 'name': 'smooth', 'description': 'Heel outline smooth', 'threshold_min': None, 'threshold_max': 1.15},
                {'code': '1', 'name': 'slightly wavy', 'description': 'Minor undulation', 'threshold_min': 1.15, 'threshold_max': 1.35},
                {'code': '2', 'name': 'wavy', 'description': 'Conspicuously undulating', 'threshold_min': 1.35, 'threshold_max': None},
            ],
            'dependencies_json': [{'if_character': 'C10', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'C12', 'name': 'Heel-Shaft transition',
            'structure_type': 'hook', 'computation_type': 'geometric',
            'parts_involved': ['Heel', 'Shaft'],
            'geometric_operation': 'junction_angle',
            'formula': 'angle at Heel to Shaft junction',
            'states_json': [
                {'code': '0', 'name': 'abrupt', 'description': 'Sharp angle at transition', 'threshold_min': None, 'threshold_max': 150},
                {'code': '1', 'name': 'moderate', 'description': 'Moderate transition', 'threshold_min': 150, 'threshold_max': 170},
                {'code': '2', 'name': 'gradual', 'description': 'Smooth transition', 'threshold_min': 170, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
    ]


def _anchor_characters():
    return [
        {
            'code': 'A01', 'name': 'Point length',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['Point', 'Shaft'],
            'geometric_operation': 'ratio_arc_length',
            'formula': 'arc_length(Point) / arc_length(Shaft)',
            'states_json': [
                {'code': '0', 'name': 'short', 'description': '0.5-1.0 of shaft', 'threshold_min': 0.5, 'threshold_max': 1.0},
                {'code': '1', 'name': 'long', 'description': '>=1.0 of shaft', 'threshold_min': 1.0, 'threshold_max': None},
                {'code': '2', 'name': 'very short', 'description': '<0.5 of shaft', 'threshold_min': None, 'threshold_max': 0.5},
                {'code': '3', 'name': 'approximately half shaft', 'description': '~0.5 of shaft', 'threshold_min': 0.45, 'threshold_max': 0.55},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A02', 'name': 'Point curvature',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['Point', 'Shaft'],
            'geometric_operation': 'junction_angle',
            'formula': 'angle at Shaft to Point junction',
            'states_json': [
                {'code': '0', 'name': 'evenly curved', 'description': 'Smooth transition', 'threshold_min': 140, 'threshold_max': None},
                {'code': '1', 'name': 'recurved', 'description': 'Sharp recurve', 'threshold_min': None, 'threshold_max': 80},
                {'code': '2', 'name': 'approximately 90 degrees', 'description': 'Near right angle', 'threshold_min': 80, 'threshold_max': 140},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A03', 'name': 'Superficial root length',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['SuperficialRoot', 'Shaft'],
            'geometric_operation': 'ratio_arc_length',
            'formula': 'arc_length(SuperficialRoot) / arc_length(Shaft)',
            'states_json': [
                {'code': '0', 'name': 'shorter', 'description': 'Root shorter than shaft', 'threshold_min': None, 'threshold_max': 0.8},
                {'code': '1', 'name': 'subequal', 'description': 'Root approximately equal to shaft', 'threshold_min': 0.8, 'threshold_max': 1.2},
                {'code': '2', 'name': 'longer', 'description': 'Root longer than shaft', 'threshold_min': 1.2, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A04', 'name': 'Deep root form',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['DeepRoot', 'Shaft'],
            'geometric_operation': 'ratio_arc_length',
            'formula': 'arc_length(DeepRoot) / arc_length(Shaft)',
            'states_json': [
                {'code': '0', 'name': 'knob-shaped', 'description': 'Rudimentary, barely protruding', 'threshold_min': None, 'threshold_max': 0.3},
                {'code': '1', 'name': 'distinct root', 'description': 'Clearly formed root', 'threshold_min': 0.3, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A05', 'name': 'Root divergence angle',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['SuperficialRoot', 'DeepRoot'],
            'geometric_operation': 'angle_between_parts',
            'formula': 'angle between root direction vectors at fork point',
            'states_json': [
                {'code': '0', 'name': 'acute', 'description': '<70 degrees', 'threshold_min': None, 'threshold_max': 70},
                {'code': '1', 'name': 'right angle', 'description': '70-120 degrees', 'threshold_min': 70, 'threshold_max': 120},
                {'code': '2', 'name': 'obtuse', 'description': '>120 degrees', 'threshold_min': 120, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A06', 'name': 'Superficial root profile',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['SuperficialRoot'],
            'geometric_operation': 'sinuosity_with_direction',
            'formula': 'sinuosity with inward/outward direction of SuperficialRoot',
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Root outline straight', 'threshold_min': -1.03, 'threshold_max': 1.03},
                {'code': '1', 'name': 'curved inward', 'description': 'Root curves medially', 'threshold_min': None, 'threshold_max': -1.03},
                {'code': '2', 'name': 'curved outward', 'description': 'Root curves laterally', 'threshold_min': 1.03, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A07', 'name': 'Deep root profile',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['DeepRoot'],
            'geometric_operation': 'sinuosity',
            'formula': 'arc_length(DeepRoot) / chord_length(DeepRoot)',
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Deep root outline straight', 'threshold_min': None, 'threshold_max': 1.08},
                {'code': '1', 'name': 'wavy', 'description': 'Deep root undulating', 'threshold_min': 1.08, 'threshold_max': None},
            ],
            'dependencies_json': [{'if_character': 'A04', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'A08', 'name': 'Sclerite at superficial root tip',
            'structure_type': 'anchor', 'computation_type': 'manual',
            'parts_involved': ['SuperficialRoot'],
            'geometric_operation': None,
            'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No sclerite at tip'},
                {'code': '1', 'name': 'present', 'description': 'Sclerite visible at root tip'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'A09', 'name': 'Shaft-superficial root angle',
            'structure_type': 'anchor', 'computation_type': 'geometric',
            'parts_involved': ['Shaft', 'SuperficialRoot'],
            'geometric_operation': 'junction_angle',
            'formula': 'junction angle between Shaft and SuperficialRoot',
            'states_json': [
                {'code': '0', 'name': 'acute', 'description': 'Sharp angle', 'threshold_min': None, 'threshold_max': 70},
                {'code': '1', 'name': 'right angle', 'description': 'Near 90 degrees', 'threshold_min': 70, 'threshold_max': 120},
                {'code': '2', 'name': 'obtuse', 'description': 'Wide angle', 'threshold_min': 120, 'threshold_max': None},
            ],
            'dependencies_json': [],
        },
    ]


def _superficial_bar_characters():
    return [
        {
            'code': 'B01', 'name': 'Membrane shape',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['BarProper'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'thin, long, tapering distally', 'description': 'Proximal margin does not reach extremities of bar (as in G. elegans)'},
                {'code': '1', 'name': 'thin, short', 'description': 'As in Cichlidae nyanzae'},
                {'code': '2', 'name': 'subrectangular', 'description': 'Rectangular outline'},
                {'code': '3', 'name': 'subtriangular', 'description': 'Triangular outline'},
                {'code': '4', 'name': 'distally round', 'description': 'Round distal margin'},
                {'code': '5', 'name': 'subquadrate with midlength constriction', 'description': 'Spathulated (as in G. stunkardi)'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'B02', 'name': 'Shield morphology',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['Shield'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No shield present'},
                {'code': '1', 'name': 'two ribbon-like projections', 'description': 'Paired ribbon extensions'},
                {'code': '2', 'name': 'thin plate', 'description': 'Plate-like shield'},
                {'code': '3', 'name': 'thin ribbon-like structure', 'description': 'Single ribbon-like shield'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'B03', 'name': 'Supporting ribs along shield',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['Shield'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No supporting ribs'},
                {'code': '1', 'name': 'present', 'description': 'Internal structural ribs visible'},
            ],
            'dependencies_json': [{'if_character': 'B02', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'B04', 'name': 'Posterior knob-like structure near midlength',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['BarProper'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No posterior knob'},
                {'code': '1', 'name': 'present', 'description': 'Knob-like projection at midlength'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'B05', 'name': 'Margin of distal end of shield',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['ShieldDistalEnd'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'smooth', 'description': 'Entire, smooth margin'},
                {'code': '1', 'name': 'clefted', 'description': 'With notches or divisions'},
            ],
            'dependencies_json': [{'if_character': 'B02', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'B06', 'name': 'Anterolateral projections',
            'structure_type': 'superficial_bar', 'computation_type': 'manual',
            'parts_involved': ['AnterolateralProcesses'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No anterolateral projections'},
                {'code': '1', 'name': 'incipient', 'description': 'Barely visible'},
                {'code': '2', 'name': 'conspicuous', 'description': '< 0.5 of bar width'},
                {'code': '3', 'name': 'long', 'description': '>= 0.5 of bar width'},
            ],
            'dependencies_json': [],
        },
    ]


def _deep_bar_characters():
    return [
        {
            'code': 'D01', 'name': 'Extremity ornaments',
            'structure_type': 'deep_bar', 'computation_type': 'manual',
            'parts_involved': [],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No terminal ornaments'},
                {'code': '1', 'name': 'single, uniform', 'description': 'Extremities with simple uniform expansion'},
                {'code': '2', 'name': 'bifid', 'description': 'Extremities with subterminal expansion or bifid (as in G. guatopotei)'},
                {'code': '3', 'name': 'tapering', 'description': 'Tapering at extremities following a slight expansion'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'D02', 'name': 'Midlength notch',
            'structure_type': 'deep_bar', 'computation_type': 'manual',
            'parts_involved': [],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'No midlength notch'},
                {'code': '1', 'name': 'present', 'description': 'As in G. mediotorus'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'D03', 'name': 'Overall shape',
            'structure_type': 'deep_bar', 'computation_type': 'manual',
            'parts_involved': [],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'straight', 'description': 'Bar is straight'},
                {'code': '1', 'name': 'gently arched', 'description': 'Slight arch'},
                {'code': '2', 'name': 'saddle-shaped', 'description': 'Saddle-like curvature'},
                {'code': '3', 'name': 'with median notch', 'description': 'Notch at midpoint'},
            ],
            'dependencies_json': [],
        },
    ]


def _mco_characters():
    return [
        {
            'code': 'M01', 'name': 'Bulb morphology',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['Bulb'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'elongate, muscular', 'description': 'As in Afrogyrodactylus, Citharodactylus'},
                {'code': '1', 'name': 'bulbous, spherical', 'description': 'Typical of many Gyrodactylus'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'M02', 'name': 'Principal spine',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['PrincipalSpine'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'absent', 'description': 'As in Gyrdicotylus gallieni'},
                {'code': '1', 'name': 'straight', 'description': 'Embedded in bulbous musculature (as in Scleroductus, Macrogyrodactylus)'},
                {'code': '2', 'name': 'recurved basally', 'description': 'As in many Gyrodactylus spp.'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'M03', 'name': 'Spinelet armature',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['Spinelets'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'unarmed', 'description': 'No spinelets'},
                {'code': '1', 'name': 'armed', 'description': 'Spinelets present'},
            ],
            'dependencies_json': [],
        },
        {
            'code': 'M04', 'name': 'Spinelet arrangement',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['Spinelets'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'single row', 'description': 'Linear arrangement'},
                {'code': '1', 'name': 'one row, mixed sizes', 'description': 'Larger spinelets mingled with smaller'},
                {'code': '2', 'name': 'scattered', 'description': 'Small spinelets randomly distributed'},
                {'code': '3', 'name': 'one row, equally sized', 'description': 'Uniform row'},
                {'code': '4', 'name': 'one row, anterior pair larger', 'description': 'Anteriormost bilateral pair visually larger'},
                {'code': '5', 'name': 'two well-defined rows', 'description': 'Double row of similar-sized spinelets'},
            ],
            'dependencies_json': [{'if_character': 'M03', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'M05', 'name': 'Spinelet count',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['Spinelets'],
            'geometric_operation': None, 'formula': None,
            'states_json': [],  # integer count, not discrete bins
            'dependencies_json': [{'if_character': 'M03', 'if_state': '0', 'then': 'inapplicable'}],
        },
        {
            'code': 'M06', 'name': 'Bulb shape',
            'structure_type': 'mco', 'computation_type': 'manual',
            'parts_involved': ['Bulb'],
            'geometric_operation': None, 'formula': None,
            'states_json': [
                {'code': '0', 'name': 'spherical', 'description': 'Round bulb'},
                {'code': '1', 'name': 'ovoid', 'description': 'Elliptical bulb'},
                {'code': '2', 'name': 'pyriform', 'description': 'Pear-shaped'},
                {'code': '3', 'name': 'irregular', 'description': 'Asymmetric or complex shape'},
            ],
            'dependencies_json': [],
        },
    ]


def initialize_project_characters(project_id: int, user_id: int):
    """Populate a project with the default character library."""
    from app.models import CharacterDefinition

    for char_data in get_default_characters():
        char = CharacterDefinition(
            project_id=project_id,
            created_by=user_id,
            description=char_data.get('formula', ''),
            history_json=[{
                'user': user_id,
                'action': 'created',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'details': 'Default character from library',
            }],
            **{k: v for k, v in char_data.items() if k != 'formula' and k != 'description'},
            formula=char_data.get('formula'),
        )
        db.session.add(char)

    db.session.commit()


from datetime import datetime, timezone
