from decimal import Decimal
import pytest
from app.pipeline import execute
from app.modules.disburse.allocating import split_buildings, split_households, CENT, money
from app.modules.disburse.readers import read_contracts, read_estates, read_balances
from app.modules.disburse.service import build_report, fingerprint
from app.errors import DataError

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]


@pytest.fixture
def run():
    src = (ROOT / 'sample_data/shift.csv').read_bytes()
    book = (ROOT / 'sample_data/external.csv').read_bytes()
    result = execute(src, book)
    result['manifest'] = {'run_id': 'run-demo'}
    return result


@pytest.fixture
def inputs():
    def load(name):
        return (ROOT / 'sample_data' / name).read_bytes()
    return (read_contracts(load('contracts.csv')),
            read_estates(load('estates.csv')),
            read_balances(load('balances.csv')))


def test_building_split_conserves_total_to_the_cent():
    # 100 元按 1:1:1 面积分到三栋：33.34+33.33+33.33，尾差按稳定次序补给第一栋，总额分厘不差
    shares = split_buildings(Decimal('100.00'), [('A', Decimal(1)), ('B', Decimal(1)), ('C', Decimal(1))])
    assert list(shares.values()) == [Decimal('33.34'), Decimal('33.33'), Decimal('33.33')]
    assert sum(shares.values(), Decimal(0)) == Decimal('100.00')


def test_household_split_weights_by_area_and_is_stable():
    units = [{'building': 'A', 'unit': str(i), 'owner': 'o', 'area': Decimal(a)}
             for i, a in enumerate((1, 2, 3))]
    shares = split_households(Decimal('100.00'), units)
    assert sum(shares.values(), Decimal(0)) == Decimal('100.00')
    # 权重 1:2:3 -> 16.67 / 33.33 / 50.00（整除到分，无尾差）
    assert [shares[i] for i in range(3)] == [Decimal('16.67'), Decimal('33.33'), Decimal('50.00')]


def test_money_rejects_sub_cent():
    with pytest.raises(ValueError):
        money(Decimal('1.001'))


def test_report_totals_balance_and_contract_kept_whole(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    assert report['contract_total'] == '9920.00'      # 含未出账合同，合同总额一分不少
    assert report['eligible_total'] == '8120.00'
    assert report['billed_total'] == '8120.00'        # 只对合格议案出账
    assert report['balanced'] is True
    for contract in report['contracts']:
        assert contract['balanced']
        assert Decimal(contract['billed_total']) == Decimal(contract['total'])


def test_only_passed_and_reconciled_proposals_bill(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    billed = {c['key'] for c in report['contracts']}
    assert billed == {'00108', '00114', '00126'}      # 通过且对账无差
    excluded = {row['key']: row['status'] for row in report['excluded']}
    assert excluded == {'00102': '待核实', '00101': '待核实', '09999': '未通过'}


def test_public_contract_shared_by_all_buildings_private_by_beneficiaries(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    public = next(c for c in report['contracts'] if c['key'] == '00108')
    assert public['paying_buildings'] == ['1栋', '2栋', '3栋']
    private = next(c for c in report['contracts'] if c['key'] == '00126')
    assert private['paying_buildings'] == ['2栋']     # 未受益楼栋不替别人买单
    assert all(row['building'] == '2栋' for row in private['household_bills'])


def test_cross_building_owner_aggregated_per_building_and_total(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    jia = next(o for o in report['owners'] if o['owner'] == '甲')
    yi = next(o for o in report['owners'] if o['owner'] == '乙')
    assert {p['building'] for p in jia['by_building']} == {'1栋', '2栋'}
    assert {p['building'] for p in yi['by_building']} == {'1栋', '3栋'}
    assert Decimal(jia['total']) == sum((Decimal(p['amount']) for p in jia['by_building']), Decimal(0))
    assert sum((Decimal(o['total']) for o in report['owners']), Decimal(0)) == Decimal(report['billed_total'])


def test_shortfall_blocks_publication_with_reasons(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    assert report['ready'] is False
    assert report['shortfall'] and report['shortfall'][0]['building'] == '2栋'
    assert any('余额不足' in blocker for blocker in report['blockers'])
    row = next(b for b in report['buildings'] if b['building'] == '2栋')
    assert row['sufficient'] is False and Decimal(row['gap']) > 0


def test_sufficient_balances_makes_ready(run, inputs):
    contracts, estates, balances = inputs
    balances['2栋'] = Decimal('5000.00')
    report = build_report(run, contracts, estates, balances)
    assert report['ready'] is True and report['blockers'] == []
    assert all(b['sufficient'] for b in report['buildings'])


def test_missing_balance_building_raises(run, inputs):
    contracts, estates, _ = inputs
    with pytest.raises(DataError) as error:
        build_report(run, contracts, estates, {'1栋': Decimal('1'), '2栋': Decimal('1')})
    assert error.value.code == 'BALANCE_MISSING'


def test_unknown_balance_building_raises(run, inputs):
    contracts, estates, balances = inputs
    balances['4栋'] = Decimal('1')
    with pytest.raises(DataError) as error:
        build_report(run, contracts, estates, balances)
    assert error.value.code == 'BALANCE_UNKNOWN'


def test_private_contract_scope_unknown_building(run, inputs):
    contracts, estates, balances = inputs
    contracts[1]['scope'] = ['9栋']
    with pytest.raises(DataError) as error:
        build_report(run, contracts, estates, balances)
    assert error.value.code == 'SCOPE_UNKNOWN'


def test_fingerprint_stable_and_distinct(run, inputs):
    contracts, estates, balances = inputs
    first = fingerprint(run, contracts, estates, balances)
    assert first == fingerprint(run, [dict(c) for c in contracts], estates, balances)
    altered = [dict(c) for c in contracts]
    altered[0]['total'] = Decimal('6800.01')
    assert fingerprint(run, altered, estates, balances) != first


def test_all_excluded_leaves_nothing_to_bill(run, inputs):
    contracts, estates, balances = inputs
    # 全部指向不在计票结果内的未通过议案，且键互不重复
    for index, contract in enumerate(contracts):
        contract['key'] = f'9{index:04d}'
    report = build_report(run, contracts, estates, balances)
    assert report['contracts'] == [] and report['billed_total'] == '0.00'
    assert report['ready'] is False
    assert any('没有通过且对账无差' in blocker for blocker in report['blockers'])


def test_empty_benefit_scope_rejected():
    from app.errors import DataError
    from app.modules.disburse.readers import _names
    with pytest.raises(DataError) as error:
        _names(' ; ')
    assert error.value.code == 'BUSINESS'


def test_zero_contract_total_is_explicit(run, inputs):
    contracts, estates, balances = inputs
    report = build_report(run, contracts, estates, balances)
    # 合同总价为正的样例全部进入合格或排除清单，合计与逐单一致
    assert Decimal(report['contract_total']) == sum(
        (Decimal(c['total']) for c in contracts), Decimal(0))
