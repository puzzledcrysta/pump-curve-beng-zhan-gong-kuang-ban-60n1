from datetime import datetime, timezone
from app.errors import DataError
from app.settings import UNIT
from app.modules.runs.service import get
from app.modules.runs.repository import fetch
from app.modules.reconcile.service import compare
from . import repository


def _dispute(run, key):
    for dispute in run.get('disputes', []):
        if dispute['key'] == key:
            return dispute
    raise DataError('DISPUTE_UNKNOWN', '该批次没有待裁定的同版冲突工况', 404)


def _candidate(dispute, chosen_line):
    for candidate in dispute['candidates']:
        if candidate['line'] == chosen_line:
            if not candidate['ok']:
                raise DataError('CANDIDATE_INVALID', '该来源曲线无法算出交点，不能采信')
            return candidate
    raise DataError('CANDIDATE_UNKNOWN', '指认的来源行不属于该冲突工况')


def _adjudicated_accepted(run):
    """原始 accepted 永久保留；生效视图在内存中并入已裁定的同版冲突工况。"""
    accepted = [dict(row) for row in run['accepted']]
    rulings = {item['key']: item for item in repository.list_for(run['manifest']['run_id'])}
    for key, ruling in rulings.items():
        chosen = next(candidate for dispute in run['disputes'] if dispute['key'] == key
                      for candidate in dispute['candidates'] if candidate['line'] == ruling['chosen_line'])
        accepted.append({'key': key, 'line': ruling['chosen_line'], 'revision': ruling['revision'],
                         'amount': chosen['amount'], 'unit': chosen.get('unit', UNIT),
                         'raw': [], 'proof': chosen['proof'], 'adjudicated': True})
    return sorted(accepted, key=lambda row: row['key'])


def effective_view(run_id):
    """裁定后重算并更新对账：以原外部账对全部生效流量重新比对。"""
    run = get(run_id)
    record = fetch(run_id)
    accepted = _adjudicated_accepted(run)
    book = compare(accepted, record['book'])
    rulings = repository.list_for(run_id)
    return {'run_id': run_id, 'accepted': accepted, 'book': book,
            'adjudicated_keys': [item['key'] for item in rulings],
            # 仍未裁定的冲突组继续隔离，其估算只用于交班
            'open_disputes': [dispute for dispute in run.get('disputes', [])
                              if dispute['key'] not in {item['key'] for item in rulings}]}


def adjudicate(run_id, key, chosen_line, note):
    run = get(run_id)
    dispute = _dispute(run, key)
    _candidate(dispute, chosen_line)  # 校验指认来源有效且可算出交点
    changed = datetime.now(timezone.utc).isoformat()
    ruling, idempotent = repository.save(run_id, dispute, chosen_line, note.strip(), changed)
    view = effective_view(run_id)
    return {'ruling': {field: ruling[field] for field in ('key', 'revision', 'chosen_line', 'note', 'changed')},
            'idempotent': idempotent, 'book': view['book'], 'adjudicated_keys': view['adjudicated_keys'],
            'open_disputes': view['open_disputes']}
