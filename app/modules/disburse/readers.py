from decimal import Decimal
from app.errors import DataError
from app.modules.domain.values import number
from app.modules.intake.reader import decode, records


MISSING = {'', '-', 'n/a', 'null', '未知'}
CENT = Decimal('0.01')


def money(raw, line, kind):
    """金额只接受两位小数，读入层即拒绝更细粒度，避免后期裸 ValueError。"""
    value = number(raw, None, 10**12)
    if value != value.quantize(CENT):
        raise DataError('NUMBER', f'{kind}第 {line} 行金额至多两位小数 {raw[:40]}')
    return value


def _open(payload, expected):
    text, _ = decode(payload)
    if not text.strip():
        raise DataError('EMPTY', '文件无内容')
    rows = iter(records(text))
    _, header = next(rows, (0, []))
    if header != expected:
        raise DataError('HEADER', f'表头应为 {",".join(expected)}')
    return rows


def _names(value):
    items = [part.strip() for part in value.split(';') if part.strip()]
    if not items:
        raise DataError('BUSINESS', '受益范围不能为空')
    if len(items) != len(set(items)):
        raise DataError('BUSINESS', f'受益范围存在重复楼栋 {value}')
    return items


def read_contracts(payload):
    """维修合同：议案编号、受益范围（楼栋；分号分隔）、合同总价、是否公共部分。"""
    rows = _open(payload, ['表决议案', '受益范围', '合同总价(元)', '公共部分'])
    contracts, seen = [], set()
    for line, row in rows:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 4:
            raise DataError('WIDTH', f'合同第 {line} 行列数不符')
        key, scope, amount, public = (cell.strip() for cell in row)
        if not key or key.casefold() in MISSING:
            raise DataError('REQUIRED', f'合同第 {line} 行缺议案编号')
        if key in seen:
            raise DataError('CONTRACT_CONFLICT', f'合同议案 {key} 重复登记')
        seen.add(key)
        total = money(amount, line, '合同')
        if public not in {'是', '否'}:
            raise DataError('BUSINESS', f'合同第 {line} 行公共部分须为是或否')
        contracts.append({'key': key, 'line': line, 'scope': _names(scope),
                          'total': total, 'public': public == '是'})
    if not contracts:
        raise DataError('HEADER_ONLY', '合同文件只有表头')
    return contracts


def read_estates(payload):
    """楼栋房产台账：楼栋、户号、业主、建筑面积。同业主可在多栋登记。"""
    rows = _open(payload, ['楼栋', '户号', '业主', '建筑面积(平方米)'])
    units, keys = [], set()
    for line, row in rows:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 4:
            raise DataError('WIDTH', f'房产台账第 {line} 行列数不符')
        building, unit, owner, area = (cell.strip() for cell in row)
        if any(value.casefold() in MISSING for value in (building, unit, owner, area)):
            raise DataError('REQUIRED', f'房产台账第 {line} 行有缺项')
        identity = (building, unit)
        if identity in keys:
            raise DataError('ESTATE_CONFLICT', f'{building} {unit} 重复登记')
        keys.add(identity)
        size = number(area, Decimal('0.01'), 10**9)
        units.append({'building': building, 'unit': unit, 'owner': owner,
                      'area': size, 'line': line})
    if not units:
        raise DataError('HEADER_ONLY', '房产台账只有表头')
    return units


def read_balances(payload):
    """分楼栋维修资金余额。"""
    rows = _open(payload, ['楼栋', '可用余额(元)'])
    balances, seen = {}, set()
    for line, row in rows:
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 2:
            raise DataError('WIDTH', f'资金余额第 {line} 行列数不符')
        building, amount = (cell.strip() for cell in row)
        if not building or building.casefold() in MISSING:
            raise DataError('REQUIRED', f'资金余额第 {line} 行缺楼栋')
        if building in seen:
            raise DataError('BALANCE_CONFLICT', f'楼栋 {building} 余额重复登记')
        seen.add(building)
        balances[building] = money(amount, line, '资金余额')
    if not balances:
        raise DataError('HEADER_ONLY', '资金余额只有表头')
    return balances
