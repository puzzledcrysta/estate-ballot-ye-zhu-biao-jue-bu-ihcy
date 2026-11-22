"""维修资金按建筑面积分摊的定点计算引擎。

金额一律以「元」计、保留两位小数；面积保留输入精度。
每一层分摊都用「尾差逐分找补」保证各分档金额之和与被分摊总额分毫不差：
先按权重求理论金额并向下取整到分，再把剩余的整分按尾差从大到小补发，
并列时按稳定次序补发，结果可复算。
"""
from decimal import Decimal, ROUND_FLOOR

CENT = Decimal('0.01')


def money(value):
    """合同金额与余额只接受两位小数。"""
    if value != value.quantize(CENT):
        raise ValueError('金额至多两位小数')
    return value


def to_cents(value):
    return int((value / CENT).to_integral_value(rounding=ROUND_FLOOR))


def _distribute(total, weighted):
    """按权重把 total（已是两位小数）分到若干档，加权和与 total 精确相等。

    weighted 为 [(key, weight, basis)]，weight 为正的 Decimal。
    返回 {key: Decimal 金额}，次序稳定、可复算。
    """
    total_cents = to_cents(total)
    weight_sum = sum((item[1] for item in weighted), Decimal(0))
    raw = [(key, total * weight / weight_sum, index) for index, (key, weight, _) in enumerate(weighted)]
    floored = {key: to_cents(value) for key, value, _ in raw}
    leftover = total_cents - sum(floored.values())
    # 尾差 = 理论值 - 已取整值；大者先得一分，并列按原次序。
    order = sorted(raw, key=lambda item: (-(item[1] - floored[item[0]]), item[2]))
    for key, _, _ in order[:leftover]:
        floored[key] += 1
    return {key: Decimal(cents) * CENT for key, cents in floored.items()}


def split_buildings(total, building_areas):
    """合同总价按各栋建筑面积分到楼栋。

    building_areas: [(building, area)]，area 为正。返回 {building: 金额}。
    """
    weighted = [(building, area, area) for building, area in building_areas]
    return _distribute(total, weighted)


def split_households(building_amount, units):
    """楼栋金额按户内建筑面积分到各户。

    units: [{'building','unit','owner','area'}]，同栋各户。
    返回 {unit_index: 金额}，键用列表下标以免同号户号冲突。
    """
    weighted = [(index, unit['area'], unit['area']) for index, unit in enumerate(units)]
    return _distribute(building_amount, weighted)
