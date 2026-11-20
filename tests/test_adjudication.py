import csv
import io
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.pipeline import stages


DISPUTE_KEY = '00899'


@pytest.fixture
def anchor(client):
    return client.post('/api/runs', json={}).json()['manifest']['run_id']


def dispute(run_or_stages, key=DISPUTE_KEY):
    return next(item for item in run_or_stages['disputes'] if item['key'] == key)


def test_conflicting_curves_stay_quarantined_with_provisional_estimates(client, anchor):
    """复核前：冲突工况继续隔离，工作台给出未核实估算但不进对账。"""
    run = client.get(f'/api/runs/{anchor}').json()
    assert DISPUTE_KEY not in {row['key'] for row in run['accepted']}
    assert [row['code'] for row in run['quarantine'] if row['key'] == DISPUTE_KEY] == ['REV_CONFLICT', 'REV_CONFLICT']
    group = dispute(run)
    assert group['revision'] == 2
    lines = sorted(candidate['line'] for candidate in group['candidates'])
    assert lines == [35, 36]
    amounts = sorted(candidate['amount'] for candidate in group['candidates'])
    assert amounts == ['100.000', '120.000']
    # 原始对账不含未核实流量
    assert DISPUTE_KEY not in {row['key'] for row in run['book']['rows']}


def test_estimates_fall_on_both_sides_of_threshold(client, anchor):
    """两份同版曲线的复算流量落在调度阈值两侧，并带明确的未核实标记。"""
    run = client.get(f'/api/runs/{anchor}').json()
    sides = {candidate['amount']: candidate['side'] for candidate in dispute(run)['candidates']}
    assert sides['100.000'] == '低于阈值' and sides['120.000'] == '高于阈值'
    assert all(candidate['threshold'] == run['manifest']['parameters']['dispatch_threshold']
               for candidate in dispute(run)['candidates'])


def test_adjudication_picks_one_curve_and_recomputes_ledger(client, anchor):
    """裁定采信后：唯一正式流量进入对账，先前的交班估算仍可查。"""
    run = client.get(f'/api/runs/{anchor}').json()
    chosen = next(c for c in dispute(run)['candidates'] if c['amount'] == '120.000')
    response = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}',
                           json={'chosen_line': chosen['line'], 'note': '采信控制柜旁证曲线'})
    assert response.status_code == 200
    outcome = response.json()
    assert outcome['adjudicated_keys'] == [DISPUTE_KEY] and outcome['open_disputes'] == []
    effective = client.get(f'/api/adjudication/{anchor}/effective').json()
    row = next(item for item in effective['accepted'] if item['key'] == DISPUTE_KEY)
    assert (row['amount'], row['line'], row['adjudicated']) == ('120.000', chosen['line'], True)
    book_row = next(item for item in effective['book']['rows'] if item['key'] == DISPUTE_KEY)
    assert book_row['code'] == 'MISSING_BOOK'  # 外部账无此键，按缺失登记而不是静默入账
    # 原始批次结果与交班估算保持原样、可追溯
    stored = client.get(f'/api/runs/{anchor}').json()
    assert DISPUTE_KEY not in {item['key'] for item in stored['accepted']}
    assert dispute(stored)['candidates']
    export = client.get(f'/api/export/{anchor}').json()
    assert [item['key'] for item in export['adjudications']] == [DISPUTE_KEY]
    assert export['provisional_estimates'][0]['candidates']
    assert export['effective_reconciliation']['rows']


def test_opposite_rulings_cannot_both_become_official(client, anchor):
    """两人同时提交相反裁定：同一工况只能有一份正式流量。"""
    run = client.get(f'/api/runs/{anchor}').json()
    first_line, second_line = sorted(c['line'] for c in dispute(run)['candidates'])
    first = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': first_line})
    assert first.status_code == 200
    conflict = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': second_line})
    assert (conflict.status_code, conflict.json()['code']) == (409, 'ADJUDICATION_EXISTS')
    rulings = client.get(f'/api/adjudication/{anchor}').json()['rulings']
    assert len(rulings) == 1 and rulings[0]['chosen_line'] == first_line


def test_concurrent_opposite_adjudications_single_winner(client, anchor):
    """数据库唯一约束兜底并发，绝不产生两条正式流量。"""
    run = client.get(f'/api/runs/{anchor}').json()
    lines = sorted(c['line'] for c in dispute(run)['candidates'])

    def submit(line):
        return client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': line})

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = [response.status_code for response in pool.map(submit, lines * 2)]
    assert sorted(statuses) == [200, 200, 409, 409]
    rulings = client.get(f'/api/adjudication/{anchor}').json()['rulings']
    assert len(rulings) == 1


def test_same_ruling_rerun_is_idempotent_without_new_batch(client, anchor):
    """同一份裁定重跑不新增批次、不新增裁定记录，返回幂等标记。"""
    run = client.get(f'/api/runs/{anchor}').json()
    line = dispute(run)['candidates'][0]['line']
    first = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': line}).json()
    second = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': line}).json()
    assert first['ruling'] == second['ruling']
    assert (first['idempotent'], second['idempotent']) == (False, True)
    assert len(client.get('/api/runs').json()) == 1
    assert len(client.get(f'/api/adjudication/{anchor}').json()['rulings']) == 1


def test_unknown_dispute_and_foreign_line_are_rejected(client, anchor):
    line = dispute(client.get(f'/api/runs/{anchor}').json())['candidates'][0]['line']
    missing = client.post(f'/api/adjudication/{anchor}/00000', json={'chosen_line': line})
    assert (missing.status_code, missing.json()['code']) == (404, 'DISPUTE_UNKNOWN')
    foreign = client.post(f'/api/adjudication/{anchor}/{DISPUTE_KEY}', json={'chosen_line': 2})
    assert (foreign.status_code, foreign.json()['code']) == (422, 'CANDIDATE_UNKNOWN')


def test_non_conflicting_keys_recalculate_as_before(source):
    """无冲突的工况照常复算，估算与裁定逻辑不影响既有结果。"""
    result = stages(source)
    keys = {row['key'] for row in result['accepted']}
    assert '00101' in keys and DISPUTE_KEY not in keys
    assert len(result['disputes']) == 1


def test_estimate_of_uncrossable_curve_is_flagged_not_accepted():
    """候选曲线本身算不出交点时，估算标记失败且禁止被采信。"""
    header = '工况单,修订号,提交时间,泵组曲线,管线曲线,原始备注'
    good = ('00700,1,2026-11-18T09:30:00+08:00,'
            '"甲=0:100,100:0;乙=0:100,100:0","0:0,200:100",')
    bad = '00700,1,2026-11-18T09:30:00+08:00,"甲=0:100,100:90","0:0,100:20",'
    payload = ('\n'.join((header, good, bad)) + '\n').encode()
    report = stages(payload)
    candidates = dispute(report, '00700')['candidates']
    failed = next(c for c in candidates if not c['ok'])
    assert failed['code'] == 'PUMP_CROSS'
