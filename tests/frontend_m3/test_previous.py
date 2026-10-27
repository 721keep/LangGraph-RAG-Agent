"""Unmodified prior UI cases, redirected exclusively to M3's local Mock."""
import json
from pathlib import Path
import types
import unittest

first = types.ModuleType('m1_regression')
source = Path('/m1/test_render.py').read_text().replace('/m1/preview.py', '/m3/preview.py').replace('/m1-artifacts/render_result.json', '/m3-artifacts/m1_cases.json')
exec(compile(source, '/m1/test_render.py', 'exec'), first.__dict__)
names = [n for n in dir(first.Rendering) if any(n.startswith('test_' + x + '_') for x in ['01', '02', '03', '04', '06', '08', '09'])]
one = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(first.Rendering(n) for n in names))

second = types.ModuleType('m2_regression')
source = Path('/m2/test_resources.py').read_text().replace('/m2/preview.py', '/m3/preview.py').replace('m2-api:8000', 'm3-api:8000')
exec(compile(source, '/m2/test_resources.py', 'exec'), second.__dict__)
two = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(second))
Path('/m3-artifacts/previous_tests.json').write_text(json.dumps({'m1': one.testsRun, 'm2': two.testsRun,
    'passed': one.wasSuccessful() and two.wasSuccessful(), 'failed_cases': [t.id() for t, _ in one.errors + one.failures + two.errors + two.failures]}, indent=2))
raise SystemExit(0 if one.wasSuccessful() and two.wasSuccessful() else 1)
