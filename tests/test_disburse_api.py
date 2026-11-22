from base64 import b64encode
from pathlib import Path
import pytest
from app.store import connection

ROOT = Path(__file__).resolve().parents[1]


def sample(name):
    return (ROOT / 'sample_data' / name).read_bytes()


def enc(blob):
    return b64encode(blob).decode()


@pytest.fixture
def run_id(client):
    return client.post('/api/runs', json={}).json()['manifest']['run_id']


def body(run_id, **overrides):
    payload = {'run_id': run_id,
               'contracts_base64': enc(sample('contracts.csv')),
               'estates_base64': enc(sample('estates.csv')),
               'balances_base64': enc(sample('balances.csv'))}
    for name in ('contracts', 'estates', 'balances'):
        if name in overrides:
            payload.pop(f'{name}_base64', None)
    payload.update(overrides)
    return payload


def sufficient_balances():
    text = sample('balances.csv').decode().replace('3900.00', '5000.00')
    return text


def test_preview_reports_blockers_but_writes_nothing(client, run_id):
    preview = client.post('/api/disburse/preview', json=body(run_id))
    assert preview.status_code == 200
    data = preview.json()
    assert data['ready'] is False and data['shortfall'][0]['building'] == '2栋'
    assert client.get('/api/disburse').json() == []


def test_publish_blocked_returns_422_and_creates_no_batch(client, run_id):
    response = client.post('/api/disburse/publish', json=body(run_id))
    assert (response.status_code, response.json()['code']) == (422, 'DISBURSE_BLOCKED')
    assert client.get('/api/disburse').json() == []


def test_publish_succeeds_and_is_idempotent(client, run_id):
    payload = body(run_id, balances=sufficient_balances())
    first = client.post('/api/disburse/publish', json=payload)
    assert first.status_code == 200
    batch = first.json()
    assert batch['idempotent'] is False and batch['billed_total'] == '8120.00'
    again = client.post('/api/disburse/publish', json=payload)
    assert again.status_code == 200
    second = again.json()
    assert second['idempotent'] is True and second['batch_no'] == batch['batch_no']
    assert len(client.get('/api/disburse').json()) == 1   # 同批重跑不新增请款


def test_persisted_bills_and_ledger_cover_whole_batch(client, run_id):
    from decimal import Decimal
    payload = body(run_id, balances=sufficient_balances())
    batch_no = client.post('/api/disburse/publish', json=payload).json()['batch_no']
    with connection() as db:
        bills = db.execute('SELECT amount FROM disburse_bills WHERE batch_no=?', (batch_no,)).fetchall()
        ledger = db.execute('SELECT DISTINCT status FROM disburse_ledger WHERE batch_no=?',
                            (batch_no,)).fetchall()
    assert len(bills) == 7
    assert sum((Decimal(row['amount']) for row in bills), Decimal(0)) == Decimal('8120.00')
    assert [row['status'] for row in ledger] == ['生效']


def test_failed_new_batch_keeps_old_bills_intact(client, run_id):
    from decimal import Decimal
    good = body(run_id, balances=sufficient_balances())
    first_no = client.post('/api/disburse/publish', json=good).json()['batch_no']
    # 第二份输入余额不足，发布被拒；旧批次账单必须原样保留
    blocked = client.post('/api/disburse/publish', json=body(run_id))
    assert blocked.status_code == 422
    with connection() as db:
        kept = db.execute('SELECT batch_no, amount FROM disburse_bills').fetchall()
    totals = {}
    for row in kept:
        totals[row['batch_no']] = totals.get(row['batch_no'], Decimal(0)) + Decimal(row['amount'])
    assert list(totals) == [first_no] and totals[first_no] == Decimal('8120.00')
    detail = client.get(f'/api/disburse/{first_no}').json()
    assert detail['report']['billed_total'] == '8120.00'


def test_distinct_inputs_create_separate_batches_and_history(client, run_id):
    first = client.post('/api/disburse/publish',
                        json=body(run_id, balances=sufficient_balances())).json()
    other = sufficient_balances().replace('5000.00', '6000.00')
    second = client.post('/api/disburse/publish', json=body(run_id, balances=other)).json()
    assert first['batch_no'] != second['batch_no'] and second['idempotent'] is False
    history = client.get('/api/disburse').json()
    assert [row['batch_no'] for row in history] == [second['batch_no'], first['batch_no']]


def test_export_round_trips_full_report(client, run_id):
    batch_no = client.post('/api/disburse/publish',
                           json=body(run_id, balances=sufficient_balances())).json()['batch_no']
    response = client.get(f'/api/disburse/{batch_no}/export')
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    exported = response.json()
    assert exported['batch_no'] == batch_no
    assert exported['report']['contract_total'] == '9920.00'
    assert {b['building'] for b in exported['report']['buildings']} == {'1栋', '2栋', '3栋'}


def test_unknown_batch_404(client):
    response = client.get('/api/disburse/999')
    assert (response.status_code, response.json()['code']) == (404, 'BATCH_UNKNOWN')


def test_unknown_run_rejected(client):
    response = client.post('/api/disburse/preview', json=body('no-such-run'))
    assert (response.status_code, response.json()['code']) == (404, 'RUN_UNKNOWN')


def test_concurrent_publish_creates_single_batch(client, run_id):
    from concurrent.futures import ThreadPoolExecutor
    payload = body(run_id, balances=sufficient_balances())
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(
            lambda _: client.post('/api/disburse/publish', json=payload), range(4)))
    assert all(response.status_code == 200 for response in responses)
    batch_numbers = {response.json()['batch_no'] for response in responses}
    assert len(batch_numbers) == 1
    assert len(client.get('/api/disburse').json()) == 1


def test_publish_failure_rolls_back_entire_batch(client, run_id, monkeypatch):
    import sqlite3
    from app import settings
    from app.modules.disburse import repository
    payload = body(run_id, balances=sufficient_balances())

    class Boom(Exception):
        pass

    class FlakyConnection:
        def __init__(self):
            self.db = sqlite3.connect(settings.DB, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.ledger_inserts = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is not None:
                self.db.rollback()      # 模拟事务回滚
            self.db.close()
            return False

        def execute(self, sql, params=()):
            if 'INSERT INTO disburse_ledger' in sql:
                self.ledger_inserts += 1
                if self.ledger_inserts == 2:
                    raise Boom('模拟台账写入中途失败')
            return self.db.execute(sql, params)

        def commit(self):
            self.db.commit()

    monkeypatch.setattr(repository, 'connector', lambda: FlakyConnection())
    with pytest.raises(Boom):
        client.post('/api/disburse/publish', json=payload)
    monkeypatch.undo()
    assert client.get('/api/disburse').json() == []
    with connection() as db:
        assert db.execute('SELECT COUNT(*) n FROM disburse_batches').fetchone()['n'] == 0
        assert db.execute('SELECT COUNT(*) n FROM disburse_bills').fetchone()['n'] == 0
        assert db.execute('SELECT COUNT(*) n FROM disburse_ledger').fetchone()['n'] == 0


def test_duplicate_contract_key_rejected(client, run_id):
    import csv
    import io
    rows = list(csv.reader(io.StringIO(sample('contracts.csv').decode())))
    out = io.StringIO(newline='')
    csv.writer(out).writerows([rows[0], rows[1], rows[1]])
    response = client.post('/api/disburse/preview',
                           json=body(run_id, contracts=out.getvalue()))
    assert (response.status_code, response.json()['code']) == (422, 'CONTRACT_CONFLICT')


def test_sub_cent_amount_rejected_at_reader(client, run_id):
    text = '表决议案,受益范围,合同总价(元),公共部分\n00108,全小区,10.001,是\n'
    response = client.post('/api/disburse/preview', json=body(run_id, contracts=text))
    assert (response.status_code, response.json()['code']) == (422, 'NUMBER')


def test_duplicate_balance_building_rejected(client, run_id):
    text = '楼栋,可用余额(元)\n1栋,1.00\n1栋,2.00\n'
    response = client.post('/api/disburse/preview', json=body(run_id, balances=text))
    assert (response.status_code, response.json()['code']) == (422, 'BALANCE_CONFLICT')


def test_duplicate_estate_unit_rejected(client, run_id):
    text = '楼栋,户号,业主,建筑面积(平方米)\n1栋,101,甲,10.00\n1栋,101,乙,20.00\n'
    response = client.post('/api/disburse/preview', json=body(run_id, estates=text))
    assert (response.status_code, response.json()['code']) == (422, 'ESTATE_CONFLICT')
