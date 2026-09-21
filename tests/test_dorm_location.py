import importlib.util
import time
import unittest
from unittest.mock import patch, MagicMock
from dorm_checkin import CheckinError

PRESENT = importlib.util.find_spec('dorm_location') is not None
if PRESENT:
    from dorm_location import validate_position, wgs84_to_gcj02


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


if __name__ == '__main__':
    unittest.main()
