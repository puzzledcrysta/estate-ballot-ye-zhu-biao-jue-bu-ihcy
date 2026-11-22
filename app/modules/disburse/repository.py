import json
from datetime import datetime, timezone
from app.errors import DataError
from app.store import connection as default_connection

# 可替换的会话工厂，便于在测试中注入故障连接验证整批回滚。
connector = default_connection


def _snapshot(db, row):
    return {'batch_no': row['batch_no'], 'fingerprint': row['fingerprint'], 'run_id': row['run_id'],
            'contract_hash': row['contract_hash'], 'estate_hash': row['estate_hash'],
            'balance_hash': row['balance_hash'], 'contract_total': row['contract_total'],
            'billed_total': row['billed_total'], 'created': row['created'],
            'report': json.loads(row['report'])}


def get_batch(batch_no):
    with connector() as db:
        row = db.execute('SELECT * FROM disburse_batches WHERE batch_no=?', (batch_no,)).fetchone()
        if row is None:
            raise DataError('BATCH_UNKNOWN', '没有此请款批次', 404)
        return _snapshot(db, row)


def history():
    with connector() as db:
        rows = db.execute('SELECT batch_no,run_id,contract_total,billed_total,created,fingerprint '
                          'FROM disburse_batches ORDER BY batch_no DESC LIMIT 50').fetchall()
        return [dict(row) for row in rows]


def publish(fingerprint, run_id, hashes, report, source_blob):
    """整批发布：单一事务内写批次、账单与生效台账；失败整体回滚，旧账单原样保留。

    同指纹重跑直接回传已存在批次，不新增请款。
    """
    created = datetime.now(timezone.utc).isoformat()
    with connector() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM disburse_batches WHERE fingerprint=?',
                              (fingerprint,)).fetchone()
        if existing is not None:
            db.commit()
            result = _snapshot(db, existing)
            result['idempotent'] = True
            return result
        cursor = db.execute(
            'INSERT INTO disburse_batches (fingerprint,run_id,contract_hash,estate_hash,balance_hash,'
            'contract_total,billed_total,created,report,source_blob) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (fingerprint, run_id, hashes['contract'], hashes['estate'], hashes['balance'],
             report['contract_total'], report['billed_total'], created,
             json.dumps(report, ensure_ascii=False), source_blob))
        batch_no = cursor.lastrowid
        for row in report['bills']:
            db.execute('INSERT INTO disburse_bills (batch_no,building,household,owner,amount,area) '
                       'VALUES (?,?,?,?,?,?)',
                       (batch_no, row['building'], row['unit'], row['owner'], row['amount'], row['area']))
            db.execute('INSERT INTO disburse_ledger (batch_no,building,household,owner,amount,status) '
                       'VALUES (?,?,?,?,?,?)',
                       (batch_no, row['building'], row['unit'], row['owner'], row['amount'], '生效'))
        db.commit()
        result = get_batch(batch_no)
        result['idempotent'] = False
        return result
