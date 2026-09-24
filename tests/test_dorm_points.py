import importlib.util
import unittest
from unittest.mock import Mock

PRESENT = importlib.util.find_spec('dorm_points') is not None
if PRESENT:
    import dorm_points

    from dorm_checkin import Store
    from pathlib import Path
    import tempfile


@unittest.skipUnless(PRESENT, 'dorm_points is not implemented')
class NamedPointTests(unittest.TestCase):
    def sample(self, latitude=29.823693, longitude=106.422310, accuracy=100.0):
        return dict(latitude=latitude, longitude=longitude, accuracy=accuracy)

    def add(self, state, name='', **overrides):
        sample = self.sample(**overrides)
        return dorm_points.add(state, name=name, latitude=sample['latitude'],
                               longitude=sample['longitude'], accuracy=sample['accuracy'],
                               saved_at='2026-09-24T09:40:00+08:00')

    def test_a_new_point_becomes_the_active_one(self):
        state = self.add(dorm_points.empty(), '宿舍楼下')
        self.assertEqual(len(state['points']), 1)
        point = state['points'][0]
        self.assertEqual(point['name'], '宿舍楼下')
        self.assertEqual(state['active'], point['id'])
        self.assertAlmostEqual(point['latitude'], 29.823693, places=6)

    def test_saving_the_same_name_again_refines_that_point(self):
        state = self.add(dorm_points.empty(), '宿舍楼下')
        first_id = state['points'][0]['id']
        state = self.add(state, '宿舍楼下', latitude=29.824000, longitude=106.423000)
        self.assertEqual(len(state['points']), 1, 'the same name must not duplicate a point')
        self.assertEqual(state['points'][0]['id'], first_id)
        self.assertAlmostEqual(state['points'][0]['latitude'], 29.824000, places=6)
        self.assertEqual(state['active'], first_id)

    def test_an_empty_name_is_generated_and_stays_unique(self):
        state = self.add(dorm_points.empty())
        state = self.add(state)
        self.assertEqual([point['name'] for point in state['points']], ['选点 1', '选点 2'])

    def test_names_are_cleaned_and_capped(self):
        state = self.add(dorm_points.empty(), '  宿舍   楼下\u0007  ')
        self.assertEqual(state['points'][0]['name'], '宿舍 楼下')
        long_name = '长' * 40
        state = self.add(state, long_name)
        self.assertEqual(len(state['points'][1]['name']), dorm_points.MAX_NAME_LENGTH)

    def test_the_list_is_capped(self):
        state = dorm_points.empty()
        for index in range(dorm_points.MAX_POINTS):
            state = self.add(state, f'选点 {index}')
        with self.assertRaises(ValueError):
            self.add(state, '再来一个')
        self.assertEqual(len(state['points']), dorm_points.MAX_POINTS)

    def test_renaming_rejects_empty_and_duplicate_names(self):
        state = self.add(dorm_points.empty(), '宿舍楼下')
        state = self.add(state, '图书馆')
        target = state['points'][0]['id']
        for name in ('', '   ', '\u0007'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dorm_points.rename(state, target, name)
        with self.assertRaises(ValueError):
            dorm_points.rename(state, target, '图书馆')
        renamed = dorm_points.rename(state, target, '宿舍东门')
        self.assertEqual(dorm_points.find(renamed, target)['name'], '宿舍东门')

    def test_unknown_points_are_refused(self):
        state = self.add(dorm_points.empty(), '宿舍楼下')
        for operation in (lambda: dorm_points.rename(state, 'missing', '新名字'),
                          lambda: dorm_points.remove(state, 'missing'),
                          lambda: dorm_points.activate(state, 'missing')):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()

    def test_removing_the_active_point_clears_the_active_marker(self):
        state = self.add(dorm_points.empty(), '宿舍楼下')
        state = self.add(state, '图书馆')
        active = state['active']
        state = dorm_points.remove(state, active)
        self.assertEqual([point['name'] for point in state['points']], ['宿舍楼下'])
        self.assertEqual(state['active'], '')
        state = dorm_points.activate(state, state['points'][0]['id'])
        self.assertEqual(state['points'][0]['id'], state['active'])

    def test_a_point_records_where_it_came_from(self):
        state = self.add(dorm_points.empty(), '地图点')
        self.assertEqual(state['points'][0]['source'], dorm_points.PICK_SOURCE)
        state = dorm_points.add(state, name='采样点', latitude=29.8, longitude=106.4, accuracy=141.0,
                                saved_at='2026-09-24T10:00:00+08:00', source=dorm_points.SAMPLE_SOURCE)
        self.assertEqual(state['points'][1]['source'], dorm_points.SAMPLE_SOURCE)
        self.assertEqual(dorm_points.normalize({'points': [dict(state['points'][0], source='weird')]})
                         ['points'][0]['source'], dorm_points.PICK_SOURCE)

    def test_holding_recognises_the_spot_we_already_have(self):
        state = self.add(dorm_points.empty(), '李园一舍', latitude=29.827439, longitude=106.422612)
        self.assertIsNotNone(dorm_points.holding(state, 29.827439, 106.422612))
        self.assertIsNotNone(dorm_points.holding(state, 29.8274395, 106.4226125),
                             'a metre of slack is the same spot')
        self.assertIsNone(dorm_points.holding(state, 29.823693, 106.422310))
        self.assertIsNone(dorm_points.holding(dorm_points.empty(), 29.827439, 106.422612))

    def test_normalize_drops_damaged_records(self):
        value = {'active': 'keep', 'points': [
            {'id': 'keep', 'name': '好的', 'latitude': '29.823693', 'longitude': 106.422310, 'accuracy': 100},
            {'id': 'bad-lat', 'name': 'x', 'latitude': 'north', 'longitude': 106.4},
            {'id': 'out-of-range', 'name': 'x', 'latitude': 91, 'longitude': 106.4},
            {'id': '', 'name': 'x', 'latitude': 29.8, 'longitude': 106.4},
            'not-a-dict',
            {'id': 'nan', 'name': 'x', 'latitude': float('nan'), 'longitude': 106.4},
            {'id': 'no-accuracy', 'name': '默认精度', 'latitude': 29.8, 'longitude': 106.4, 'accuracy': 'x'},
        ]}
        state = dorm_points.normalize(value)
        self.assertEqual([point['id'] for point in state['points']], ['keep', 'no-accuracy'])
        self.assertEqual(state['points'][1]['accuracy'], 100.0)
        self.assertEqual(state['active'], 'keep')

    def test_normalize_forgets_an_active_id_that_is_gone(self):
        state = dorm_points.normalize({'active': 'ghost', 'points': []})
        self.assertEqual(state, {'points': [], 'active': ''})

    def test_normalize_survives_garbage(self):
        for value in (None, [], 'points', 42):
            with self.subTest(value=value):
                self.assertEqual(dorm_points.normalize(value), {'points': [], 'active': ''})

    def test_store_round_trip_and_damaged_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), protector=Mock(protect=lambda b: b[::-1],
                                                           unprotect=lambda b: b[::-1]))
            self.assertEqual(store.points(), {'points': [], 'active': ''})
            state = self.add(dorm_points.empty(), '宿舍楼下')
            store.save_points(state)
            self.assertEqual(store.points()['points'][0]['name'], '宿舍楼下')
            (store.root / 'location-points.json').write_text('{', encoding='utf-8')
            self.assertEqual(store.points(), {'points': [], 'active': ''},
                             'a damaged list must not break the picker')


if __name__ == '__main__':
    unittest.main()
