"""Portable release preparation: no Docker, private assets, credentials or models."""
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'deployment/release'))
import daily_manage


def test_published_manifest_is_pinned_public_metadata():
    value=json.loads((ROOT/'deployment/release/tokenizer.manifest.json').read_text())
    assert set(value)=={'repo','revision','files'}
    assert re.fullmatch(r'deepseek-ai/[A-Za-z0-9_.-]+',value['repo'])
    assert re.fullmatch(r'[0-9a-f]{40}',value['revision'])
    assert {'tokenizer.json','encoding/encoding.py','LICENSE'}.issubset(value['files'])
    for name,meta in value['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert set(meta)=={'bytes','sha256'} and type(meta['bytes']) is int and meta['bytes']>0
        assert re.fullmatch(r'[0-9a-f]{64}',meta['sha256'])


def fresh(tmp_path,monkeypatch,valid=True):
    private=tmp_path/'private'
    private.mkdir()
    (private/'runtime.json').write_text('{"fixture": "not credentials"}')
    (private/'database_identity.json').write_text('{"attested":false}')
    source=tmp_path/'verified-public-source'
    source.mkdir()
    content=b'fictional tokenizer for filesystem preparation test only'
    (source/'tokenizer.json').write_bytes(content)
    digest=hashlib.sha256(content).hexdigest() if valid else '0'*64
    manifest={'files':{'tokenizer.json':{'bytes':len(content),'sha256':digest}}}
    (source/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(daily_manage,'PRIVATE',private)
    monkeypatch.setattr(daily_manage,'HERE',ROOT/'deployment/release')
    monkeypatch.setattr(daily_manage.subprocess,'run',lambda *a,**kw:pytest.fail('Preparation must not require historical containers'))
    return private,source


def test_fresh_explicit_source_needs_no_historical_test_container(tmp_path,monkeypatch):
    private,source=fresh(tmp_path,monkeypatch)
    assert not (private/'r2-tokenizer').exists()
    daily_manage.prepare(source)
    policy=json.loads((private/'daily_policy.json').read_text())
    assert policy['requests_per_day']['chat']==300
    assert (private/'daily-tokenizer/tokenizer.json').read_bytes()==(source/'tokenizer.json').read_bytes()
    before=(private/'daily_policy.json').read_bytes()
    with pytest.raises(RuntimeError,match='refusing replacement'):daily_manage.prepare(source)
    assert (private/'daily_policy.json').read_bytes()==before


def test_invalid_source_hash_does_not_publish_runtime_configuration(tmp_path,monkeypatch):
    private,source=fresh(tmp_path,monkeypatch,valid=False)
    with pytest.raises(RuntimeError,match='integrity mismatch'):daily_manage.prepare(source)
    assert not (private/'daily_policy.json').exists()
    assert not (private/'daily-tokenizer').exists()
