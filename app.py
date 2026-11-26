# ===== Agent Şəbəkəsi — server (Python Flask + PostgreSQL), Railway üçün =====
import os, json, time, hmac
from flask import Flask, request, session, jsonify, send_from_directory
from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-ozunuzunkunu-yazin')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('RAILWAY_ENVIRONMENT') is not None)
if os.environ.get('RAILWAY_ENVIRONMENT'):
    app.config['PREFERRED_URL_SCHEME'] = 'https'

DB = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(HERE, 'test.db'))
if DB.startswith('postgres://'):
    DB = 'postgresql://' + DB[len('postgres://'):]
eng = create_engine(DB, pool_pre_ping=True, future=True)
with eng.begin() as c:
    c.execute(text("CREATE TABLE IF NOT EXISTS an_rec (kind VARCHAR(8) NOT NULL, id INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (kind, id))"))
    c.execute(text("CREATE TABLE IF NOT EXISTS an_meta (k VARCHAR(32) NOT NULL PRIMARY KEY, v BIGINT NOT NULL DEFAULT 0)"))

KINDS = {'users': 'u', 'docs': 'd', 'files': 'f', 'log': 'l'}

class Fail(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.code = code

def ok(d): return jsonify(d)
def err(m, code=400): r = jsonify({'ok': False, 'err': m}); r.status_code = code; return r
def sw(s, p): return bool(p) and str(s or '').startswith(p)
def i(x):
    try: return int(x)
    except Exception: return 0
def U(s): return str(s or '').strip().upper()

def meta(c, k):
    v = c.execute(text('SELECT v FROM an_meta WHERE k=:k'), {'k': k}).scalar()
    return int(v) if v is not None else 0
def set_meta(c, k, v):
    c.execute(text('INSERT INTO an_meta (k, v) VALUES (:k, :v) ON CONFLICT (k) DO UPDATE SET v=excluded.v'), {'k': k, 'v': int(v)})
def rows(c, kind):
    return {int(r[0]): json.loads(r[1]) for r in c.execute(text('SELECT id, data FROM an_rec WHERE kind=:k ORDER BY id'), {'k': kind})}
def put(c, kind, rec):
    c.execute(text('INSERT INTO an_rec (kind, id, data) VALUES (:k, :i, :d) ON CONFLICT (kind, id) DO UPDATE SET data=excluded.data'),
              {'k': kind, 'i': int(rec['id']), 'd': json.dumps(rec, ensure_ascii=False)})
def delete(c, kind, id_):
    c.execute(text('DELETE FROM an_rec WHERE kind=:k AND id=:i'), {'k': kind, 'i': int(id_)})
def has_users(c):
    return c.execute(text("SELECT 1 FROM an_rec WHERE kind='users' LIMIT 1")).first() is not None
def isF(u): return bool(u) and u.get('role') == 'founder'
def me(c):
    rk = os.environ.get('ROBOT_KEY', '')
    if rk and hmac.compare_digest(request.headers.get('X-Robot-Key', ''), rk):  # İSB robotu: qurucu adından
        return next((u for u in rows(c, 'users').values() if u.get('role') == 'founder' and u.get('active')), None)
    uid = session.get('uid')
    if not uid: return None
    u = rows(c, 'users').get(int(uid))
    return u if u and u.get('active') else None

def state_for(c, V):
    base = {'ok': True, 'hasUsers': has_users(c), 'rev': meta(c, 'rev'), 'uid': int(V['id']) if V else 0}
    if not V:
        base['state'] = {'users': [], 'docs': [], 'files': [], 'log': [], 'seq': {'u': 0, 'd': 0, 'f': 0, 'l': 0}}
        return base
    users, docs, files, log = rows(c, 'users'), rows(c, 'docs'), rows(c, 'files'), rows(c, 'log')
    vid = int(V['id']); anc = [i(x) for x in str(V.get('path', '')).split('/') if x]
    UU = []
    for id_, u in users.items():
        u = dict(u)
        if id_ != vid: u.pop('pw', None)
        if isF(V) or sw(u.get('path'), V.get('path')): UU.append(u)
        elif id_ in anc:
            UU.append({'id': id_, 'name': u.get('name'), 'role': u.get('role'), 'path': u.get('path'), 'depth': u.get('depth', 1),
                       'parent_id': u.get('parent_id'), 'active': True, 'code': ''})
    D = [d for d in docs.values() if isF(V) or i(d.get('user_id')) == vid]
    ids = {i(d['id']) for d in D}
    base['state'] = {'users': UU, 'docs': D,
                     'files': [f for f in files.values() if i(f.get('document_id')) in ids],
                     'log': [l for l in log.values() if i(l.get('document_id')) in ids],
                     'founderPreset': meta(c, 'founderPreset'),
                     'seq': {k: meta(c, 'seq_' + k) for k in KINDS.values()}}
    return base

@app.route('/')
@app.route('/index.html')
def index():
    r = send_from_directory(HERE, 'index.html'); r.headers['Cache-Control'] = 'no-cache'; return r

@app.route('/api.php', methods=['GET', 'POST'])
def api():
    a = request.args.get('a', '')
    inp = request.get_json(silent=True) or {}
    try:
        if a == 'state':
            with eng.connect() as c:
                V = me(c)
                if 'rev' in request.args and i(request.args['rev']) == meta(c, 'rev') and V and i(request.args.get('uid')) == int(V['id']):
                    return ok({'ok': True, 'same': True})
                return ok(state_for(c, V))

        if a == 'login':
            code, pw = U(inp.get('code')), str(inp.get('pw') or '')
            with eng.connect() as c:
                for u in rows(c, 'users').values():
                    if code and (U(u.get('code')) == code or U(u.get('username')) == code) and u.get('active') \
                            and hmac.compare_digest(str(u.get('pw') or ''), pw):
                        session.clear(); session['uid'] = int(u['id']); session.permanent = False
                        return ok(state_for(c, u))
            time.sleep(0.8); return err('Kod və ya parol səhvdir.', 401)

        if a == 'logout':
            session.clear(); return ok({'ok': True})

        if a == 'nid':
            k = request.args.get('k', '')
            if k not in KINDS.values(): return err('Səhv sorğu')
            with eng.begin() as c:
                if not me(c) and has_users(c): return err('Daxil olun', 401)
                n = c.execute(text('INSERT INTO an_meta (k, v) VALUES (:k, 1) ON CONFLICT (k) DO UPDATE SET v=an_meta.v+1 RETURNING v'), {'k': 'seq_' + k}).scalar()
            return ok({'ok': True, 'id': int(n)})

        if a == 'sync':
            with eng.begin() as c:
                sync(c, inp)
            with eng.connect() as c:
                return ok(state_for(c, me(c)))
    except Fail as e:
        return err(str(e), e.code)
    return err('Naməlum sorğu', 404)

def sync(c, inp):
    V = me(c); boot = False
    if not V:
        if has_users(c): raise Fail('Sessiya bitib — yenidən daxil olun.', 401)
        boot = True
    ch, dl, mt = inp.get('ch') or {}, inp.get('del') or {}, inp.get('meta') or {}
    users, docs, files, log = rows(c, 'users'), rows(c, 'docs'), rows(c, 'files'), rows(c, 'log')
    # --- istifadəçilər ---
    for r in ch.get('users') or []:
        id_ = i(r.get('id'))
        if id_ <= 0: raise Fail('Səhv istifadəçi')
        old = users.get(id_)
        if boot:
            if old or len(ch.get('users')) != 1 or r.get('role') != 'founder': raise Fail('İcazə yoxdur')
            r.update(path=f'/{id_}/', parent_id=None, depth=1, active=True)
        elif old:
            if not isF(V) and not sw(old.get('path'), V.get('path')): raise Fail('Bu istifadəçini dəyişməyə icazə yoxdur')
            if not r.get('pw'): r['pw'] = old.get('pw', '')
            for f in ('role', 'path', 'parent_id', 'depth'): r[f] = old.get(f)
            if id_ == int(V['id']): r['active'] = old.get('active', True)
        else:
            p = users.get(i(r.get('parent_id')))
            if not p or (not isF(V) and not sw(p.get('path'), V.get('path'))): raise Fail('İcazə yoxdur')
            if r.get('role') == 'founder': raise Fail('İcazə yoxdur')
            r['path'] = p['path'] + f'{id_}/'; r['depth'] = i(p.get('depth') or 1) + 1
        cd = U(r.get('code'))
        if cd and any(oid != id_ and (U(o.get('code')) == cd or U(o.get('username')) == cd) for oid, o in users.items()):
            raise Fail('Bu kod artıq başqasındadır.')
        users[id_] = r; put(c, 'users', r)
        if boot: session.clear(); session['uid'] = id_; V = r
    if not V: raise Fail('Daxil olun', 401)
    vid = int(V['id'])
    own = lambda d: bool(d) and (isF(V) or i(d.get('user_id')) == vid)
    # --- sənədlər ---
    for r in ch.get('docs') or []:
        id_ = i(r.get('id'))
        if id_ <= 0: raise Fail('Səhv sənəd')
        old = docs.get(id_)
        if not old:
            if i(r.get('user_id')) != vid: raise Fail('İcazə yoxdur')
            r['path'] = V['path']
            if not isF(V): r.update(status='yeni', review_note='', isb_code=None, reviewed_by=None)
        else:
            if not own(old): raise Fail('Bu sənədi dəyişməyə icazə yoxdur')
            if not isF(V):
                r['user_id'] = old['user_id']; r['path'] = old.get('path')
                if r.get('status') not in (old.get('status'), 'yeni'): r['status'] = old.get('status')
                if (r.get('review_note') or '') not in ('', old.get('review_note') or ''): r['review_note'] = old.get('review_note') or ''
                r['isb_code'] = old.get('isb_code'); r['reviewed_by'] = old.get('reviewed_by')
        docs[id_] = r; put(c, 'docs', r)
    # --- fayllar ---
    for r in ch.get('files') or []:
        id_ = i(r.get('id'))
        if id_ <= 0 or not own(docs.get(i(r.get('document_id')))): raise Fail('Fayl üçün icazə yoxdur')
        if len(str(r.get('data') or '')) > 6_000_000: raise Fail('Fayl çox böyükdür')
        files[id_] = r; put(c, 'files', r)
    # --- jurnal (yalnız əlavə) ---
    for r in ch.get('log') or []:
        id_ = i(r.get('id'))
        if id_ <= 0 or (id_ in log and not isF(V)) or not own(docs.get(i(r.get('document_id')))): continue
        if not isF(V): r['user_id'] = vid
        log[id_] = r; put(c, 'log', r)
    # --- silinmələr ---
    for id_ in dl.get('docs') or []:
        id_ = i(id_); d = docs.get(id_)
        if not d: continue
        if not own(d): raise Fail('Silməyə icazə yoxdur')
        delete(c, 'docs', id_)
        for fid, f in files.items():
            if i(f.get('document_id')) == id_: delete(c, 'files', fid)
        for lid, l in log.items():
            if i(l.get('document_id')) == id_: delete(c, 'log', lid)
    for id_ in dl.get('files') or []:
        f = files.get(i(id_))
        if f and own(docs.get(i(f.get('document_id')))): delete(c, 'files', i(id_))
    for id_ in dl.get('log') or []:
        if i(id_) in log and isF(V): delete(c, 'log', i(id_))
    for id_ in dl.get('users') or []:
        t = users.get(i(id_))
        if not t or i(id_) == vid: continue
        if not isF(V) and not sw(t.get('path'), V.get('path')): raise Fail('Silməyə icazə yoxdur')
        if any(i(o.get('parent_id')) == i(id_) for o in users.values() if i(o['id']) not in [i(x) for x in dl.get('users') or []]): raise Fail('Əvvəlcə altdakı agentləri silin')
        delete(c, 'users', i(id_))
    if isF(V) and 'founderPreset' in mt: set_meta(c, 'founderPreset', i(mt['founderPreset']))
    for kind, k in KINDS.items():
        mx = c.execute(text('SELECT MAX(id) FROM an_rec WHERE kind=:k'), {'k': kind}).scalar() or 0
        if int(mx) > meta(c, 'seq_' + k): set_meta(c, 'seq_' + k, int(mx))
    set_meta(c, 'rev', meta(c, 'rev') + 1)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
