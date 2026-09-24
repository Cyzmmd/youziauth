"""Named simulation points: the list behind the map picker's dropdown.

The list is the single source of truth for 模拟定位. `location-sample.json` is only a mirror of
whichever entry is active, because the replay path (`locate('simulation', ...)`) reads exactly one
file. Keeping it that way means switching, re-picking or renaming never destroys a position: every
candidate lives in the list until the user deletes it.
"""
from __future__ import annotations

import math
import re
import uuid

MAX_POINTS = 12
MAX_NAME_LENGTH = 24
DEFAULT_NAME = '选点'
SAMPLE_NAME = '本机采样样本'
PREVIOUS_SAMPLE_NAME = '上一个样本'
PICK_SOURCE = 'MAP_PICK'
SAMPLE_SOURCE = 'SAMPLE'
_CONTROL_CHARACTERS = re.compile(r'[\x00-\x1f\x7f]')

# Starter positions shipped with the program, so a fresh install already has something to choose
# from. They are ordinary points: they can be switched, renamed or deleted like any other, and
# 模拟定位 itself stays opt-in (the default location source is 真实定位). The first entry is the
# active one - the author's own captured Wi-Fi fix, which is the plainest starting position.
DEFAULT_POINTS = (
    {'name': '李园一舍', 'latitude': 29.823692963919697, 'longitude': 106.42230951951426,
     'accuracy': 141.0, 'source': SAMPLE_SOURCE},
    {'name': '杏园三舍', 'latitude': 29.827409, 'longitude': 106.422742,
     'accuracy': 100.0, 'source': PICK_SOURCE},
)


def seeded(saved_at=''):
    """The starter list: the first shipped position is active so 模拟定位 works out of the box."""
    state = empty()
    for entry in DEFAULT_POINTS:
        state = add(state, name=entry['name'], latitude=entry['latitude'], longitude=entry['longitude'],
                    accuracy=entry['accuracy'], saved_at=saved_at, source=entry['source'])
    if state['points']:
        state = activate(state, state['points'][0]['id'])
    return state


def clean_name(value, *, fallback=''):
    """Collapse whitespace, drop control characters and cap the length."""
    name = ' '.join(str(value or '').split())
    return _CONTROL_CHARACTERS.sub('', name)[:MAX_NAME_LENGTH] or fallback


def empty():
    return {'points': [], 'active': ''}


def normalize(value):
    """Keep only well-formed records; a damaged list degrades to empty instead of failing."""
    if not isinstance(value, dict):
        return empty()
    points = []
    for item in value.get('points') or []:
        if not isinstance(item, dict):
            continue
        try:
            latitude, longitude = float(item['latitude']), float(item['longitude'])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            accuracy = float(item.get('accuracy', 0.0))
        except (TypeError, ValueError):
            accuracy = 0.0
        point_id = str(item.get('id') or '')
        if (not point_id or not math.isfinite(latitude) or not math.isfinite(longitude)
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            continue
        points.append({'id': point_id,
                       'name': clean_name(item.get('name'), fallback=DEFAULT_NAME),
                       'latitude': latitude, 'longitude': longitude,
                       'accuracy': accuracy if math.isfinite(accuracy) and accuracy > 0 else 100.0,
                       'source': (SAMPLE_SOURCE if item.get('source') == SAMPLE_SOURCE else PICK_SOURCE),
                       'saved_at': str(item.get('saved_at') or '')})
    active = str(value.get('active') or '')
    if active and active not in {point['id'] for point in points}:
        active = ''
    return {'points': points[:MAX_POINTS], 'active': active}


def find(state, point_id):
    return next((point for point in normalize(state)['points'] if point['id'] == point_id), None)


def metres_apart(lat_a, lng_a, lat_b, lng_b):
    """Small-scale distance; only used to recognise "this is the spot we already hold"."""
    north = (lat_b - lat_a) * 111320.0
    east = (lng_b - lng_a) * 111320.0 * math.cos(math.radians((lat_a + lat_b) / 2))
    return math.hypot(north, east)


def holding(state, latitude, longitude, tolerance=1.0):
    """The entry already at this spot, so an adopted sample is never listed twice."""
    return next((point for point in normalize(state)['points']
                 if metres_apart(point['latitude'], point['longitude'], latitude, longitude) <= tolerance),
                None)


def suggest_name(state):
    used = {point['name'] for point in normalize(state)['points']}
    for index in range(1, MAX_POINTS + 2):
        candidate = f'{DEFAULT_NAME} {index}'
        if candidate not in used:
            return candidate
    return DEFAULT_NAME


def add(state, *, latitude, longitude, accuracy, saved_at, name='', source=PICK_SOURCE):
    """Add a point and make it active; an existing name refines that point instead of duplicating."""
    state = normalize(state)
    clean = clean_name(name) or suggest_name(state)
    existing = next((point for point in state['points'] if point['name'] == clean), None)
    if existing is not None:
        # Saving again under the same name moves that point: the list stays free of duplicates.
        existing.update(latitude=latitude, longitude=longitude, accuracy=accuracy,
                        source=source, saved_at=saved_at)
        state['active'] = existing['id']
        return state
    if len(state['points']) >= MAX_POINTS:
        raise ValueError(f'最多保存 {MAX_POINTS} 个选点，请先删除一个再保存。')
    point = {'id': uuid.uuid4().hex[:12], 'name': clean, 'latitude': latitude,
             'longitude': longitude, 'accuracy': accuracy,
             'source': SAMPLE_SOURCE if source == SAMPLE_SOURCE else PICK_SOURCE, 'saved_at': saved_at}
    state['points'].append(point)
    state['active'] = point['id']
    return state


def rename(state, point_id, name):
    state = normalize(state)
    point = find(state, point_id)
    if point is None:
        raise ValueError('找不到这个选点，请刷新后重试。')
    clean = clean_name(name)
    if not clean:
        raise ValueError(f'名称不能为空，最多 {MAX_NAME_LENGTH} 个字。')
    if any(other['name'] == clean and other['id'] != point_id for other in state['points']):
        raise ValueError('已有同名选点，请换一个名字。')
    for candidate in state['points']:
        if candidate['id'] == point_id:
            candidate['name'] = clean
    return state


def remove(state, point_id):
    state = normalize(state)
    if find(state, point_id) is None:
        raise ValueError('找不到这个选点，请刷新后重试。')
    state['points'] = [point for point in state['points'] if point['id'] != point_id]
    if state['active'] == point_id:
        state['active'] = ''
    return state


def activate(state, point_id):
    state = normalize(state)
    if find(state, point_id) is None:
        raise ValueError('找不到这个选点，请刷新后重试。')
    state['active'] = point_id
    return state
