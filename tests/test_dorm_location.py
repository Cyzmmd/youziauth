import contextlib
import importlib.util
import io
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dorm_checkin import CheckinError

PRESENT = importlib.util.find_spec('dorm_location') is not None
if PRESENT:
    from dorm_location import validate_position, wgs84_to_gcj02


def metres_between(lat_a, lng_a, lat_b, lng_b):
    """Haversine distance, independent of the module's own drift maths."""
    earth = 6_371_000.0
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(lng_b - lng_a)
    chord = (math.sin(delta_phi / 2) ** 2
             + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2)
    return 2 * earth * math.asin(math.sqrt(chord))


class LocationProviderTests(unittest.TestCase):
    def test_locate_waits_for_a_real_winrt_position(self):
        from dorm_location import locate
        raw = dict(latitude=29.82, longitude=106.42, accuracy=50, timestamp=time.time(), source='WI_FI')
        with patch('dorm_location.read_position', return_value=raw) as read:
            result = locate()
        read.assert_called_once()
        self.assertEqual(result['accuracy'], 50)

    def test_probe_never_returns_coordinates(self):
        from dorm_location import probe_location
        raw = dict(latitude=29.82, longitude=106.42, accuracy=141, timestamp=time.time(), source='WI_FI')
        with patch('dorm_location.read_position', return_value=raw):
            result = probe_location()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['accuracy'], 141)
        self.assertNotIn('latitude', result)
        self.assertNotIn('longitude', result)

    def test_denied_authorization_does_not_read_position(self):
        from dorm_location import probe_location
        with patch('dorm_location.request_permission', return_value='DENIED'), patch('dorm_location.read_position') as read:
            result = probe_location(ui_dispatch=MagicMock())
        self.assertEqual(result['state'], 'denied')
        read.assert_not_called()

    def test_no_data_is_distinct_from_denied_permission(self):
        from dorm_location import probe_location, LocationFailure
        with patch('dorm_location.read_position', side_effect=LocationFailure('no_data')):
            result = probe_location()
        self.assertEqual(result['state'], 'no_data')
        self.assertNotIn('请开启位置服务', result['message'])

    def test_coarse_or_default_position_is_not_accepted(self):
        from dorm_location import probe_location
        for change, expected in [({'accuracy':600}, 'inaccurate'), ({'source':'DEFAULT'}, 'default_position')]:
            raw = dict(latitude=29.82,longitude=106.42,accuracy=50,timestamp=time.time(),source='WI_FI')
            raw.update(change)
            with patch('dorm_location.read_position', return_value=raw):
                self.assertEqual(probe_location()['state'], expected)


class FeatureExists(unittest.TestCase):
    def test_location_exists(self):
        self.assertTrue(PRESENT, 'Location module missing')


@unittest.skipUnless(PRESENT, 'Location module missing')
class LocationTests(unittest.TestCase):
    def position(self, **kwargs):
        return dict(latitude=29.82, longitude=106.42, accuracy=50,
                    timestamp=time.time(), **kwargs)

    def test_reject_stale_or_inaccurate_or_invalid_positions(self):
        for key, value in [('timestamp', time.time()-180), ('accuracy', 10000),
                           ('latitude', float('nan')), ('longitude', 181),
                           ('accuracy', -1), ('timestamp', time.time()+120)]:
            data = self.position()
            data[key] = value
            with self.assertRaises(CheckinError):
                validate_position(data)

    def test_current_position_is_converted_without_default_address(self):
        data = validate_position(self.position())
        self.assertEqual(data['provider'], 'windows')
        self.assertEqual(data['accuracy'], 50)
        self.assertEqual(data['address'], '')
        self.assertTrue(data['isOffset'])
        self.assertGreater(data['longitude'], 106.42)

    def test_known_coordinate_conversion(self):
        lat, lng = wgs84_to_gcj02(39.908823, 116.397470)
        self.assertAlmostEqual(lat, 39.910226, places=5)
        self.assertAlmostEqual(lng, 116.403714, places=5)

    def test_outside_china_coordinates_are_unchanged(self):
        self.assertEqual(wgs84_to_gcj02(51.5, -0.1), (51.5, -0.1))


class SimulatedLocationTests(unittest.TestCase):
    def setUp(self):
        import dorm_location
        self.assertTrue(callable(getattr(dorm_location, 'simulate_location', None)),
                        'Shared location replay is not implemented')
        self.simulate = dorm_location.simulate_location
        self.sample = dict(latitude=39.908823, longitude=116.397470, accuracy=141,
                           timestamp=1700000000, source='WI_FI')
        clock = patch('time.time', return_value=1700000300)
        clock.start()
        self.addCleanup(clock.stop)
        location = patch('dorm_location.read_position', side_effect=AssertionError('Unexpected live location'))
        location.start()
        self.addCleanup(location.stop)
        network = patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access'))
        network.start()
        self.addCleanup(network.stop)

    def sample_point(self):
        """The sample's own position, converted exactly the way a replay is."""
        return wgs84_to_gcj02(self.sample['latitude'], self.sample['longitude'])

    def test_replay_converts_the_sample_without_marking_itself_for_the_school(self):
        result = self.simulate(self.sample)
        self.assertLess(metres_between(result['latitude'], result['longitude'], *self.sample_point()), 25)
        self.assertNotIn('simulation', result.values())
        self.assertAlmostEqual(result['accuracy'], 141, delta=141 * 0.15)
        self.assertGreater(result['accuracy'], 0)
        self.assertEqual(result['time'], 1700000300000)
        self.assertTrue(result['isOffset'])
        self.assertEqual(result['address'], '')

    def test_every_replay_drifts_within_a_bounded_radius(self):
        distances = []
        for _ in range(200):
            result = self.simulate(self.sample)
            distances.append(metres_between(result['latitude'], result['longitude'], *self.sample_point()))
        self.assertLessEqual(max(distances), 25.001)
        self.assertGreater(max(distances), 20)  # the drift is real, not a no-op

    def test_replays_never_repeat_the_same_numbers(self):
        keys = ('latitude', 'longitude', 'accuracy')
        seen = {json.dumps([self.simulate(self.sample)[key] for key in keys]) for _ in range(50)}
        self.assertEqual(len(seen), 50)

    def test_drift_stays_clear_of_the_acceptance_limit(self):
        for accuracy in (5, 141, 199, 200):
            with self.subTest(accuracy=accuracy):
                sample = dict(self.sample, accuracy=accuracy)
                for _ in range(50):
                    result = self.simulate(sample)
                    self.assertGreater(result['accuracy'], 0)
                    self.assertLessEqual(result['accuracy'], 200)

    def test_replay_reports_the_same_provider_as_a_live_fix(self):
        live = validate_position(dict(self.sample, timestamp=time.time(), source='WI_FI'))
        replay = self.simulate(self.sample)
        self.assertEqual(replay['provider'], live['provider'])
        self.assertEqual(set(replay), set(live))

    def test_replay_does_not_mutate_the_captured_sample(self):
        original = dict(self.sample)
        self.simulate(self.sample, latitude=51.5, longitude=-0.1, accuracy=12)
        self.assertEqual(self.sample, original)

    def test_explicit_coordinates_accuracy_and_timestamp_are_used(self):
        result = self.simulate(self.sample, latitude=0, longitude=0,
                               accuracy=12.5, timestamp=1700000299.75)
        self.assertEqual(result['latitude'], 0)
        self.assertEqual(result['longitude'], 0)
        self.assertEqual(result['accuracy'], 12.5)
        self.assertEqual(result['time'], 1700000299750)

    def test_invalid_or_stale_overrides_keep_existing_quality_checks(self):
        for change in ({'latitude': 91}, {'longitude': float('nan')},
                       {'accuracy': 201}, {'accuracy': 0}, {'timestamp': 1700000000}):
            with self.subTest(change=change), self.assertRaises(CheckinError):
                self.simulate(self.sample, **change)

    def test_replay_does_not_relabel_coarse_sources_as_wifi(self):
        for source in ('DEFAULT', 'IP_ADDRESS', 'OBFUSCATED'):
            with self.subTest(source=source), self.assertRaises(CheckinError):
                self.simulate(dict(self.sample, source=source))


class SimulatedLocationCliTests(unittest.TestCase):
    def setUp(self):
        import dorm_selftest
        self.assertTrue(callable(getattr(dorm_selftest, 'location_main', None)),
                        'Local location replay command is not implemented')
        self.main = dorm_selftest.location_main
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sample_path = Path(directory.name) / 'sample.json'
        self.sample_path.write_text(json.dumps(dict(latitude=39.908823, longitude=116.397470,
                                                    accuracy=141, timestamp=1700000000,
                                                    source='WI_FI')), encoding='utf-8')
        for guard in (patch('time.time', return_value=1700000300),
                      patch('dorm_location.read_position', side_effect=AssertionError('Unexpected live location')),
                      patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access'))):
            guard.start()
            self.addCleanup(guard.stop)

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.main([str(self.sample_path), *args])
        return code, json.loads(output.getvalue())

    def test_cli_replays_local_sample_without_changing_it(self):
        original = self.sample_path.read_bytes()
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(result['ok'])
        self.assertEqual(result['scope'], 'local-only simulation')
        self.assertEqual(result['captured_at'], 1700000000)
        self.assertEqual(result['source'], 'WI_FI')
        self.assertEqual(result['position']['provider'], validate_position(
            dict(latitude=39.908823, longitude=116.397470, accuracy=141,
                 timestamp=time.time(), source='WI_FI'))['provider'])
        self.assertNotIn('simulation', result['position'].values())
        self.assertLess(metres_between(result['position']['latitude'], result['position']['longitude'],
                                       *wgs84_to_gcj02(39.908823, 116.397470)), 25)
        self.assertEqual(result['position']['time'], 1700000300000)
        self.assertEqual(self.sample_path.read_bytes(), original)

    def test_cli_replays_differ_between_runs(self):
        _, first = self.invoke()
        _, second = self.invoke()
        self.assertNotEqual(first['position'], second['position'])

    def test_cli_supports_overrides(self):
        code, result = self.invoke('--latitude', '0', '--longitude', '0',
                                   '--accuracy', '25', '--timestamp', '1700000299')
        self.assertEqual(code, 0)
        self.assertEqual(result['position']['latitude'], 0)
        self.assertEqual(result['position']['longitude'], 0)
        self.assertEqual(result['position']['accuracy'], 25)
        self.assertEqual(result['position']['time'], 1700000299000)

    def test_cli_rejects_missing_or_malformed_samples(self):
        self.sample_path.unlink()
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertFalse(result['ok'])
        for content in ('{', 'null', '[]', '{}'):
            with self.subTest(content=content):
                self.sample_path.write_text(content, encoding='utf-8')
                code, result = self.invoke()
                self.assertEqual(code, 2)
                self.assertFalse(result['ok'])

    def test_cli_rejects_invalid_original_capture_timestamps(self):
        sample = json.loads(self.sample_path.read_text(encoding='utf-8'))
        for timestamp in (None, 'invalid', float('nan'), float('inf')):
            with self.subTest(timestamp=timestamp):
                sample['timestamp'] = timestamp
                self.sample_path.write_text(json.dumps(sample), encoding='utf-8')
                code, result = self.invoke()
                self.assertEqual(code, 2)
                self.assertFalse(result['ok'])
                self.assertNotIn('position', result)

    def test_cli_reports_quality_failure_without_position(self):
        code, result = self.invoke('--accuracy', '600')
        self.assertEqual(code, 2)
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'inaccurate')
        self.assertNotIn('position', result)


if __name__ == '__main__':
    unittest.main()
