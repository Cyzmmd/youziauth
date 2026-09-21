import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from campus_auth_desktop import main


class LocationDiagnosticEntryTests(unittest.TestCase):
    def test_diagnostic_entry_never_starts_school_or_desktop_controller(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'result.json'
            summary = {'state':'ready','message':'ok','accuracy':141,'checked':'22:00:00'}
            with patch('dorm_location.probe_location',return_value=summary), patch('campus_auth_desktop.DesktopBridge') as controller:
                self.assertEqual(main(['--location-self-test',str(output)]),0)
                self.assertEqual(json.loads(output.read_text(encoding='utf-8')),summary)
                controller.assert_not_called()
