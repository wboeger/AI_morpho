"""Auto-generation of species descriptions and higher-taxon diagnoses."""
from collections import defaultdict
from app.models import (
    Project, Specimen, Structure, CharacterDefinition, CharacterValue, TaxonomicGroup
)


def generate_species_description(specimen_id: int, project_id: int) -> str:
    """Generate a taxonomic description for a species from its character matrix."""
    specimen = Specimen.query.get(specimen_id)
    structures = Structure.query.filter_by(specimen_id=specimen_id).all()

    characters = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code).all()

    # Group characters by structure type
    char_by_type = defaultdict(list)
    for char in characters:
        char_by_type[char.structure_type].append(char)

    # Get values for each structure
    values = {}  # char_code -> state info
    for struct in structures:
        for char in char_by_type.get(struct.structure_type, []):
            val = CharacterValue.query.filter_by(
                structure_id=struct.id, character_id=char.id
            ).first()
            if val and val.state and val.state != '?':
                state_name = _get_state_name(char, val.state)
                values[char.code] = {
                    'state': val.state,
                    'name': state_name,
                    'raw_value': val.raw_value,
                }

    # Build description
    sections = []

    # Hook description
    hook_text = _describe_hooks(values)
    if hook_text:
        sections.append(f"Marginal hooks: {hook_text}")

    # Anchor description
    anchor_text = _describe_anchors(values)
    if anchor_text:
        sections.append(f"Anchors: {anchor_text}")

    # Superficial bar
    sbar_text = _describe_superficial_bar(values)
    if sbar_text:
        sections.append(f"Superficial bar: {sbar_text}")

    # Deep bar
    dbar_text = _describe_deep_bar(values)
    if dbar_text:
        sections.append(f"Deep bar: {dbar_text}")

    # MCO
    mco_text = _describe_mco(values)
    if mco_text:
        sections.append(f"Male copulatory organ: {mco_text}")

    description = f"{specimen.species_name}\n\n" + "\n\n".join(sections)
    return description


def _get_state_name(char_def, state_code):
    """Get the name of a state from its code."""
    if state_code == '-':
        return 'inapplicable'
    for s in (char_def.states_json or []):
        if s['code'] == state_code:
            return s['name']
    return state_code


def _v(values, code):
    """Get state name for a character code, or None."""
    entry = values.get(code)
    if entry and entry['state'] != '-':
        return entry['name']
    return None


def _describe_hooks(v):
    parts = []
    if _v(v, 'C01'):
        parts.append(f"Point {_v(v, 'C01')} relative to shaft")
    if _v(v, 'C02'):
        parts.append(_v(v, 'C02'))
    if _v(v, 'C03'):
        parts.append(f"point outline {_v(v, 'C03')}")
    if _v(v, 'C04'):
        parts.append(f"point extending {_v(v, 'C04')} relative to toe")
    if _v(v, 'C05'):
        parts.append(f"shaft {_v(v, 'C05')}")
    if _v(v, 'C06'):
        parts.append(f"shaft {_v(v, 'C06')} relative to base")
    if _v(v, 'C07'):
        parts.append(f"shelf {_v(v, 'C07')}")
    if _v(v, 'C08'):
        parts.append(f"base {_v(v, 'C08')}")
    if _v(v, 'C09'):
        parts.append(f"base {_v(v, 'C09')} relative to heel")
    if _v(v, 'C10'):
        parts.append(f"heel {_v(v, 'C10')}")
    if _v(v, 'C11'):
        parts.append(f"heel {_v(v, 'C11')}")
    if _v(v, 'C12'):
        parts.append(f"heel-shaft transition {_v(v, 'C12')}")
    return "; ".join(parts) + "." if parts else ""


def _describe_anchors(v):
    parts = []
    if _v(v, 'A01'):
        parts.append(f"Point {_v(v, 'A01')} relative to shaft")
    if _v(v, 'A02'):
        parts.append(_v(v, 'A02'))
    if _v(v, 'A03'):
        parts.append(f"superficial root {_v(v, 'A03')} relative to shaft")
    if _v(v, 'A04'):
        parts.append(f"deep root {_v(v, 'A04')}")
    if _v(v, 'A05'):
        parts.append(f"root divergence {_v(v, 'A05')}")
    if _v(v, 'A06'):
        parts.append(f"superficial root {_v(v, 'A06')}")
    if _v(v, 'A07'):
        parts.append(f"deep root {_v(v, 'A07')}")
    if _v(v, 'A08'):
        parts.append(f"sclerite at superficial root tip {_v(v, 'A08')}")
    if _v(v, 'A09'):
        parts.append(f"shaft-root angle {_v(v, 'A09')}")
    return "; ".join(parts) + "." if parts else ""


def _describe_superficial_bar(v):
    parts = []
    if _v(v, 'B01'):
        parts.append(f"membrane {_v(v, 'B01')}")
    if _v(v, 'B02'):
        parts.append(f"shield {_v(v, 'B02')}")
    if _v(v, 'B03'):
        parts.append(f"shield ribs {_v(v, 'B03')}")
    if _v(v, 'B04'):
        parts.append(f"posterior knob {_v(v, 'B04')}")
    if _v(v, 'B05'):
        parts.append(f"shield distal margin {_v(v, 'B05')}")
    if _v(v, 'B06'):
        parts.append(f"anterolateral projections {_v(v, 'B06')}")
    return "; ".join(parts) + "." if parts else ""


def _describe_deep_bar(v):
    parts = []
    if _v(v, 'D01'):
        parts.append(f"extremity ornaments {_v(v, 'D01')}")
    if _v(v, 'D02'):
        parts.append(f"midlength notch {_v(v, 'D02')}")
    if _v(v, 'D03'):
        parts.append(_v(v, 'D03'))
    return "; ".join(parts) + "." if parts else ""


def _describe_mco(v):
    parts = []
    if _v(v, 'M01'):
        parts.append(f"bulb {_v(v, 'M01')}")
    if _v(v, 'M06'):
        parts.append(_v(v, 'M06'))
    if _v(v, 'M02'):
        parts.append(f"principal spine {_v(v, 'M02')}")
    if _v(v, 'M03'):
        parts.append(_v(v, 'M03'))
    if _v(v, 'M04'):
        parts.append(f"spinelets {_v(v, 'M04')}")
    m05 = v.get('M05')
    if m05 and m05['state'] != '-':
        parts.append(f"{m05['state']} spinelets")
    return "; ".join(parts) + "." if parts else ""


def generate_group_diagnosis(group_id: int, project_id: int) -> str:
    """Generate a diagnosis for a taxonomic group."""
    group = TaxonomicGroup.query.get(group_id)
    if not group or not group.included_species:
        return ""

    characters = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code).all()

    # Collect states per character for species in this group
    group_states = defaultdict(set)  # char_code -> set of states
    group_species_states = defaultdict(dict)  # char_code -> {state: [species]}

    for species_name in group.included_species:
        specimen = Specimen.query.filter_by(
            project_id=project_id, species_name=species_name
        ).first()
        if not specimen:
            continue

        structures = Structure.query.filter_by(specimen_id=specimen.id).all()
        for char in characters:
            struct = next((s for s in structures if s.structure_type == char.structure_type), None)
            if not struct:
                continue
            val = CharacterValue.query.filter_by(
                structure_id=struct.id, character_id=char.id
            ).first()
            if val and val.state and val.state not in ('?', '-'):
                group_states[char.code].add(val.state)
                if val.state not in group_species_states[char.code]:
                    group_species_states[char.code][val.state] = []
                group_species_states[char.code][val.state].append(species_name)

    # Collect states for all OTHER groups at same rank for autapomorphies
    other_groups = TaxonomicGroup.query.filter(
        TaxonomicGroup.project_id == project_id,
        TaxonomicGroup.rank == group.rank,
        TaxonomicGroup.id != group.id,
    ).all()

    outgroup_states = defaultdict(set)
    for og in other_groups:
        for species_name in (og.included_species or []):
            specimen = Specimen.query.filter_by(
                project_id=project_id, species_name=species_name
            ).first()
            if not specimen:
                continue
            structures = Structure.query.filter_by(specimen_id=specimen.id).all()
            for char in characters:
                struct = next((s for s in structures if s.structure_type == char.structure_type), None)
                if not struct:
                    continue
                val = CharacterValue.query.filter_by(
                    structure_id=struct.id, character_id=char.id
                ).first()
                if val and val.state and val.state not in ('?', '-'):
                    outgroup_states[char.code].add(val.state)

    # Build diagnosis
    char_by_code = {c.code: c for c in characters}

    invariant = []
    variable = []
    autapomorphic = []

    for code, states in sorted(group_states.items()):
        char = char_by_code.get(code)
        if not char:
            continue

        state_names = [_get_state_name(char, s) for s in states]

        if len(states) == 1:
            state = list(states)[0]
            invariant.append(f"{char.name}: {_get_state_name(char, state)}")

            # Check if autapomorphic
            if state not in outgroup_states.get(code, set()):
                autapomorphic.append(f"{char.name}: {_get_state_name(char, state)}")
        else:
            # Variable
            parts = []
            for state, species_list in group_species_states[code].items():
                sname = _get_state_name(char, state)
                parts.append(f"{sname} in {', '.join(species_list)}")
            variable.append(f"{char.name} ({'; '.join(parts)})")

    # Build text
    lines = [f"{group.name} ({group.rank})\n"]

    if autapomorphic:
        lines.append(f"Distinguished from other {group.rank or 'taxa'} by: {'; '.join(autapomorphic)}.\n")

    if invariant:
        lines.append(f"Characterized by: {'; '.join(invariant)}.\n")

    if variable:
        lines.append(f"Variable in: {'; '.join(variable)}.\n")

    return "\n".join(lines)
