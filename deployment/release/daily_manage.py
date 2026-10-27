"""Explicit daily lifecycle; never reads or writes an acceptance budget."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import manage

HERE, PRIVATE = manage.HERE, manage.PRIVATE
COMPOSE = manage.COMPOSE + ['-f', str(HERE / 'compose.daily.yaml')]
VOLUME = 'rag_release_daily_usage_v1'
IMAGE = 'langgraph-rag-release-backend:20261027-daily'


def validate_topology(topology):
    backend = topology['services']['backend']
    if (backend['environment'].get('RELEASE_MODEL_MODE') != 'daily'
            or backend['environment'].get('RELEASE_PHASE') != 'DAILY'
            or backend['image'] != IMAGE
            or topology['networks']['daily-egress']['name'] != 'rag_release_daily_models'
            or topology['volumes']['daily-data']['name'] != VOLUME
            or any(s['source'].startswith('r2_') for s in backend.get('secrets', []))):
        raise RuntimeError('Daily deployment topology rejected')
    mounts = {m['target']:m for m in backend.get('volumes', [])}
    if mounts.get('/run/daily', {}).get('source') != 'daily-data' or '/run/r2' in mounts:
        raise RuntimeError('Daily ledger isolation rejected')


def prepare(source=None):
    if not (PRIVATE / 'runtime.json').is_file() or not (PRIVATE / 'database_identity.json').is_file():
        raise RuntimeError('Prepare the isolated base release first')
    path = PRIVATE / 'daily_policy.json'
    if path.exists():
        raise RuntimeError('Daily configuration exists; refusing replacement')
    source = Path(source) if source else PRIVATE / 'r2-tokenizer'
    manifest = json.loads((source / 'manifest.json').read_text())
    for name, item in manifest['files'].items():
        file = source / name
        if not file.resolve().is_relative_to(source.resolve()) or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Reviewed tokenizer missing or integrity mismatch')
    target = PRIVATE / 'daily-tokenizer'
    if target.exists():
        raise RuntimeError('Daily tokenizer already exists; inspect incomplete preparation')
    shutil.copytree(source, target)
    limits = json.loads((HERE / 'daily.example.json').read_text())
    limits['ledger_id'] = str(uuid.uuid4())
    with path.open('x') as file:
        json.dump(limits, file, indent=2)
    path.chmod(0o600)
    print('Independent daily policy/tokenizer prepared. No acceptance ledger or credential changed.')


def ledger(operation='status'):
    values = json.loads((PRIVATE / 'daily_policy.json').read_text())
    args = ['docker','run','-i','--network','none','--read-only','--tmpfs','/tmp',
            '--mount','type=volume,src='+VOLUME+',dst=/run/daily', '--entrypoint','python', IMAGE,
            '-B','/release/daily_init.py',operation]
    if operation == 'resume':
        args += ['--acknowledge']
    process = subprocess.run(args, input=json.dumps(values), text=True, capture_output=True)
    if process.returncode:
        raise RuntimeError('Daily ledger check failed; never regenerate or reset it')
    return json.loads(process.stdout)


def storage():
    query = subprocess.run(['docker','volume','inspect',VOLUME],capture_output=True,text=True)
    if query.returncode:
        # Marker prevents silent counter reset if a previously initialized volume vanished.
        if (PRIVATE / 'daily_storage.json').exists():
            raise RuntimeError('Daily volume missing; restore backup, do not reset usage')
        subprocess.run(['docker','volume','create','--label','org.langgraph.daily=usage-v1',
                        '--label','com.docker.compose.project=rag-release-r1',
                        '--label','com.docker.compose.volume=daily-data',VOLUME],check=True,stdout=subprocess.DEVNULL)
        result = ledger('init')
        (PRIVATE / 'daily_storage.json').write_text(json.dumps({'volume':VOLUME,
            'ledger_id':json.loads((PRIVATE/'daily_policy.json').read_text())['ledger_id']}))
    else:
        labels = json.loads(query.stdout)[0].get('Labels',{})
        if labels.get('org.langgraph.daily') != 'usage-v1' or labels.get('com.docker.compose.project') != 'rag-release-r1':
            raise RuntimeError('Refusing unknown daily usage volume')
        if (PRIVATE / 'daily_storage.json').exists():
            result = ledger()
        else:
            # Recover only an interrupted *first* preparation; init uses an
            # exclusive create and can never replace an existing ledger file.
            result = ledger('init')
            (PRIVATE / 'daily_storage.json').write_text(json.dumps({'volume':VOLUME,
                'ledger_id':json.loads((PRIVATE/'daily_policy.json').read_text())['ledger_id']}))
    return result


def main(action, args=()):
    if action == 'prepare-daily':
        prepare(args[0] if args else None)
    elif action == 'build-daily':
        subprocess.run(COMPOSE + ['build','backend','frontend','checks'],check=True)
    elif action in ('up-daily','restart-daily'):
        state = storage()
        if state['paused']:
            raise RuntimeError('Daily ledger paused; inspect daily-status before explicit resume-daily')
        # Reuse every database/volume/instance/port check, changing only the model profile.
        manage.COMPOSE = COMPOSE
        manage.up(recreate=action=='restart-daily', daily=True)
    elif action == 'daily-status':
        # Read the live process without constructing a second Ledger that could
        # interpret active HTTP requests as a crashed operation.
        code = "from daily_guard import Ledger; import json; from pathlib import Path; l=object.__new__(Ledger); l.path=Path('/run/daily/usage.sqlite3'); l.limits=json.loads(Path('/run/secrets/daily_policy').read_text()); from datetime import datetime,timezone; l.clock=lambda:datetime.now(timezone.utc).date().isoformat(); print(json.dumps(l.status()))"
        subprocess.run(['docker','exec','rag_release_r1_backend','python','-B','-c',code],check=True)
    elif action == 'resume-daily':
        if list(args) != ['--acknowledge']:
            raise RuntimeError('Use resume-daily --acknowledge only after reviewing failed/uncertain requests')
        # Only resume while stopped: changing unresolved requests mid-flight is unsafe.
        probe = manage.docker_json('inspect','rag_release_r1_backend')[0]
        if probe['State']['Running']:
            raise RuntimeError('Stop backend/frontend first; resume never repeats a model request')
        print(json.dumps(ledger('resume')))
