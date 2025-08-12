from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import os
import re  # ⬅ 추가: 기존 메모의 시간 꼬리표 제거용

load_dotenv()

# --- Config ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, '1-7app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ADMIN_CODE = os.environ.get('ADMIN_CODE', '1234')  # set in environment for security

db = SQLAlchemy(app)
with app.app_context():
    db.create_all()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Models ---
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    category = db.Column(db.String(20), nullable=False)  # 'assessment' or 'assignment'

class Supply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_text = db.Column(db.String(200), nullable=False)

class Config(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(500), nullable=True)

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Helpers ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_config(key, default=None):
    cfg = db.session.get(Config, key)
    return cfg.value if cfg else default

def set_config(key, value):
    cfg = db.session.get(Config, key)
    if not cfg:
        cfg = Config(key=key, value=value)
        db.session.add(cfg)
    else:
        cfg.value = value
    db.session.commit()

def is_admin():
    return session.get('is_admin', False)

@app.context_processor
def inject_globals():
    return dict(is_admin=is_admin())

# --- Routes ---
@app.route('/')
def index():
    # Count upcoming items for quick badges
    today = date.today()
    upcoming_count = Task.query.filter(Task.due_date >= today).count()
    supplies_count = Supply.query.count()
    notes_count = Note.query.count()
    return render_template('index.html',
                           upcoming_count=upcoming_count,
                           supplies_count=supplies_count,
                           notes_count=notes_count)

# -------- Tasks (수행평가/과제) --------
@app.route('/tasks')
def tasks():
    today = date.today()
    assessments = Task.query.filter_by(category='assessment').order_by(Task.due_date.asc()).all()
    assignments = Task.query.filter_by(category='assignment').order_by(Task.due_date.asc()).all()
    return render_template('tasks.html', assessments=assessments, assignments=assignments, today=today)

@app.route('/tasks/add', methods=['POST'])
def add_task():
    if not is_admin():
        flash('관리자만 추가할 수 있어요.', 'error')
        return redirect(url_for('tasks'))
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'assignment')
    due_date_str = request.form.get('due_date', '')
    if not title or not due_date_str or category not in ('assessment','assignment'):
        flash('모든 항목을 정확히 입력해 주세요.', 'error')
        return redirect(url_for('tasks'))
    try:
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('마감일 형식이 올바르지 않습니다 (YYYY-MM-DD).', 'error')
        return redirect(url_for('tasks'))
    db.session.add(Task(title=title, due_date=due_date, category=category))
    db.session.commit()
    flash('추가되었습니다.', 'success')
    return redirect(url_for('tasks'))


@app.route('/tasks/delete/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    if not is_admin():
        flash('관리자만 삭제할 수 있어요.', 'error')
        return redirect(url_for('tasks'))
    task = db.session.get(Task, task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
        flash('삭제되었습니다.', 'success')
    return redirect(url_for('tasks'))

# -------- Supplies (내일의 준비물) --------
@app.route('/supplies')
def supplies():
    items = Supply.query.order_by(Supply.id.asc()).all()
    return render_template('supplies.html', items=items)

@app.route('/supplies/add', methods=['POST'])
def add_supply():
    if not is_admin():
        flash('관리자만 추가할 수 있어요.', 'error')
        return redirect(url_for('supplies'))
    text = request.form.get('item_text', '').strip()
    if not text:
        flash('내용을 입력해 주세요.', 'error')
        return redirect(url_for('supplies'))
    db.session.add(Supply(item_text=text))
    db.session.commit()
    flash('추가되었습니다.', 'success')
    return redirect(url_for('supplies'))

@app.route('/supplies/delete/<int:item_id>', methods=['POST'])
def delete_supply(item_id):
    if not is_admin():
        flash('관리자만 삭제할 수 있어요.', 'error')
        return redirect(url_for('supplies'))
    item = db.session.get(Supply, item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
        flash('삭제되었습니다.', 'success')
    return redirect(url_for('supplies'))

# -------- Timetable (시간표 이미지) --------
@app.route('/timetable', methods=['GET', 'POST'])
def timetable():
    if request.method == 'POST':
        if not is_admin():
            flash('관리자만 변경할 수 있어요.', 'error')
            return redirect(url_for('timetable'))
        file = request.files.get('image')
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            file.save(save_path)
            set_config('timetable_image', os.path.join('uploads', filename))
            flash('시간표 이미지가 업데이트되었습니다.', 'success')
            return redirect(url_for('timetable'))
        else:
            flash('이미지 파일을 선택해 주세요 (png/jpg/jpeg/gif/webp).', 'error')
            return redirect(url_for('timetable'))
    image_path = get_config('timetable_image')
    if not image_path:
        default_path = os.path.join('uploads', 'timetable.png')
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], 'timetable.png')):
            set_config('timetable_image', default_path)
            image_path = default_path
    return render_template('timetable.html', image_path=image_path)

# -------- Misc (기타: 자유메모) --------
@app.route('/misc', methods=['GET'])
def misc():
    notes = Note.query.order_by(Note.created_at.desc()).all()
    kst_offset = timedelta(hours=9)
    for n in notes:
        n.kst_str = (n.created_at + kst_offset).strftime('%Y-%m-%d')  # 날짜만
    return render_template('misc.html', notes=notes)

@app.route('/misc/add', methods=['POST'])
def add_note():
    if not is_admin():
        flash('관리자만 작성할 수 있어요.', 'error')
        return redirect(url_for('misc'))
    content = request.form.get('content', '').strip()
    if not content:
        flash('내용을 입력해 주세요.', 'error')
        return redirect(url_for('misc'))

    # ✅ 시간대 붙이지 않고 내용만 저장
    db.session.add(Note(content=content))
    db.session.commit()
    flash('메모가 등록되었습니다.', 'success')
    return redirect(url_for('misc'))

@app.route('/misc/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    if not is_admin():
        flash('관리자만 삭제할 수 있어요.', 'error')
        return redirect(url_for('misc'))
    note = db.session.get(Note, note_id)
    if note:
        db.session.delete(note)
        db.session.commit()
        flash('삭제되었습니다.', 'success')
    return redirect(url_for('misc'))

# -------- Admin --------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        code = request.form.get('code', '')
        if code == ADMIN_CODE:
            session['is_admin'] = True
            flash('관리자 모드로 전환되었습니다.', 'success')
            return redirect(url_for('index'))
        else:
            flash('관리자 코드가 올바르지 않습니다.', 'error')
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    flash('관리자 모드가 해제되었습니다.', 'success')
    return redirect(url_for('index'))

# --- CLI to init DB ---
@app.cli.command('init-db')
def init_db():
    db.create_all()
    print('Initialized the database.')

# --- 앱 시작 시: 기존 메모의 "(MM-DD HH:MM)" 꼬리표 제거 (한 번 실행돼도 안전)
def _strip_old_note_timestamps():
    pattern = re.compile(r"\s\(\d{2}-\d{2}\s\d{2}:\d{2}\)$")
    changed = 0
    notes = Note.query.all()
    for n in notes:
        new_content = re.sub(pattern, "", n.content)
        if new_content != n.content:
            n.content = new_content
            changed += 1
    if changed:
        db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # ✅ 기존 메모 정리(있으면)
        _strip_old_note_timestamps()
    app.run(host='0.0.0.0', port=5000, debug=True)
