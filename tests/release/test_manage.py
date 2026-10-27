"""Release CLI regression: never regenerate secrets or recapture live test runtimes."""
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[2] / 'deployment/release/manage.py'
spec = importlib.util.spec_from_file_location('release_manage_test', path)
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


def test_existing_private_config_is_never_overwritten(monkeypatch, tmp_path):
    monkeypatch.setattr(manage, 'PRIVATE', tmp_path)
    file = tmp_path / 'runtime.json'
    file.write_text('offline fixture; not a real credential')
    before = file.read_bytes()
    with pytest.raises(RuntimeError, match='refusing regeneration'):
        manage.prepare()
    assert file.read_bytes() == before


def test_fresh_machine_constraints_do_not_require_m4(monkeypatch, tmp_path):
    monkeypatch.setattr(manage, 'HERE', tmp_path)
    for name in ['backend.constraints.txt', 'frontend.constraints.txt']:
        (tmp_path / name).write_text('# fixture\nknown-package==1.2.3\n')
    monkeypatch.setattr(manage.subprocess, 'check_output', lambda *a, **kw: pytest.fail('Constraints check must not inspect M4'))
    manage.check_constraints()


@pytest.mark.parametrize('content', ['', 'package>=1.0'])
def test_unpinned_constraints_are_rejected(monkeypatch, tmp_path, content):
    monkeypatch.setattr(manage, 'HERE', tmp_path)
    (tmp_path / 'backend.constraints.txt').write_text(content)
    (tmp_path / 'frontend.constraints.txt').write_text('package==1.0\n')
    with pytest.raises(RuntimeError):
        manage.check_constraints()
