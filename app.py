from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from markupsafe import Markup, escape
from dotenv import load_dotenv
import os, re, sqlite3

load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp','pdf','doc','docx','ppt','pptx','xls','xlsx','zip'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY','dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, '1-7app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ADMIN_CODE = os.environ.get('ADMIN_CODE','1234')

db = SQLAlchemy(app)

# ---------- Models ----------
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    category = db.Column(db.String(20), nullable=False)  # assessment | assignment
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed = db.Column(db.Boolean, default=False)
    color = db.Column(db.String(20), default='#8b5cf6')
    attachment_path = db.Column(db.String(300))

class Supply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_text = db.Column(db.String(200), nullable=False)

class Config(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(1000))

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    tags = db.Column(db.String(200), default='')
    pinned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    attachment_path = db.Column(db.String(300))

# ---------- Helpers ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_dirs():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def _alter_table_if_missing(table, column, ddl):
    """간단 스키마 보강(컬럼 없으면 추가)."""
    conn = sqlite3.connect(os.path.join(BASE_DIR, '1-7app.db'))
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        conn.commit()
    conn.close()

def get_config(key, default=None):
    cfg = db.session.get(Config, key)
    return cfg.value if cfg else default

def set_config(key, value):
    cfg = db.session.get(Config, key)
    if not cfg:
        cfg = Config(key=key, value=value); db.session.add(cfg)
    else:
        cfg.value = value
    db.session.commit()

def is_admin():
    return session.get('is_admin', False)

@app.context_processor
def inject_globals():
    return dict(is_admin=is_admin())

_link_re = re.compile(r'(https?://[^\s<]+)')
def linkify(text:str)->Markup:
    if not text: return Markup("")
    safe = escape(text)
    safe = _link_re.sub(r'<a href="\\1" target="_blank" rel="noopener noreferrer">\\1</a>', safe)
    return Markup(safe.replace('\r\n','\n').replace('\r','\n').replace('\n','<br>'))

with app.app_context():
    db.create_all(); ensure_dirs()
    try:
        _alter_table_if_missing('task','created_at',"created_at TIMESTAMP")
        _alter_table_if_missing('task','completed',"completed BOOLEAN DEFAULT 0")
        _alter_table_if_missing('task','color',"color VARCHAR(20) DEFAULT '#8b5cf6'")
        _alter_table_if_missing('task','attachment_path',"attachment_path VARCHAR(300)")
        _alter_table_if_missing('note','tags',"tags VARCHAR(200) DEFAULT ''")
        _alter_table_if_missing('note','pinned',"pinned BOOLEAN DEFAULT 0")
        _alter_table_if_missing('note','attachment_path',"attachment_path VARCHAR(300)")
    except Exception as e:
        print('Schema upgrade error:', e)

# ---------- Routes ----------
@app.route('/')
def index():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    today_count = Task.query.filter(Task.due_date==today).count()
    tomorrow_count = Task.query.filter(Task.due_date==tomorrow).count()
    upcoming = Task.query.filter(Task.due_date>=today).order_by(Task.due_date.asc()).limit(5).all()
    notes = Note.query.filter_by(pinned=True).order_by(Note.created_at.desc()).limit(3).all()
    kst = timedelta(hours=9)
    for n in notes:
        n.kst_str = (n.created_at + kst).strftime('%Y-%m-%d %H:%M')
    supplies_count = Supply.query.count()
    return render_template('index.html',
        today_count=today_count, tomorrow_count=tomorrow_count,
        upcoming=upcoming, pinned_notes=notes, supplies_count=supplies_count)

# --- 관리자 코드 로그인(간단 모드) ---
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method=='POST':
        if request.form.get('code','') == ADMIN_CODE:
            session['is_admin'] = True; flash('관리자 모드 ON','success'); return redirect(url_for('index'))
        flash('관리자 코드가 올바르지 않습니다.','error'); return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None); flash('관리자 모드 OFF','success'); return redirect(url_for('index'))

# --- 과제/수행 (정렬/필터/완료/첨부, 연도 2025 고정: 월/일만 입력) ---
@app.route('/tasks')
def tasks():
    sort = request.args.get('sort','due_asc')
    when = request.args.get('when','')
    qs = Task.query
    today = date.today()
    if when=='today': qs = qs.filter(Task.due_date==today)
    elif when=='tomorrow': qs = qs.filter(Task.due_date==today+timedelta(days=1))
    elif when=='upcoming': qs = qs.filter(Task.due_date>=today)
    if sort=='due_desc': qs = qs.order_by(Task.due_date.desc())
    elif sort=='created_desc': qs = qs.order_by(Task.created_at.desc())
    elif sort=='created_asc': qs = qs.order_by(Task.created_at.asc())
    elif sort=='category': qs = qs.order_by(Task.category.asc(), Task.due_date.asc())
    else: qs = qs.order_by(Task.due_date.asc())
    return render_template('tasks.html',
        tasks=qs.all(), sort=sort, when=when,
        default_month=today.month, default_day=today.day)

@app.route('/tasks/add', methods=['POST'])
def add_task():
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('tasks'))
    title = request.form.get('title','').strip()
    category = request.form.get('category','assignment')
    color = request.form.get('color','#8b5cf6')
    month = request.form.get('month'); day = request.form.get('day')
    file = request.files.get('attachment')
    if not title or not month or not day or category not in ('assignment','assessment'):
        flash('입력 오류','error'); return redirect(url_for('tasks'))
    try:
        due_date = date(2025, int(month), int(day))  # 연도 2025 고정
    except Exception:
        flash('월/일 형식 오류','error'); return redirect(url_for('tasks'))
    attach_path = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'tasks'); os.makedirs(save_dir, exist_ok=True)
        file.save(os.path.join(save_dir, filename))
        attach_path = os.path.join('uploads','tasks',filename)
    db.session.add(Task(title=title, due_date=due_date, category=category, color=color, attachment_path=attach_path))
    db.session.commit(); flash('추가됨 (연도 2025 고정)','success'); return redirect(url_for('tasks'))

@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
def complete_task(task_id):
    t = db.session.get(Task, task_id)
    if not t: return ('',404)
    t.completed = not t.completed; db.session.commit(); return ('',204)

@app.route('/tasks/delete/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('tasks'))
    t = db.session.get(Task, task_id)
    if t: db.session.delete(t); db.session.commit(); flash('삭제됨','success')
    return redirect(url_for('tasks'))

# --- 준비물 ---
@app.route('/supplies')
def supplies():
    items = Supply.query.order_by(Supply.id.asc()).all()
    return render_template('supplies.html', items=items)

@app.route('/supplies/add', methods=['POST'])
def add_supply():
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('supplies'))
    text = request.form.get('item_text','').strip()
    if not text: flash('내용 필요','error'); return redirect(url_for('supplies'))
    db.session.add(Supply(item_text=text)); db.session.commit(); flash('추가됨','success')
    return redirect(url_for('supplies'))

@app.route('/supplies/delete/<int:item_id>', methods=['POST'])
def delete_supply(item_id):
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('supplies'))
    it = db.session.get(Supply, item_id)
    if it: db.session.delete(it); db.session.commit(); flash('삭제됨','success')
    return redirect(url_for('supplies'))

# --- 시간표 ---
@app.route('/timetable', methods=['GET','POST'])
def timetable():
    if request.method=='POST':
        if not is_admin():
            flash('관리자만 가능','error'); return redirect(url_for('timetable'))
        f = request.files.get('image')
        if f and allowed_file(f.filename):
            filename = secure_filename(f.filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            set_config('timetable_image', os.path.join('uploads', filename))
            flash('업데이트됨','success'); return redirect(url_for('timetable'))
        flash('이미지 파일을 선택하세요.','error'); return redirect(url_for('timetable'))
    image_path = get_config('timetable_image')
    return render_template('timetable.html', image_path=image_path)

# --- 공지(메모) : 태그/핀 + 파일 첨부 ---
@app.route('/misc')
def misc():
    items = Note.query.order_by(Note.pinned.desc(), Note.created_at.desc()).all()
    kst = timedelta(hours=9)
    for n in items:
        n.kst_str = (n.created_at + kst).strftime('%Y-%m-%d %H:%M')
        n.html = linkify(n.content)
    return render_template('misc.html', notes=items)

@app.route('/misc/add', methods=['POST'])
def add_note():
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('misc'))
    tags = request.form.get('tags','').strip()
    content = request.form.get('content','').strip()
    f = request.files.get('attachment')
    if not content and not f:
        flash('내용이나 파일 중 하나는 필요합니다.','error'); return redirect(url_for('misc'))
    attach_path = None
    if f and allowed_file(f.filename):
        filename = secure_filename(f.filename)
        save_dir = os.path.join(app.config['UPLOAD_FOLDER'],'misc'); os.makedirs(save_dir, exist_ok=True)
        f.save(os.path.join(save_dir, filename))
        attach_path = os.path.join('uploads','misc',filename)
    db.session.add(Note(content=content or '', tags=tags, attachment_path=attach_path))
    db.session.commit(); flash('등록됨','success'); return redirect(url_for('misc'))

@app.route('/misc/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('misc'))
    n = db.session.get(Note, note_id)
    if n: db.session.delete(n); db.session.commit(); flash('삭제됨','success')
    return redirect(url_for('misc'))

@app.route('/misc/pin/<int:note_id>', methods=['POST'])
def toggle_pin(note_id):
    if not is_admin():
        flash('관리자만 가능','error'); return redirect(url_for('misc'))
    n = db.session.get(Note, note_id)
    if n: n.pinned = not n.pinned; db.session.commit()
    return redirect(url_for('misc'))

# --- ICS 캘린더 피드 (Google Calendar 구독용) ---
@app.route('/calendar.ics')
def calendar_ics():
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//1-7 ClassHub//EN']
    for t in Task.query.order_by(Task.due_date.asc()).all():
        dt = t.due_date.strftime('%Y%m%d')
        lines += ['BEGIN:VEVENT',
                  f'UID:task-{t.id}@1-7',
                  f'SUMMARY:{t.title}',
                  f'DTSTART;VALUE=DATE:{dt}',
                  f'DTEND;VALUE=DATE:{dt}',
                  f'CATEGORIES:{t.category}',
                  'END:VEVENT']
    lines.append('END:VCALENDAR')
    return Response('\r\n'.join(lines), mimetype='text/calendar')

# ---------- Run ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all(); ensure_dirs()
    app.run(host='0.0.0.0', port=5000, debug=True)
