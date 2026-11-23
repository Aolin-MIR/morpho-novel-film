"""Validate the independently recorded style directions of a study."""


def classification_fields(record):
    if not isinstance(record, dict):
        raise ValueError('Expected a study classification object.')
    result = {}
    for field in ('directions', 'methods'):
        values = record.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value.strip() or value != value.strip()
                       for value in values)
                or len(set(values)) != len(values)):
            raise ValueError(f'Expected distinct text values for {field}.')
        result[field] = values
    status = record.get('classification_status')
    if (status not in ('confirmed', 'pending') or bool(result['directions']) != (status == 'confirmed')):
        raise ValueError('Confirmed directions require a name; pending directions must be empty.')
    return {**result, 'classification_status': status}
