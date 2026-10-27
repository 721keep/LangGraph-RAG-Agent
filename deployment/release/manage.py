"""R1 lifecycle: no deletion, secret output, model requests or old database access."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIVATE = HERE / '.private'
ART = HERE / 'artifacts'
COMPOSE = ['docker', 'compose', '--project-name', 'rag-release-r1', '-f', str(HERE / 'compose.yaml')]
DB_CONTAINER = 'rag_release_r1_postgres'
VOLUME = 'rag_release_r1_pgdata_v1'


def docker_json(*args):
    return json.loads(subprocess.check_output(['docker', *args], text=True))


def check_constraints():
    for name in ['backend.constraints.txt', 'frontend.constraints.txt']:
        path = HERE / name
        if not path.is_file():
            raise RuntimeError('Reviewed release constraints missing')
        lines = [s for s in path.read_text().splitlines() if s.strip() and not s.startswith('#')]
        if not lines or any(len(s.split('==')) != 2 for s in lines):
            raise RuntimeError('Release constraints must contain exact version pins')


def prepare():
    if (PRIVATE / 'runtime.json').exists():
        raise RuntimeError('R1 private configuration exists; refusing regeneration or JWT rotation')
    check_constraints()
    for name in [DB_CONTAINER, 'rag_release_r1_backend', 'rag_release_r1_frontend']:
        found = subprocess.run(['docker', 'container', 'inspect', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if found.returncode == 0:
            raise RuntimeError('R1 name already exists before preparation')
    if subprocess.run(['docker', 'volume', 'inspect', VOLUME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('R1 Volume already exists; refusing to adopt existing data')
    PRIVATE.mkdir(exist_ok=True)
    ART.mkdir(exist_ok=True)
    if subprocess.run(['git', 'check-ignore', '-q', str(PRIVATE / 'runtime.json')], cwd=ROOT).returncode:
        raise RuntimeError('Private runtime is not Git ignored')
    sys.path.append('/mnt/e/anaconda3/Lib/site-packages')
    from dotenv import dotenv_values
    config = dotenv_values(ROOT / '.env', interpolate=False)
    names = ['CHAT_API_KEY', 'EMBEDDINGS_API_KEY', 'RERANK_API_KEY', 'TAVILY_API_KEY']
    if any(not config.get(n) or len(config[n]) < 12 for n in names):
        raise RuntimeError('Required local model/search credential missing; no private configuration created')
    runtime = {n: config[n] for n in names}
    runtime.update(POSTGRES_HOST='r1-postgres', POSTGRES_PORT='5432', POSTGRES_USER='r1_rag_user',
                   POSTGRES_PASSWORD=secrets.token_hex(32), POSTGRES_DATABASE='langgraph_rag_bge_prod',
                   PGVECTOR_COLLECTION_NAME='bge_m3_1024_v1', JWT_SECRET=secrets.token_urlsafe(64),
                   MODEL_PROVIDER='openai', MODEL_NAMES='["deepseek-flash"]', MODEL_BASE_URL='https://api.deepseek.com',
                   EMBEDDINGS_PROVIDER='openai', EMBEDDINGS_MODEL_NAME='BAAI/bge-m3', EMBEDDINGS_BASE_URL='https://api.siliconflow.cn/v1',
                   RERANK_MODEL_NAME='BAAI/bge-reranker-v2-m3', RERANK_BASE_URL='https://api.siliconflow.cn/v1', RERANK_PATH='/rerank',
                   DASHSCOPE_API_KEY='', TOKEN_BEARER_URL='/api/v1/auth/login', JWT_ALGORITHM='HS256',
                   ACCESS_TOKEN_EXPIRY_MINS='240', REFRESH_TOKEN_EXPIRY_DAYS='1', APP_TIMEZONE='Asia/Shanghai',
                   LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    assert runtime['JWT_SECRET'] != config.get('JWT_SECRET')
    for name, body in [('runtime.json', json.dumps(runtime)), ('db_password', runtime['POSTGRES_PASSWORD']),
                       ('database_identity.json', json.dumps({'attested': False}))]:
        path = PRIVATE / name
        with path.open('x') as file:
            file.write(body)
        path.chmod(0o600)
    PRIVATE.chmod(0o700)
    paths = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    baseline = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in set(paths)
                if n and not n.startswith(('deployment/release/', 'tests/release/')) and (ROOT / n).is_file()}
    baseline['.env'] = hashlib.sha256((ROOT / '.env').read_bytes()).hexdigest()
    (ART / 'baseline.json').write_text(json.dumps(baseline, indent=2))
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    before = docker_json('inspect', *ids) if ids else []
    safe = [{'name': c['Name'].lstrip('/'), 'id': c['Id'], 'image': c['Image'],
             'status': c['State']['Status'], 'networks': list(c['NetworkSettings']['Networks']),
             'volumes': [m.get('Name') for m in c['Mounts'] if m['Type'] == 'volume']} for c in before]
    (ART / 'existing_containers.json').write_text(json.dumps(safe, indent=2))
    print('R1 private configuration and new JWT created using reviewed release constraints. No secret values printed.')


def attest():
    runtime = json.loads((PRIVATE / 'runtime.json').read_text())
    container = docker_json('inspect', DB_CONTAINER)[0]
    labels = container['Config'].get('Labels', {})
    if labels.get('com.docker.compose.project') != 'rag-release-r1':
        raise RuntimeError('R1 database container owner mismatch')
    mounts = [m for m in container['Mounts'] if m['Destination'] == '/var/lib/postgresql/data']
    if len(mounts) != 1 or mounts[0]['Type'] != 'volume' or mounts[0]['Name'] != VOLUME:
        raise RuntimeError('Database Volume isolation failed')
    volume = docker_json('volume', 'inspect', VOLUME)[0]
    if volume['Labels'].get('org.langgraph.release') != 'r1-bge-v1' or volume['Labels'].get('com.docker.compose.project') != 'rag-release-r1':
        raise RuntimeError('R1 Volume owner mismatch')
    if container['HostConfig']['PortBindings'] or set(container['NetworkSettings']['Networks']) != {'rag_release_r1_database'}:
        raise RuntimeError('Database network/port isolation failed')
    query = "BEGIN READ ONLY; SELECT json_build_object('database',current_database(),'user',current_user,'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),'business_tables',(SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public')); COMMIT;"
    output = subprocess.check_output(['docker', 'exec', DB_CONTAINER, 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'r1_rag_user', '-d', 'langgraph_rag_bge_prod', '-c', query], text=True)
    actual = json.loads(output.strip())
    if actual['database'] != runtime['POSTGRES_DATABASE'] or actual['user'] != runtime['POSTGRES_USER']:
        raise RuntimeError('Actual database target mismatch')
    path = PRIVATE / 'database_identity.json'
    old = json.loads(path.read_text())
    if old.get('attested') and old['system_identifier'] != actual['system_identifier']:
        raise RuntimeError('Database system identity changed; refusing to overwrite attestation')
    if not old.get('attested') and actual['business_tables'] != 0:
        raise RuntimeError('First attestation requires a completely empty new database')
    identity = {'attested': True, 'system_identifier': actual['system_identifier'], 'volume': VOLUME,
                'database': actual['database'], 'user': actual['user']}
    path.write_text(json.dumps(identity))
    first_proof = ART / 'database_preinit.json'
    if not first_proof.exists():
        first_proof.write_text(json.dumps({**identity, 'tables_before_first_initialization': actual['business_tables']}, indent=2))
    (ART / 'database_latest_attestation.json').write_text(json.dumps({**identity, 'existing_public_tables': actual['business_tables']}, indent=2))
    print('R1 actual database and new Volume attested. Initializers may now run only against this database identity.')


def up(recreate=False, r2=False):
    subprocess.run(COMPOSE + ['config', '--quiet'], check=True)
    topology = json.loads(subprocess.check_output(COMPOSE + ['config', '--format', 'json'], text=True))
    pg = topology['services']['r1-postgres']
    if (topology['name'] != 'rag-release-r1' or topology['volumes']['r1-data']['name'] != VOLUME
            or pg.get('ports') or pg['container_name'] != DB_CONTAINER
            or set(pg['networks']) != {'database'}
            or not topology['networks']['database']['internal']
            or not topology['networks']['application']['internal']
            or topology['services']['backend'].get('ports')
            or set(topology['services']['backend']['networks']) != ({'database', 'application', 'egress'} if r2 else {'database', 'application'})):
        raise RuntimeError('Pre-initialization deployment topology mismatch')
    if r2 and (topology['services']['backend']['environment'].get('RELEASE_MODEL_MODE') != 'bounded'
               or topology['services']['backend']['image'] != 'langgraph-rag-release-backend:20261027-r2'
               or topology['networks']['egress']['name'] != 'rag_release_r2_egress'):
        raise RuntimeError('R2 explicit bounded deployment mismatch')
    mount = next(m for m in pg['volumes'] if m['target'] == '/var/lib/postgresql/data')
    if mount['type'] != 'volume' or mount['source'] != 'r1-data':
        raise RuntimeError('Pre-initialization PostgreSQL volume target mismatch')
    for service in ['backend', 'frontend']:
        if any(p.get('host_ip') != '127.0.0.1' for p in topology['services'][service].get('ports', [])):
            raise RuntimeError('Public host port forbidden in R1')
    for service in ['r1-postgres', 'backend', 'frontend']:
        name = topology['services'][service]['container_name']
        probe = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True)
        if probe.returncode == 0:
            existing = json.loads(probe.stdout)[0]
            if existing['Config'].get('Labels', {}).get('com.docker.compose.project') != 'rag-release-r1':
                raise RuntimeError('Refusing to replace a container from another project')
    print('R1 target database/Volume, project owner, internal backend networks and loopback ports verified.', flush=True)
    if recreate:
        subprocess.run(COMPOSE + ['stop', 'frontend', 'backend', 'r1-postgres'], check=True)
    flags = ['--force-recreate'] if recreate else []
    # Database only first: no backend DDL before this Volume/identity check.
    subprocess.run(COMPOSE + ['up', '-d', '--wait', *flags, 'r1-postgres'], check=True)
    attest()
    subprocess.run(COMPOSE + ['up', '-d', '--wait', *flags, 'backend', 'frontend'], check=True)


if __name__ == '__main__':
    try:
        action = sys.argv[1]
        if action == 'prepare':
            prepare()
        elif action == 'attest':
            attest()
        elif action == 'up':
            up()
        elif action == 'recreate':
            up(recreate=True)
        elif action in ('build-r2', 'up-r2', 'restart-r2'):
            COMPOSE += ['-f', str(HERE / 'compose.r2.yaml')]
            if action == 'build-r2':
                subprocess.run(COMPOSE + ['build', 'backend', 'frontend', 'checks'], check=True)
            else:
                if action == 'restart-r2':
                    subprocess.run(COMPOSE + ['stop', 'frontend', 'backend', 'r1-postgres'], check=True)
                up(r2=True)
        elif action in ('arm-r2', 'seal-r2'):
            operation = action.split('-')[0]
            code = """import json,sys,urllib.request
from pathlib import Path
request=urllib.request.Request('http://127.0.0.1:8000/release/r2/control',data=json.dumps({'action':sys.argv[1]}).encode(),headers={'Content-Type':'application/json','X-R2-Control':Path('/run/secrets/r2_control').read_text().strip()})
with urllib.request.urlopen(request,timeout=10) as response: result=json.load(response)
print(json.dumps({k:result[k] for k in ['mode','requests','counts']}))
"""
            process = subprocess.run(['docker','exec','rag_release_r1_backend','python','-B','-c',code,operation],
                                     capture_output=True,text=True)
            if process.returncode:
                raise RuntimeError('R2 control rejected; never reset a sealed/halted ledger')
            print(process.stdout.strip())
        elif action in ('build', 'stop', 'ps'):
            extra = ['backend', 'frontend', 'checks'] if action == 'build' else []
            subprocess.run(COMPOSE + [action, *extra], check=True)
        elif action == 'logs':
            subprocess.run(COMPOSE + ['logs', '--tail', '100', 'backend', 'frontend'], check=True)
        else:
            raise RuntimeError('Use prepare, attest, build, up, recreate, stop, ps or logs')
    except Exception as error:
        # Never include exception text/locals that may contain credentials.
        print('R1 operation stopped: ' + type(error).__name__ + '; inspect the non-secret configuration/health checks.')
        raise SystemExit(1) from None
