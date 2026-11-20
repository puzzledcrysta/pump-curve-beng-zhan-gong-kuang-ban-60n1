import json
from app.modules.runs.service import get
from app.modules.adjudication.repository import list_for
from app.modules.adjudication.service import effective_view
from .decisions import timeline


def bundle(run_id):
    history = timeline(run_id)
    run = get(run_id)
    rulings = list_for(run_id)
    view = effective_view(run_id)
    # The export carries source line evidence, the applicable decision version,
    # provisional handover estimates and adjudicated reconciliation.
    return json.dumps({'manifest': run['manifest'], 'counts': run['counts'], 'results': run['accepted'],
                       'quarantine': run['quarantine'], 'suppressed': run['suppressed'],
                       'reconciliation': run['book'],
                       'provisional_estimates': run.get('disputes', []),
                       'adjudications': rulings,
                       'effective_reconciliation': view['book'],
                       'review': history}, ensure_ascii=False, indent=2)
