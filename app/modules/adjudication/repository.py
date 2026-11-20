import json
import sqlite3
from app.errors import DataError
from app.store import connection


def save(run_id, dispute, chosen_line, note, changed):
    """同工况只允许一条正式裁定：主键冲突即拒绝并发/重复的相反裁定。"""
    provisional = json.dumps(dispute, ensure_ascii=False)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT chosen_line FROM adjudications WHERE run_id=? AND key=?',
                              (run_id, dispute['key'])).fetchone()
        if existing is not None:
            if existing['chosen_line'] == chosen_line:
                # 同一份裁定重跑：幂等返回，不新增批次、不新增记录
                row = db.execute('SELECT * FROM adjudications WHERE run_id=? AND key=?',
                                 (run_id, dispute['key'])).fetchone()
                return dict(row), True
            raise DataError('ADJUDICATION_EXISTS', '该工况已有生效裁定，不能再提交相反结论', 409)
        try:
            db.execute('INSERT INTO adjudications (run_id,key,revision,chosen_line,note,changed,provisional)'
                       ' VALUES (?,?,?,?,?,?,?)',
                       (run_id, dispute['key'], dispute['revision'], chosen_line, note, changed, provisional))
        except sqlite3.IntegrityError as error:  # 并发下由唯一约束兜底
            raise DataError('ADJUDICATION_EXISTS', '该工况已有生效裁定，不能再提交相反结论', 409) from error
        row = db.execute('SELECT * FROM adjudications WHERE run_id=? AND key=?',
                         (run_id, dispute['key'])).fetchone()
        return dict(row), False


def list_for(run_id):
    with connection() as db:
        rows = db.execute('SELECT * FROM adjudications WHERE run_id=? ORDER BY key', (run_id,)).fetchall()
        return [dict(row) for row in rows]


def one(run_id, key):
    with connection() as db:
        row = db.execute('SELECT * FROM adjudications WHERE run_id=? AND key=?', (run_id, key)).fetchone()
        return dict(row) if row is not None else None
