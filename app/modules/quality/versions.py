from collections import defaultdict
from decimal import Decimal
from app.evidence import finding
from app.settings import DISPATCH_THRESHOLD, UNIT
from app.modules.domain.rule import evaluate
from app.errors import DataError


def _estimate(candidate):
    """同一冲突组内每条来源各自复算，结果仅作交班估算，不进入对账。"""
    try:
        amount, proof = evaluate(candidate['fields'])
    except DataError as error:
        return {'line': candidate['line'], 'ok': False, 'code': error.code, 'detail': str(error)}
    amount = amount.quantize(Decimal('0.001'))
    return {'line': candidate['line'], 'ok': True, 'amount': format(amount, 'f'), 'unit': UNIT,
            'threshold': str(DISPATCH_THRESHOLD),
            'side': '高于阈值' if amount > DISPATCH_THRESHOLD else
                    '低于阈值' if amount < DISPATCH_THRESHOLD else '恰在阈值',
            'proof': [{'metric': label, 'value': str(value), 'unit': unit} for label, value, unit in proof]}


def split_versions(records, unverified):
    groups = defaultdict(list)
    for record in records:
        groups[record['key']].append(record)
    ready, duplicates, conflicts, disputes = [], [], [], []
    for key, group in sorted(groups.items()):
        latest = max(row['revision'] for row in group)
        pending = unverified.get(key, 0)
        if pending >= latest:
            conflicts.extend(finding(row, 'REV_BLOCKED', f'修订 {pending} 仍有来源未采信') for row in group)
            continue
        candidates = sorted((row for row in group if row['revision'] == latest), key=lambda row: row['line'])
        older = [row for row in group if row['revision'] != latest]
        duplicates.extend(finding(row, 'SUPERSEDED', f'修订 {latest} 取代旧版本') for row in older)
        signatures = {tuple(row['fields'].items()) for row in candidates}
        if len(signatures) > 1:
            conflicts.extend(finding(row, 'REV_CONFLICT', f'最高修订 {latest} 的来源字段互相冲突') for row in candidates)
            # 同版且字段互异：保留整组候选，逐曲线给出未核实估算
            disputes.append({'key': key, 'revision': latest,
                             'candidates': [_estimate(row) for row in candidates]})
        else:
            ready.append(candidates[0])
            duplicates.extend(finding(row, 'EXACT_DUP', f'同为修订 {latest}，保留第 {candidates[0]["line"]} 行')
                              for row in candidates[1:])
    return ready, duplicates, conflicts, disputes
