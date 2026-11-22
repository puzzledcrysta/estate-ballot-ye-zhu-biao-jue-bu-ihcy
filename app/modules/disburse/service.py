"""请款分账服务：把合格维修合同按面积分摊到楼栋与户，校验分栋资金余额。

不变量：
- 每份出账合同的账单金额之和与合同总价分厘不差（定点尾差找补）。
- 整批账单总额等于全部合格合同总价；未通过或待核实议案一律不进账单。
- 任何受益楼栋余额不足，整批不得发布；发布动作整批生效或整批退回。
"""
import hashlib
import json
from decimal import Decimal
from app.errors import DataError
from .allocating import CENT, money, split_buildings, split_households
from .eligibility import classify


def _building_index(estates):
    areas, units = {}, {}
    for unit in estates:
        building = unit['building']
        areas[building] = areas.get(building, Decimal(0)) + unit['area']
        units.setdefault(building, []).append(unit)
    for building in units:
        units[building].sort(key=lambda item: (item['unit'], item['owner']))
    return dict(sorted(areas.items())), {key: units[key] for key in sorted(units)}


def _check_money(contracts, balances):
    for contract in contracts:
        contract['total'] = money(contract['total'])
    for building, value in balances.items():
        balances[building] = money(value)


def build_report(run, contracts, estates, balances):
    _check_money(contracts, balances)
    areas, units_by_building = _building_index(estates)
    estate_buildings = set(areas)
    if set(balances) != estate_buildings:
        missing = sorted(estate_buildings - set(balances))
        unknown = sorted(set(balances) - estate_buildings)
        if missing:
            raise DataError('BALANCE_MISSING', f'楼栋 {",".join(missing)} 缺资金余额，无法判断是否足额')
        raise DataError('BALANCE_UNKNOWN', f'余额表含房产台账不存在的楼栋 {",".join(unknown)}')

    eligible, excluded = classify(contracts, run)
    contract_reports, bill_acc = [], {}

    def bill(building, unit_no, owner, area, amount, key):
        slot = bill_acc.setdefault((building, unit_no),
                                   {'building': building, 'unit': unit_no, 'owner': owner,
                                    'area': Decimal(0), 'amount': Decimal(0), 'parts': []})
        slot['area'] += area
        slot['amount'] += amount
        slot['parts'].append({'key': key, 'amount': amount})

    for contract in eligible:
        key, total, public, scope = (contract[name] for name in ('key', 'total', 'public', 'scope'))
        if public:
            paying = list(areas)
            scope_note = '公共部分，由全小区按楼栋建筑面积分担'
        else:
            unknown = [name for name in scope if name not in areas]
            if unknown:
                raise DataError('SCOPE_UNKNOWN', f'合同 {key} 受益楼栋 {",".join(unknown)} 不在房产台账')
            paying = [name for name in areas if name in scope]
            if not paying:
                raise DataError('SCOPE_UNKNOWN', f'合同 {key} 受益范围未匹配到任何楼栋')
            scope_note = f'专有部分，仅由受益楼栋 {"、".join(paying)} 分担，未受益楼栋不分摊'
        weighted_areas = [(name, areas[name]) for name in paying]
        building_shares = split_buildings(total, weighted_areas)
        share_rows, household_rows = [], []
        for building in paying:
            share = building_shares[building]
            units = units_by_building[building]
            per_household = split_households(share, units)
            share_rows.append({'building': building, 'amount': str(share),
                               'area': str(areas[building]),
                               'basis': f'{scope_note}；该栋面积 {areas[building]} ㎡ 占受益面积 '
                                        f'{sum(areas[n] for n in paying)} ㎡ 之比，尾差按分找补'})
            for index, unit in enumerate(units):
                amount = per_household[index]
                household_rows.append({'building': building, 'unit': unit['unit'], 'owner': unit['owner'],
                                       'area': str(unit['area']), 'amount': str(amount),
                                       'basis': f'{building} 受益面积 {areas[building]} ㎡，'
                                                f'本户 {unit["area"]} ㎡ 按户内面积分摊，尾差按分找补'})
                bill(building, unit['unit'], unit['owner'], unit['area'], amount, key)
        billed = sum(building_shares.values(), Decimal(0))
        contract_reports.append({'key': key, 'total': str(total), 'public': public,
                                 'scope': scope, 'paying_buildings': paying,
                                 'building_shares': share_rows, 'household_bills': household_rows,
                                 'billed_total': str(billed), 'balanced': billed == total,
                                 'reason': scope_note})

    bills = [bill_acc[slot] for slot in sorted(bill_acc)]
    for row in bills:
        row['area'] = str(row['area'])
        row['amount'] = str(row['amount'])
        row['parts'] = [{'key': part['key'], 'amount': str(part['amount'])} for part in row['parts']]

    building_rows, shortfall = [], []
    for building in areas:
        billed = sum((Decimal(row['amount']) for row in bills if row['building'] == building), Decimal(0))
        balance = balances[building]
        gap = billed - balance
        sufficient = billed <= balance
        building_rows.append({'building': building, 'area': str(areas[building]),
                              'balance': str(balance), 'billed': str(billed),
                              'gap': str(gap), 'sufficient': sufficient,
                              'reason': '余额足以覆盖本批分摊' if sufficient else
                                        f'本批分摊 {billed} 元超出可用余额 {balance} 元，缺 {gap} 元'})
        if not sufficient:
            shortfall.append({'building': building, 'balance': str(balance),
                              'billed': str(billed), 'gap': str(gap),
                              'reason': building_rows[-1]['reason']})

    owners = {}
    for row in bills:
        slot = owners.setdefault(row['owner'], {'owner': row['owner'], 'by_building': {},
                                                 'total': Decimal(0)})
        slot['by_building'][row['building']] = slot['by_building'].get(row['building'], Decimal(0)) + Decimal(row['amount'])
        slot['total'] += Decimal(row['amount'])
    owner_rows = [{'owner': owner, 'total': str(slot['total']),
                   'by_building': [{'building': name, 'amount': str(slot['by_building'][name])}
                                   for name in sorted(slot['by_building'])]}
                  for owner, slot in sorted(owners.items())]

    contract_total = sum((item['total'] for item in contracts), Decimal(0)).quantize(CENT)
    eligible_total = sum((Decimal(item['billed_total']) for item in contract_reports), Decimal(0)).quantize(CENT)
    billed_total = sum((Decimal(row['amount']) for row in bills), Decimal(0)).quantize(CENT)
    balanced = all(item['balanced'] for item in contract_reports) and billed_total == eligible_total
    blockers = []
    if not eligible:
        blockers.append('没有通过且对账无差的议案，无账单可发布')
    if not balanced:
        blockers.append('账单总额与合格合同总价未能对平')
    if shortfall:
        blockers.append('楼栋余额不足：' + '、'.join(item['building'] for item in shortfall))

    return {'run_id': run.get('manifest', {}).get('run_id', ''), 'contract_total': str(contract_total),
            'eligible_total': str(eligible_total), 'billed_total': str(billed_total),
            'balanced': balanced, 'ready': not blockers, 'blockers': blockers,
            'contracts': contract_reports, 'excluded': excluded, 'buildings': building_rows,
            'bills': bills, 'owners': owner_rows, 'shortfall': shortfall}


def fingerprint(run, contracts, estates, balances):
    """对全部输入字节的规范化内容取指纹，同批重跑不新增请款。"""
    payload = {'run_id': run.get('manifest', {}).get('run_id', ''),
               'contracts': sorted((c['key'], ';'.join(c['scope']), str(c['total']), '是' if c['public'] else '否')
                                   for c in contracts),
               'estates': sorted((u['building'], u['unit'], u['owner'], str(u['area'])) for u in estates),
               'balances': sorted((name, str(value)) for name, value in balances.items())}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
