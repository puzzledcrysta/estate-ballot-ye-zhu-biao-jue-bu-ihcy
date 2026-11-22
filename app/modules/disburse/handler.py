import json
from hashlib import sha256
from fastapi import APIRouter, Response
from app.errors import DataError
from app.inputs import Disburse
from app.modules.runs.repository import lookup
from app.store import connection
from .readers import read_contracts, read_estates, read_balances
from .service import build_report, fingerprint
from . import repository

router = APIRouter()


def _load(model: Disburse):
    run_id = model.run_id
    with connection() as db:
        run = json.loads(lookup(db, run_id)['result'])
    contract_blob, estate_blob, balance_blob = (model.file('contracts'),
                                                 model.file('estates'), model.file('balances'))
    contracts = read_contracts(contract_blob)
    estates = read_estates(estate_blob)
    balances = read_balances(balance_blob)
    report = build_report(run, contracts, estates, balances)
    hashes = {'contract': sha256(contract_blob).hexdigest(),
              'estate': sha256(estate_blob).hexdigest(),
              'balance': sha256(balance_blob).hexdigest()}
    stamp = fingerprint(run, contracts, estates, balances)
    source_blob = json.dumps({'run_id': run_id, 'hashes': hashes, 'fingerprint': stamp},
                             ensure_ascii=False).encode()
    return stamp, run_id, hashes, report, source_blob


@router.post('/api/disburse/preview')
def preview(model: Disburse):
    stamp, _, hashes, report, _ = _load(model)
    return {'fingerprint': stamp, 'hashes': hashes, **report}


@router.post('/api/disburse/publish')
def publish(model: Disburse):
    stamp, run_id, hashes, report, source_blob = _load(model)
    if not report['ready']:
        raise DataError('DISBURSE_BLOCKED', '；'.join(report['blockers']), 422)
    return repository.publish(stamp, run_id, hashes, report, source_blob)


@router.get('/api/disburse')
def batches():
    return repository.history()


@router.get('/api/disburse/{batch_no}')
def detail(batch_no: int):
    return repository.get_batch(batch_no)


@router.get('/api/disburse/{batch_no}/export')
def export(batch_no: int):
    batch = repository.get_batch(batch_no)
    return Response(json.dumps(batch, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="disburse-{batch_no}.json"'})
