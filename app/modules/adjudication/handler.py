from fastapi import APIRouter
from app.inputs import Adjudication
from app.modules.runs.repository import lookup
from app.store import connection
from . import repository
from .service import adjudicate, effective_view

router = APIRouter()


@router.get('/api/adjudication/{run_id}')
def list_disputes(run_id: str):
    with connection() as db:
        lookup(db, run_id)
    run_view = effective_view(run_id)
    return {'rulings': repository.list_for(run_id),
            'open_disputes': run_view['open_disputes']}


@router.post('/api/adjudication/{run_id}/{key}')
def rule(run_id: str, key: str, body: Adjudication):
    return adjudicate(run_id, key, body.chosen_line, body.note)


@router.get('/api/adjudication/{run_id}/effective')
def effective(run_id: str):
    return effective_view(run_id)
