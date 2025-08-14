
from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, jsonify, send_file, Response
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user, current_user,
    login_required, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from markupsafe import Markup, escape
from dotenv import load_dotenv
from pywebpush import webpush, WebPushException
from apscheduler.schedulers.background import BackgroundScheduler
import os, re, csv, io, json, sqlite3

# ====== Setup ======
load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp','pdf','doc','docx','ppt','pptx','xls','xlsx','zip'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, '1-7app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ADMIN_CODE = os.environ.get('ADMIN_CODE', '1234')

VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIM_EMAIL = os.environ.get('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ====== Models ======
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='student')  # student | admin
    notifications_enabled = db.Column(db.Boolean, default=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_admin(self):
        return self.role == 'admin'


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    category = db.Column(db.String(20), nullable=False)  # assessment | assignment
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed = db.Column(db.Boolean, default=False)
    color = db.Column(db.String(20), default='#2563eb')
    attachment_path = db.Column(db.String(300))
    gcal_added = db.Column(db.Boolean, default=False)


class Supply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_text = db.Column(db.String(200), nullable=False)


class Config(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(1000))


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    tags = db.Column(db.String(200), default='')      # "#태그1,#태그2"
    pinned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


# ====== Helpers ======
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_config(key, default=None):
    c = db.session.get(Config, key)
    return c.value if c else default

def set_config(key, value):
    c = db.session.get(Config, key)
    if not c:
        c = Config(key=key, value=value)
        db.session.add(c)
    else:
        c.value = value
    db.session.commit()

def is_admin():
    return (current_user.is_authenticated and current_user.is_admin) or session.get('is_admin', False)

@app.context_processor
def inject_globals():
    return dict(is_admin=is_admin(), vapid_public_key=VAPID_PUBLIC_KEY)

_link_re = re.compile(r'(https?://[^\s<]+)')
def linkify(text: str) -> Markup:
    if not text:
        return Markup("")
    safe = escape(text)
    safe = _link_re.sub(r'<a href="\\1" target="_blank" rel="noopener noreferrer">\\1</a>', safe)
    safe = safe.replace('\\r\\n','\\n').replace('\\r','\\n').replace('\\n','<br>')
    return Markup(safe)

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

def ensure_dirs():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def _alter_table_if_missing(table, column, ddl):
    conn = sqlite3.connect(os.path.join(BASE_DIR, '1-7app.db'))
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        conn.commit()
    conn.close()

with app.app_context():
    db.create_all()
    ensure_dirs()
    # Simple schema upgrades
    try:
        _alter_table_if_missing('task', 'created_at', "created_at TIMESTAMP")
        _alter_table_if_missing('task', 'completed', "completed BOOLEAN DEFAULT 0")
        _alter_table_if_missing('task', 'color', "color VARCHAR(20) DEFAULT '#2563eb'")
        _alter_table_if_missing('task', 'attachment_path', "attachment_path VARCHAR(300)")
        _alter_table_if_missing('task', 'gcal_added', "gcal_added BOOLEAN DEFAULT 0")
        _alter_table_if_missing('note', 'tags', "tags VARCHAR(200) DEFAULT ''")
        _alter_table_if_missing('note', 'pinned', "pinned BOOLEAN DEFAULT 0")
        _alter_table_if_missing('user','notifications_enabled',"notifications_enabled BOOLEAN DEFAULT 1") 
    except Exception as e:
        print('Migration warn:', e)
    # Create default admin if none
    if not User.query.filter_by(role='admin').first():
        u = User(username='admin', role='admin')
        u.set_password(ADMIN_CODE)
        db.session.add(u); db.session.commit()

# ====== Routes ======
@app.route('/')
def index():
    today = date.today()
    upcoming = Task.query.filter(Task.due_date >= today).count()
    supplies_count = Supply.query.count()
    notes_count = Note.query.count()
    return render_template('index.html',
                           upcoming_count=upcoming,
                           supplies_count=supplies_count,
                           notes_count=notes_count)
# --- Unsubscribe: 브라우저 구독 해제 + 서버 DB 삭제 ---
@app.route('/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '')
    # 1) 브라우저 쪽은 JS에서 unsubscribe() 실행
    # 2) 서버 DB에서 해당 endpoint 삭제
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint).delete()
        db.session.commit()
    # 로그인 사용자는 “알림 끄기” 토글 시 내 구독 전부 날리기 옵션
    if current_user.is_authenticated and data.get('all_for_user'):
        PushSubscription.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
    return ('', 204)

# --- 사용자 설정: 알림 On/Off 토글 ---
@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    if request.method == 'POST':
        enable = request.form.get('notifications_enabled') == 'on'
        current_user.notifications_enabled = enable
        db.session.commit()
        # 끈 경우: 내 구독 전부 정리(원하면 유지 가능하지만 깔끔하게 삭제 권장)
        if not enable:
            PushSubscription.query.filter_by(user_id=current_user.id).delete()
            db.session.commit()
        flash('알림 설정이 저장되었습니다.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', notifications_enabled=current_user.notifications_enabled)

# --- Auth ---
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if not username or not password:
            flash('입력 누락', 'error'); return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('이미 존재하는 아이디', 'error'); return redirect(url_for('register'))
        u = User(username=username, role='student'); u.set_password(password)
        db.session.add(u); db.session.commit()
        flash('회원가입 완료', 'success'); return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        admin_code = request.form.get('admin_code','').strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if admin_code and admin_code == ADMIN_CODE:
                user.role = 'admin'; db.session.commit(); session['is_admin'] = True
            login_user(user); flash('로그인 성공','success'); return redirect(url_for('index'))
        flash('로그인 실패','error'); return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user(); session.pop('is_admin', None)
    flash('로그아웃 되었습니다.','success'); return redirect(url_for('index'))

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('code','') == ADMIN_CODE:
            session['is_admin'] = True; flash('관리자 모드 ON','success'); return redirect(url_for('index'))
        flash('코드 오류','error'); return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None); flash('관리자 모드 OFF','success'); return redirect(url_for('index'))

# --- Tasks ---
@app.route('/tasks')
def tasks():
    q = request.args.get('q','').strip()
    sort = request.args.get('sort','due_asc')
    cat = request.args.get('cat','')
    due = request.args.get('due','')

    query = Task.query

    # search
    if q:
        query = query.filter(Task.title.contains(q))

    # category filter
    if cat in ('assessment','assignment'):
        query = query.filter_by(category=cat)

    # due filter
    today = date.today()
    if due == 'today':
        query = query.filter(Task.due_date == today)
    elif due == 'tomorrow':
        query = query.filter(Task.due_date == today + timedelta(days=1))

    # sort
    if sort == 'due_desc':
        query = query.order_by(Task.due_date.desc())
    elif sort == 'created_desc':
        query = query.order_by(Task.created_at.desc())
    elif sort == 'created_asc':
        query = query.order_by(Task.created_at.asc())
    elif sort == 'category':
        query = query.order_by(Task.category.asc(), Task.due_date.asc())
    else:
        query = query.order_by(Task.due_date.asc())

    items = query.all()
    return render_template('tasks.html', items=items, sort=sort, cat=cat, due=due)

@app.route('/tasks/add', methods=['POST'])
def add_task():
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('tasks'))
    title = request.form.get('title','').strip()
    category = request.form.get('category','assignment')
    due_str = request.form.get('due_date','')
    color = request.form.get('color','#2563eb')
    if not title or category not in ('assessment','assignment') or not due_str:
        flash('입력 오류','error'); return redirect(url_for('tasks'))
    try:
        y,m,d = due_str.split('-'); due = date(int(y), int(m), int(d))
    except:
        flash('마감일 형식 오류 YYYY-MM-DD','error'); return redirect(url_for('tasks'))
    # upload
    attachment_path = None
    f = request.files.get('attachment')
    if f and allowed_file(f.filename):
        filename = secure_filename(f.filename)
        save_dir = os.path.join(UPLOAD_FOLDER, datetime.now().strftime('%Y%m%d'))
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        f.save(save_path)
        attachment_path = save_path.replace('\\','/')
    t = Task(title=title, category=category, due_date=due, color=color, attachment_path=attachment_path)
    db.session.add(t); db.session.commit()
    flash('추가 완료','success'); return redirect(url_for('tasks'))

@app.route('/tasks/delete/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('tasks'))
    t = db.session.get(Task, task_id)
    if t: db.session.delete(t); db.session.commit()
    return redirect(url_for('tasks'))

@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
def toggle_complete(task_id):
    t = db.session.get(Task, task_id)
    if not t: return redirect(url_for('tasks'))
    t.completed = not t.completed; db.session.commit()
    nxt = request.form.get('next') or url_for('tasks')
    return redirect(nxt)

@app.route('/tasks/<int:task_id>/gcal')
def task_gcal(task_id):
    t = db.session.get(Task, task_id)
    if not t: return redirect(url_for('tasks'))
    # Google Calendar quick add link
    start = datetime.combine(t.due_date, datetime.min.time()).strftime('%Y%m%d')
    end = datetime.combine(t.due_date, datetime.min.time()).strftime('%Y%m%d')
    text = t.title
    details = f"카테고리: {t.category}"
    url = f"https://calendar.google.com/calendar/u/0/r/eventedit?text={text}&dates={start}/{end}&details={details}"
    return redirect(url)

# ICS feed
@app.route('/tasks.ics')
def tasks_ics():
    items = Task.query.order_by(Task.due_date.asc()).all()
    lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//ClassHub//KST//KO"]
    for t in items:
        dt = datetime.combine(t.due_date, datetime.min.time())
        dt_utc = (dt - timedelta(hours=9)).strftime('%Y%m%dT%H%M%SZ')  # from KST to UTC
        lines += [
            "BEGIN:VEVENT",
            f"UID:task-{t.id}@classhub",
            f"DTSTAMP:{dt_utc}",
            f"DTSTART:{dt_utc}",
            f"SUMMARY:{t.title}",
            f"DESCRIPTION:카테고리:{t.category}",
            "END:VEVENT"
        ]
    lines.append("END:VCALENDAR")
    return Response("\\r\\n".join(lines), mimetype="text/calendar")

# CSV Export
@app.route('/export/csv')
def export_csv():
    items = Task.query.order_by(Task.due_date.asc()).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id","title","category","due_date","created_at","completed","color","attachment_path"])
    for t in items:
        w.writerow([t.id,t.title,t.category,t.due_date.isoformat(),(t.created_at or datetime.utcnow()).isoformat(),int(t.completed),t.color or "",t.attachment_path or ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=tasks.csv"})

# --- Supplies ---
@app.route('/supplies')
def supplies():
    items = Supply.query.order_by(Supply.id.asc()).all()
    return render_template('supplies.html', items=items)

@app.route('/supplies/add', methods=['POST'])
def add_supply():
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('supplies'))
    text = request.form.get('item_text','').strip()
    if not text: flash('입력 누락','error'); return redirect(url_for('supplies'))
    db.session.add(Supply(item_text=text)); db.session.commit()
    flash('추가 완료','success'); return redirect(url_for('supplies'))

@app.route('/supplies/delete/<int:item_id>', methods=['POST'])
def delete_supply(item_id):
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('supplies'))
    it = db.session.get(Supply, item_id); 
    if it: db.session.delete(it); db.session.commit()
    return redirect(url_for('supplies'))

# --- Timetable ---
@app.route('/timetable', methods=['GET','POST'])
def timetable():
    if request.method == 'POST':
        if not is_admin():
            flash('관리자 전용','error'); return redirect(url_for('timetable'))
        file = request.files.get('image')
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            file.save(save_path)
            set_config('timetable_image', os.path.join('uploads', filename))
            flash('업데이트 완료','success')
        else:
            flash('이미지 파일을 선택하세요','error')
        return redirect(url_for('timetable'))
    image_path = get_config('timetable_image')
    return render_template('timetable.html', image_path=image_path)

# --- Misc ---
@app.route('/misc')
def misc():
    q = request.args.get('q','').strip()
    tag = request.args.get('tag','').strip().lstrip('#')
    query = Note.query
    if q:
        query = query.filter(Note.content.contains(q))
    if tag:
        query = query.filter(Note.tags.contains(tag))
    notes_all = query.order_by(Note.created_at.desc()).all()
    pinned = [n for n in notes_all if n.pinned]
    notes = [n for n in notes_all if not n.pinned]
    kst = timedelta(hours=9)
    for n in notes_all:
        n.kst_str = (n.created_at + kst).strftime('%Y-%m-%d')
        # extract tags (simple)
        tags = [t.strip('#, ') for t in re.findall(r'(#[0-9A-Za-z가-힣_]+)', n.content)]
        n.tags = ",".join(sorted(set([t.lstrip('#') for t in tags])))
        n.html = linkify(n.content)
    return render_template('misc.html', notes=notes, pinned=pinned)

@app.route('/misc/add', methods=['POST'])
def add_note():
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('misc'))
    content = request.form.get('content','').strip()
    if not content: flash('입력 누락','error'); return redirect(url_for('misc'))
    tags = ",".join([t.lstrip('#') for t in re.findall(r'(#[0-9A-Za-z가-힣_]+)', content)])
    db.session.add(Note(content=content, tags=tags)); db.session.commit()
    flash('등록 완료','success'); return redirect(url_for('misc'))

@app.route('/misc/pin/<int:note_id>', methods=['POST'])
def toggle_pin(note_id):
    n = db.session.get(Note, note_id)
    if n: n.pinned = not n.pinned; db.session.commit()
    return redirect(url_for('misc'))

@app.route('/misc/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    if not is_admin():
        flash('관리자 전용','error'); return redirect(url_for('misc'))
    n = db.session.get(Note, note_id)
    if n: db.session.delete(n); db.session.commit()
    return redirect(url_for('misc'))

# --- Push ---
@app.route('/push/subscribe', methods=['POST'])
def push_subscribe():
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return ('VAPID 키 미설정', 400)
    data = request.get_json(force=True)
    endpoint = data.get('endpoint')
    keys = data.get('keys',{})
    p256dh = keys.get('p256dh'); auth = keys.get('auth')
    if not endpoint or not p256dh or not auth:
        return ('구독 데이터 오류', 400)
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not sub:
        sub = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth, user_id=current_user.id if current_user.is_authenticated else None)
        db.session.add(sub); db.session.commit()
    return ('ok', 200)

def send_push_all(title, body, url='/tasks'):
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY: return 0
    payload = json.dumps({"title":title,"body":body,"url":url})
    sent = 0
    for s in PushSubscription.query.all():
        try:
            webpush(
                subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL}
            )
            sent += 1
        except WebPushException as e:
            print('Push fail:', e)
    return sent

# --- Daily reminder (cron-like). On Render, call via external cron.
@app.route('/cron/due-reminders')
def cron_due_reminders():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    items = Task.query.filter(Task.due_date == tomorrow, Task.completed == False).all()
    if not items: return 'no due', 200
    titles = ', '.join([i.title for i in items])
    n = send_push_all('내일 마감 알림', titles, url='/tasks?due=tomorrow')
    return f'sent {n}', 200

# --- CLI
@app.cli.command('init-db')
def init_db():
    db.create_all(); print('db ok')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
