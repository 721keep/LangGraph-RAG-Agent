"""Run independent fixtures in separate processes, never read a local .env."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / '.env').exists():
    raise SystemExit('Export the reviewed Git index first; .env must not be mounted.')

def run(paths, env):
    subprocess.run([sys.executable, '-B', '-m', 'pytest', *paths,
                    '-q', '-p', 'no:cacheprovider', '--tb=short'], cwd=ROOT,
                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **env}, check=True)

run(['tests/test_upload_reliability.py', 'tests/test_model_configuration.py',
     'tests/release/test_release_safety.py', 'tests/release/test_manage.py',
     'tests/release/test_empty_thread.py', 'tests/release/test_r2_guard.py'],
    {'RELEASE_R2_TESTS': '1', 'PYTHONPATH': str(ROOT / 'deployment/release/runtime')})
run(['tests/stage3/test_guard.py'], {'PYTHONPATH': str(ROOT / 'tests/stage3')})
run(['tests/stage3/agent/test_contract.py', 'tests/stage3/agent/test_regressions.py'],
    {'STAGE3D_MODE': 'offline', 'PYTHONPATH': os.pathsep.join(
        str(ROOT / p) for p in ['tests/stage3', 'tests/stage3/agent'])})
