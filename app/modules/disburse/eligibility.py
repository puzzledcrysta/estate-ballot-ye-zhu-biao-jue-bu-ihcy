"""议案请款资格：只有计票双门槛通过、且与独立账逐笔对账无差的议案可进入请款。

- 未通过 / 待核实（隔离、被新版本阻断等）/ 不在已计票结果中的议案：不出账单。
- 已通过但与外部账存在差额、单边缺失：不出账单，记为待核实。
- 对账无差（含外部账缺键但复算为零的单边记录之外的 MATCH）才放行。
"""


def _passed(run, key):
    for row in run.get('accepted', []):
        if row['key'] != key:
            continue
        metrics = {item['metric']: item['value'] for item in row['proof']}
        return metrics.get('双门槛') == '通过'
    return False


def _reconcile_code(run, key):
    for row in run.get('book', {}).get('rows', []):
        if row['key'] == key:
            return row['code']
    return None


def classify(contracts, run):
    """按议案键给出 eligible / excluded 两份清单及逐单理由。

    对账无差的判定：该议案在对账表中存在且结论为 MATCH（复算与外部账逐笔相符）。
    """
    quarantined = {row['key'] for row in run.get('quarantine', [])}
    suppressed = {row['key'] for row in run.get('suppressed', [])}
    accepted_keys = {row['key'] for row in run.get('accepted', [])}
    eligible, excluded = [], []
    for contract in contracts:
        key = contract['key']
        code = _reconcile_code(run, key)
        if key in quarantined or key in suppressed:
            status, reason = '待核实', '议案有隔离或被新版本替代的记录，尚待核实，不出账单'
        elif key not in accepted_keys:
            status, reason = '未通过', '议案不在本批已计票通过的结果内，未通过，不出账单'
        elif not _passed(run, key):
            status, reason = '未通过', '议案未达户与面积双门槛，未通过，不出账单'
        elif code is None:
            status, reason = '待核实', '议案已通过但缺少对账记录，待核实，不出账单'
        elif code != 'MATCH':
            status, reason = '待核实', f'议案虽通过但对账结论为 {code}，金额或单边存在差异，待核实，不出账单'
        else:
            eligible.append(contract)
            continue
        excluded.append({**contract, 'total': str(contract['total']),
                         'status': status, 'reason': reason})
    return eligible, excluded
