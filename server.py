"""本机审阅服务：SQLite追加历史、版本绑定、并发冲突检查。"""
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote
from datetime import datetime, timezone
import argparse
import hashlib
import json
import mimetypes
import sqlite3
import secrets
from contextlib import contextmanager

HERE = Path(__file__).resolve().parent
from threading import RLock
from project import load_project, proposal, digest

parser = argparse.ArgumentParser(description="Serve a local Revision Desk workspace")
parser.add_argument('--port', type=int, default=8540)
workspace = parser.add_mutually_exclusive_group(required=True)
workspace.add_argument('--workspace', type=Path)
workspace.add_argument('--demo', action='store_true', help="open a persistent copy of the fictional example")
args = parser.parse_args()
if args.demo:
    from shutil import copytree
    ROOT = HERE / '.local' / 'demo'
    if not ROOT.exists():
        copytree(HERE / 'examples' / 'garden', ROOT)
else:
    ROOT = args.workspace.resolve()
DATA, INPUTS, DOCS, IMAGES = load_project(ROOT)
db_dir = ROOT / '.reviewdesk'
db_dir.mkdir(exist_ok=True)
guard = (db_dir / 'service.lock').open('a+b')
try:
    import os
    if os.name == 'nt':
        import msvcrt
        guard.seek(0)
        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    raise SystemExit("This workspace already has a running server.")
DB = db_dir / 'review.sqlite3' 
LOCK = RLock()
ITEMS = {}
ACTIVE = {}
GROUPS = {x['id'] for x in DATA['tasks'] + DATA['issues']}
# 原稿及意见是候选赖以成立的基线；当前稿可在落实后刷新。
BASE = digest(json.dumps([DATA['tex_sha256'], DATA['tasks'], DATA['issues'],
                          [digest(IMAGES[p['image']]) for p in DOCS['original']['pages']]],
                         ensure_ascii=False, sort_keys=True).encode())
CURRENT = digest(json.dumps([DOCS['current']['source_lines'],
                             [digest(IMAGES[p['image']]) for p in DOCS['current']['pages']]],
                            ensure_ascii=False).encode())
TOKEN = secrets.token_urlsafe(32)
ORIGIN = f'http://127.0.0.1:{args.port}'


@contextmanager
def database():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


with database() as conn:
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, version TEXT NOT NULL, action TEXT NOT NULL, value TEXT NOT NULL, text TEXT NOT NULL, actor TEXT NOT NULL, time TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS proposals (entity TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(entity,version))')
    conn.execute('CREATE TABLE IF NOT EXISTS heads (entity TEXT PRIMARY KEY, version TEXT NOT NULL, current_hash TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    conn.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('baseline', BASE))
    if conn.execute("SELECT value FROM meta WHERE key='baseline'").fetchone()[0] != BASE:
        raise SystemExit("Original manuscript or review groups changed. Start a separate workspace for the new baseline.")
    conn.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('workspace', secrets.token_hex(12)))
    WORKSPACE_ID = conn.execute("SELECT value FROM meta WHERE key='workspace'").fetchone()[0]

    if args.demo and not conn.execute("SELECT 1 FROM heads LIMIT 1").fetchone():
        example = json.loads((HERE / 'examples' / 'change.json').read_text(encoding='utf-8'))
        item = proposal(example, DATA, DOCS)
        item['version'] = secrets.token_hex(12)
        conn.execute('INSERT INTO proposals VALUES(?,?,?)', (item['id'], item['version'], json.dumps(item, ensure_ascii=False)))
        conn.execute('INSERT INTO heads VALUES(?,?,?)', (item['id'], item['version'], CURRENT))


def reload_items():
    ACTIVE.clear()
    ITEMS.clear()
    DATA['documents'].clear()
    with database() as conn:
        for row in conn.execute('SELECT p.payload, h.current_hash FROM heads h JOIN proposals p USING(entity,version) ORDER BY h.rowid'):
            item = json.loads(row['payload'])
            if row['current_hash'] != CURRENT:
                item['current_lines'] = []
                item['current_anchors'] = []
                for edit in item['edits']:
                    edit.update(current_line=None, current_anchor=None)
            ITEMS[item['id']] = item
            if item['lifecycle'] != 'withdrawn':
                ACTIVE[item['id']] = item
            DATA['documents'][item['id']] = {'text': item['body']}
    DATA['items'] = list(ITEMS.values())


reload_items()


def events():
    with database() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM events ORDER BY seq')]


def state():
    log = events()
    with database() as conn:
        snapshots = {row['entity'] + ':' + row['version']: json.loads(row['payload']) for row in conn.execute('SELECT * FROM proposals')}
    states = {}
    for item_id, item in ITEMS.items():
        history = [e for e in log if e['entity'] == item_id]
        current = [e for e in history if e['version'] == item['version']]
        decision = next((e['value'] for e in reversed(current) if e['action'] == 'decision'), 'needs_info' if item.get('kind') == 'question' else 'pending')
        last_decision = next((e['seq'] for e in reversed(current) if e['action'] == 'decision'), 0)
        # 聊天明确授权单独保留来源，不伪造审阅台的“接受”事件。
        chat_authorized = bool(item.get('chat_authorization', {}).get('source') and item.get('chat_authorization', {}).get('text') and item.get('review_stage') == 'addressed' and decision == 'pending')
        implementation = next((e['value'] for e in reversed(current) if e['action'] == 'implementation' and e['seq'] > last_decision), 'not_started') if decision == 'accepted' or chat_authorized else 'not_started'
        states[item_id] = {'decision': decision, 'chat_authorized': chat_authorized, 'implementation': implementation, 'revision': history[-1]['seq'] if history else 0, 'history': history}
    groups = {}
    for group in GROUPS:
        related = [i for i in ACTIVE.values() if (i['task'] == group or group in i['issues'])]
        current_version = group_version(group)
        history = [e for e in log if e['entity'] == group]
        current = [e for e in history if e['version'] == current_version and e['action'] == 'closure']
        resolved = sum(states[i['id']]['decision'] in ('accepted','rejected') or i.get('review_stage')=='addressed' for i in related)
        verified = sum(states[i['id']]['decision'] == 'rejected' or ((states[i['id']]['decision'] == 'accepted' or states[i['id']]['chat_authorized']) and states[i['id']]['implementation'] == 'verified') for i in related)
        technical = [todo for task in DATA['tasks'] for todo in task.get('technical_todos', []) if task['id'] == group or group in todo.get('issues', [])]
        eligible = len(related) > 0 and verified == len(related) and not technical
        last_change = max((states[i['id']]['revision'] for i in related), default=0)
        closed = bool(eligible and current and current[-1]['value'] == 'closed' and current[-1]['seq'] > last_change)
        groups[group] = {'total': len(related), 'resolved': resolved, 'verified': verified, 'technical_pending': technical, 'eligible': eligible, 'closed': closed, 'history': history, 'version': current_version, 'revision': history[-1]['seq'] if history else 0}
    return {'items': states, 'groups': groups, 'events': log, 'proposals': snapshots}


def group_version(group):
    members = [(i['id'], i['version']) for i in ACTIVE.values() if i['task'] == group or group in i['issues']]
    technical = [todo for task in DATA['tasks'] for todo in task.get('technical_todos', []) if task['id'] == group or group in todo.get('issues', [])]
    return hashlib.sha256(json.dumps([members, technical]).encode()).hexdigest()[:16]


def stale_sources():
    return [name for name, sha in INPUTS.items()
            if not (ROOT / name).is_file() or digest((ROOT / name).read_bytes()) != sha]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *unused):
        pass

    def send(self, status, data, content_type='application/json; charset=utf-8'):
        raw = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        with LOCK:
            self.get()

    def get(self):
        if self.headers.get('Host') != f'127.0.0.1:{args.port}':
            return self.send(403, {'error': '请使用127.0.0.1本机地址'})
        path = unquote(urlparse(self.path).path)
        if path == '/api/data':
            return self.send(200, {'data': DATA, 'workspace_id': WORKSPACE_ID, 'token': TOKEN, 'stale': stale_sources()})
        if path == '/api/state':
            return self.send(200, state())
        if path == '/api/export':
            return self.send(200, {'project': DATA['project'], 'tex_sha256': DATA['tex_sha256'], 'items': DATA['items'], 'state': state()})
        if path == '/api/health':
            return self.send(200, {'project': DATA['project'], 'service': 'revision-desk-v1', 'items': len(ITEMS)})
        if path in IMAGES:
            return self.send(200, IMAGES[path], 'image/png')
        target = (HERE / 'web' / (path.lstrip('/') or 'index.html')).resolve()
        if not target.is_relative_to((HERE / 'web').resolve()) or not target.is_file():
            return self.send(404, {'error': '未找到文件'})
        kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if target.suffix == '.js':
            kind = 'text/javascript'
        self.send(200, target.read_bytes(), kind)

    def do_POST(self):
        with LOCK:
            self.post()

    def post(self):
        length_text = self.headers.get('Content-Length', '0')
        if not length_text.isdigit() or not 0 < int(length_text) < 100000:
            return self.send(400, {'error': '内容过长或为空'})
        raw = self.rfile.read(int(length_text))
        if self.headers.get('Origin') != ORIGIN or self.headers.get('X-Review-Token') != TOKEN or self.headers.get('Host') != f'127.0.0.1:{args.port}':
            return self.send(403, {'error': '请求来源不匹配，请从审阅页面操作'})
        if self.path == '/api/proposal':
            return self.publish(raw)
        if self.path not in ['/api/event','/api/batch']:
            return self.send(404, {'error': '未知操作'})
        if self.path == '/api/batch':
            return self.batch(raw)
        try:
            payload = json.loads(raw)
            entity = payload['entity']
            action = payload['action']
            value = payload.get('value','')
            note = str(payload.get('text','')).strip()
            expected = int(payload['revision'])
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return self.send(400, {'error': '请求格式不完整'})
        if entity not in ITEMS and entity not in GROUPS:
            return self.send(400, {'error': '未知修改点'})
        if entity in ITEMS and entity not in ACTIVE and action != 'comment':
            return self.send(409, {'error': '此旧提案已撤回或合并，仅保留讨论历史；请审阅关联的新提案。'})
        if entity in ITEMS and ITEMS[entity].get('review_deferred') and action != 'comment':
            return self.send(409, {'error': '此项暂缓审阅，目前可保存评论。'})
        version = ITEMS[entity]['version'] if entity in ITEMS else group_version(entity)
        if payload.get('version') != version or stale_sources():
            return self.send(409, {'error': '方案或原文已有新版本。请重新导入并刷新后审阅，旧历史将保留。'})
        allowed = {'decision': ['pending','accepted','rejected','needs_revision','needs_info'], 'implementation': ['not_started','applied','verified'], 'closure': ['open','closed'], 'comment': ['']}
        if action not in allowed or value not in allowed[action] or (entity in GROUPS and action not in ('closure','comment')) or (entity in ITEMS and action == 'closure'):
            return self.send(400, {'error': '无效的状态操作'})
        if (action == 'comment' or action == 'implementation' and value != 'not_started' or action == 'closure' and value == 'closed') and not note:
            return self.send(400, {'error': '请填写评论或落实/验收证据'})
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            last = conn.execute('SELECT MAX(seq) FROM events WHERE entity=?', (entity,)).fetchone()[0] or 0
            if last != expected:
                return self.send(409, {'error': '该点已有更新，已保留您的输入，请刷新历史后重试'})
            current_state = state()
            if action == 'implementation' and value != 'not_started' and current_state['items'][entity]['decision'] != 'accepted':
                return self.send(400, {'error': '须先接受当前提案，才能登记落实'})
            if action == 'closure' and value == 'closed' and not current_state['groups'][entity]['eligible']:
                return self.send(400, {'error': '仍有未决或未核验的关联修改，暂不能验收关闭'})
            actor = str(payload.get('actor', '作者')).strip()[:80] or '作者'
            conn.execute('INSERT INTO events(entity,version,action,value,text,actor,time) VALUES(?,?,?,?,?,?,?)', (entity, version, action, value, note, actor, datetime.now(timezone.utc).isoformat()))
        return self.send(200, state())

    def publish(self, raw):
        try:
            request = json.loads(raw)
            item = proposal(request['item'], DATA, DOCS)
            expected = request['expected_version']
            revision = request['revision']
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return self.send(400, {'error': str(exc)})
        if stale_sources():
            return self.send(409, {'error': 'Workspace inputs changed. Restart after checking the manuscript.'})
        key = item['id']
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            head = conn.execute('SELECT version FROM heads WHERE entity=?', (key,)).fetchone()
            seq = conn.execute('SELECT MAX(seq) FROM events WHERE entity=?', (key,)).fetchone()[0] or 0
            if expected != (head[0] if head else None) or revision != seq:
                return self.send(409, {'error': 'Read the current proposal and discussion before publishing.'})
            item['version'] = secrets.token_hex(12)
            conn.execute('INSERT INTO proposals VALUES(?,?,?)', (key, item['version'], json.dumps(item, ensure_ascii=False)))
            conn.execute('INSERT OR REPLACE INTO heads VALUES(?,?,?)', (key, item['version'], CURRENT))
        reload_items()
        return self.send(201, {'id': key, 'version': item['version'], 'revision': seq})

    def batch(self, raw):
        try:
            payload = json.loads(raw)
            chosen = payload['items']
            value = payload['value']
            note = str(payload.get('text','')).strip()
            actor = str(payload.get('actor','作者'))[:80]
            valid = value in ['accepted','rejected','needs_revision','needs_info','pending'] and isinstance(chosen,list) and 0 < len(chosen) <= len(ITEMS)
            if not valid or len({i['id'] for i in chosen}) != len(chosen):
                return self.send(400, {'error':'批量选择或操作无效'})
            if stale_sources():
                return self.send(409, {'error':'原文或方案已更新，请重新导入'})
            with database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                for choice in chosen:
                    item = ACTIVE.get(choice['id'])
                    last = conn.execute('SELECT MAX(seq) FROM events WHERE entity=?',(choice['id'],)).fetchone()[0] or 0
                    if not item or item.get('review_deferred') or choice['version'] != item['version'] or choice['revision'] != last:
                        return self.send(409, {'error':'所选项有更新，全部未提交；请刷新后重试'})
                for choice in chosen:
                    conn.execute('INSERT INTO events(entity,version,action,value,text,actor,time) VALUES(?,?,?,?,?,?,?)',(choice['id'],choice['version'],'decision',value,note,actor,datetime.now(timezone.utc).isoformat()))
            return self.send(200,state())
        except (ValueError,TypeError,KeyError):
            return self.send(400,{'error':'批量请求格式无效'})


httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
print(f'Revision Desk {ORIGIN}', flush=True)
httpd.serve_forever()
