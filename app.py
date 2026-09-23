import os
import sqlite3
import hashlib
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for,
    render_template_string, jsonify, g
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'barber.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = 'emmanuel-stylist-secret-key-change-in-prod'

DAY_NAMES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def hash_password(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.executescript('''
    CREATE TABLE IF NOT EXISTS businesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        owner_name TEXT,
        phone TEXT,
        whatsapp TEXT,
        address TEXT,
        instagram TEXT,
        logo_url TEXT,
        photo_tech_url TEXT,
        photo_peinado_url TEXT,
        primary_color TEXT DEFAULT '#c9a86a',
        secondary_color TEXT DEFAULT '#0a0a0a'
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    CREATE TABLE IF NOT EXISTS business_hours (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        day_of_week INTEGER NOT NULL,
        open_time TEXT,
        close_time TEXT,
        is_closed INTEGER DEFAULT 0,
        UNIQUE(business_id, day_of_week),
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        duration_minutes INTEGER NOT NULL,
        active INTEGER DEFAULT 1,
        category TEXT,
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        name TEXT,
        phone TEXT,
        created_at TEXT,
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        service_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        price INTEGER,
        status TEXT DEFAULT 'confirmed',
        FOREIGN KEY (business_id) REFERENCES businesses(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (service_id) REFERENCES services(id)
    );
    ''')
    conn.commit()

    row = c.execute('SELECT id FROM businesses WHERE slug=?', ('emmanuel',)).fetchone()
    if row is None:
        c.execute('''INSERT INTO businesses
            (slug, name, owner_name, phone, whatsapp, address, instagram,
             logo_url, photo_tech_url, photo_peinado_url, primary_color, secondary_color)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('emmanuel', 'EMMANUEL STYLIST', 'Emmanuel', '1123456789', '5491123456789',
             '', '@emmanuel.stylist', '', '', '', '#c9a86a', '#0a0a0a'))
        business_id = c.lastrowid

        c.execute('INSERT INTO users (business_id, username, password_hash) VALUES (?,?,?)',
                   (business_id, 'admin', hash_password('1234')))

        for dow in range(7):
            if dow == 6:
                c.execute('''INSERT OR REPLACE INTO business_hours
                    (business_id, day_of_week, open_time, close_time, is_closed)
                    VALUES (?,?,?,?,?)''', (business_id, dow, '09:00', '20:00', 1))
            else:
                c.execute('''INSERT OR REPLACE INTO business_hours
                    (business_id, day_of_week, open_time, close_time, is_closed)
                    VALUES (?,?,?,?,?)''', (business_id, dow, '09:00', '20:00', 0))

        services = [
            ('Corte + secado', 34850, 40, 'corte & color'),
            ('Color crecimiento', 94350, 90, 'corte & color'),
            ('Color raíz a punta', 127500, 120, 'corte & color'),
            ('Color + brusing', 136000, 120, 'corte & color'),
            ('Balayage desde', 272000, 180, 'tecnicos'),
            ('Baby light desde', 204000, 180, 'tecnicos'),
            ('Mechas con gorra desde', 246500, 180, 'tecnicos'),
            ('Money pieces desde', 221000, 150, 'tecnicos'),
            ('Brusing + plancha', 68000, 45, 'peinado'),
            ('Peinado con ondas', 85000, 60, 'peinado'),
            ('Alisado', 76500, 90, 'peinado'),
            ('Botox vegan', 93500, 60, 'peinado'),
            ('Plastificado', 93500, 60, 'peinado'),
        ]
        for name, price, duration, category in services:
            c.execute('''INSERT INTO services
                (business_id, name, price, duration_minutes, active, category)
                VALUES (?,?,?,?,1,?)''', (business_id, name, price, duration, category))

        conn.commit()

    conn.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session or 'business_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return wrapper


def get_business_by_slug(slug):
    conn = get_db()
    return conn.execute('SELECT * FROM businesses WHERE slug=?', (slug,)).fetchone()


def fmt_money(v):
    try:
        return '{:,.0f}'.format(v).replace(',', '.')
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# Availability logic
# ---------------------------------------------------------------------------

def get_availability(business_id, service_id, date_str):
    conn = get_db()
    service = conn.execute(
        'SELECT * FROM services WHERE id=? AND business_id=? AND active=1',
        (service_id, business_id)
    ).fetchone()
    if not service:
        return []

    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return []

    dow = dt.weekday()
    hours = conn.execute(
        'SELECT * FROM business_hours WHERE business_id=? AND day_of_week=?',
        (business_id, dow)
    ).fetchone()
    if not hours or hours['is_closed']:
        return []

    try:
        open_t = datetime.strptime(hours['open_time'], '%H:%M')
        close_t = datetime.strptime(hours['close_time'], '%H:%M')
    except (ValueError, TypeError):
        return []

    duration = service['duration_minutes']

    appts = conn.execute(
        "SELECT start_time, end_time FROM appointments "
        "WHERE business_id=? AND date=? AND status='confirmed'",
        (business_id, date_str)
    ).fetchall()
    busy = []
    for a in appts:
        try:
            busy.append((
                datetime.strptime(a['start_time'], '%H:%M'),
                datetime.strptime(a['end_time'], '%H:%M')
            ))
        except ValueError:
            continue

    now = datetime.now()
    is_today = dt.date() == now.date()

    slots = []
    cur = open_t
    while cur + timedelta(minutes=duration) <= close_t:
        slot_end = cur + timedelta(minutes=duration)
        conflict = False
        for bstart, bend in busy:
            if cur < bend and slot_end > bstart:
                conflict = True
                break
        if not conflict:
            if not is_today or cur.time() > now.time():
                slots.append(cur.strftime('%H:%M'))
        cur += timedelta(minutes=30)
    return slots


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return redirect('/b/emmanuel')


@app.route('/b/<slug>')
def public_business(slug):
    biz = get_business_by_slug(slug)
    if not biz:
        return 'Negocio no encontrado', 404

    conn = get_db()
    services = conn.execute(
        'SELECT * FROM services WHERE business_id=? AND active=1 ORDER BY id',
        (biz['id'],)
    ).fetchall()

    corte_color = [s for s in services if s['category'] == 'corte & color']
    tecnicos = [s for s in services if s['category'] == 'tecnicos']
    peinado = [s for s in services if s['category'] == 'peinado']

    initials = ''.join([w[0] for w in biz['name'].split()][:2]).upper() if biz['name'] else 'EM'

    return render_template_string(
        PUBLIC_HTML,
        biz=biz,
        corte_color=corte_color,
        tecnicos=tecnicos,
        peinado=peinado,
        initials=initials,
        fmt_money=fmt_money,
    )


@app.route('/api/business/<slug>/services')
def api_services(slug):
    biz = get_business_by_slug(slug)
    if not biz:
        return jsonify({'error': 'not found'}), 404
    conn = get_db()
    rows = conn.execute(
        'SELECT id, name, price, duration_minutes, category FROM services '
        'WHERE business_id=? AND active=1 ORDER BY id', (biz['id'],)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/business/<slug>/availability')
def api_availability(slug):
    biz = get_business_by_slug(slug)
    if not biz:
        return jsonify({'error': 'not found'}), 404
    service_id = request.args.get('service_id', type=int)
    date_str = request.args.get('date', type=str)
    if not service_id or not date_str:
        return jsonify({'error': 'missing params'}), 400
    slots = get_availability(biz['id'], service_id, date_str)
    return jsonify(slots)


@app.route('/api/business/<slug>/book', methods=['POST'])
def api_book(slug):
    biz = get_business_by_slug(slug)
    if not biz:
        return jsonify({'error': 'not found'}), 404

    data = request.get_json(silent=True) or {}
    service_id = data.get('service_id')
    date_str = data.get('date')
    start_time = data.get('start_time')
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()

    if not all([service_id, date_str, start_time, name, phone]):
        return jsonify({'error': 'Faltan datos'}), 400

    conn = get_db()
    service = conn.execute(
        'SELECT * FROM services WHERE id=? AND business_id=? AND active=1',
        (service_id, biz['id'])
    ).fetchone()
    if not service:
        return jsonify({'error': 'Servicio inválido'}), 400

    available_slots = get_availability(biz['id'], service_id, date_str)
    if start_time not in available_slots:
        return jsonify({'error': 'Ese horario ya no está disponible'}), 409

    start_dt = datetime.strptime(start_time, '%H:%M')
    end_dt = start_dt + timedelta(minutes=service['duration_minutes'])
    end_time = end_dt.strftime('%H:%M')

    customer = conn.execute(
        'SELECT * FROM customers WHERE business_id=? AND phone=?',
        (biz['id'], phone)
    ).fetchone()
    if customer:
        customer_id = customer['id']
        conn.execute('UPDATE customers SET name=? WHERE id=?', (name, customer_id))
    else:
        cur = conn.execute(
            'INSERT INTO customers (business_id, name, phone, created_at) VALUES (?,?,?,?)',
            (biz['id'], name, phone, datetime.now().isoformat())
        )
        customer_id = cur.lastrowid

    conn.execute('''INSERT INTO appointments
        (business_id, customer_id, service_id, date, start_time, end_time, price, status)
        VALUES (?,?,?,?,?,?,?,'confirmed')''',
        (biz['id'], customer_id, service_id, date_str, start_time, end_time, service['price']))
    conn.commit()

    whatsapp_msg = (
        f"Hola {biz['name']}! Soy {name}, confirmo {service['name']} "
        f"el {date_str} a las {start_time}. Precio ${fmt_money(service['price'])}"
    )
    import urllib.parse
    whatsapp_link = f"https://wa.me/{biz['whatsapp']}?text={urllib.parse.quote(whatsapp_msg)}"

    return jsonify({
        'service': service['name'],
        'date': date_str,
        'start_time': start_time,
        'price': service['price'],
        'business_name': biz['name'],
        'whatsapp': biz['whatsapp'],
        'whatsapp_link': whatsapp_link,
    })


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if user and user['password_hash'] == hash_password(password):
            session['user_id'] = user['id']
            session['business_id'] = user['business_id']
            return redirect(url_for('admin_dashboard'))
        error = 'Usuario o contraseña incorrectos'
    return render_template_string(LOGIN_HTML, error=error)


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    business_id = session['business_id']
    biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()
    today = datetime.now().strftime('%Y-%m-%d')

    today_appts = conn.execute('''
        SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name
        FROM appointments a
        JOIN customers c ON a.customer_id = c.id
        JOIN services s ON a.service_id = s.id
        WHERE a.business_id=? AND a.date=? AND a.status='confirmed'
        ORDER BY a.start_time
    ''', (business_id, today)).fetchall()

    upcoming = conn.execute('''
        SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name
        FROM appointments a
        JOIN customers c ON a.customer_id = c.id
        JOIN services s ON a.service_id = s.id
        WHERE a.business_id=? AND a.date > ? AND a.status='confirmed'
        ORDER BY a.date, a.start_time
        LIMIT 20
    ''', (business_id, today)).fetchall()

    stats = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(price),0) as total
        FROM appointments WHERE business_id=? AND status='confirmed'
    ''', (business_id,)).fetchone()

    month_start = datetime.now().strftime('%Y-%m-01')
    month_stats = conn.execute('''
        SELECT COUNT(*) as cnt, COALESCE(SUM(price),0) as total
        FROM appointments WHERE business_id=? AND status='confirmed' AND date >= ?
    ''', (business_id, month_start)).fetchone()

    return render_template_string(
        ADMIN_DASHBOARD_HTML, biz=biz, today_appts=today_appts, upcoming=upcoming,
        stats=stats, month_stats=month_stats, today=today, fmt_money=fmt_money, active='dashboard'
    )


@app.route('/admin/appointments/<int:appt_id>/cancel', methods=['POST'])
@login_required
def admin_cancel_appointment(appt_id):
    conn = get_db()
    conn.execute(
        "UPDATE appointments SET status='cancelled' WHERE id=? AND business_id=?",
        (appt_id, session['business_id'])
    )
    conn.commit()
    return redirect(request.referrer or url_for('admin_dashboard'))


# ---------------------------------------------------------------------------
# Admin appointments
# ---------------------------------------------------------------------------

@app.route('/admin/appointments')
@login_required
def admin_appointments():
    business_id = session['business_id']
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()

    appts = conn.execute('''
        SELECT a.*, c.name as customer_name, c.phone as customer_phone, s.name as service_name
        FROM appointments a
        JOIN customers c ON a.customer_id = c.id
        JOIN services s ON a.service_id = s.id
        WHERE a.business_id=? AND a.date=? AND a.status='confirmed'
        ORDER BY a.start_time
    ''', (business_id, date_str)).fetchall()

    services = conn.execute(
        'SELECT * FROM services WHERE business_id=? AND active=1 ORDER BY name', (business_id,)
    ).fetchall()

    return render_template_string(
        ADMIN_APPOINTMENTS_HTML, biz=biz, appts=appts, services=services,
        date_str=date_str, fmt_money=fmt_money, active='appointments', error=request.args.get('error')
    )


@app.route('/admin/appointments/create', methods=['POST'])
@login_required
def admin_create_appointment():
    business_id = session['business_id']
    date_str = request.form.get('date')
    time_str = request.form.get('time')
    name = (request.form.get('name') or '').strip()
    phone = (request.form.get('phone') or '').strip()
    service_id = request.form.get('service_id', type=int)

    if not all([date_str, time_str, name, phone, service_id]):
        return redirect(url_for('admin_appointments', date=date_str, error='Faltan datos'))

    conn = get_db()
    service = conn.execute(
        'SELECT * FROM services WHERE id=? AND business_id=? AND active=1',
        (service_id, business_id)
    ).fetchone()
    if not service:
        return redirect(url_for('admin_appointments', date=date_str, error='Servicio inválido'))

    available_slots = get_availability(business_id, service_id, date_str)
    if time_str not in available_slots:
        return redirect(url_for('admin_appointments', date=date_str, error='Ese horario se superpone con otro turno'))

    start_dt = datetime.strptime(time_str, '%H:%M')
    end_dt = start_dt + timedelta(minutes=service['duration_minutes'])
    end_time = end_dt.strftime('%H:%M')

    customer = conn.execute(
        'SELECT * FROM customers WHERE business_id=? AND phone=?', (business_id, phone)
    ).fetchone()
    if customer:
        customer_id = customer['id']
        conn.execute('UPDATE customers SET name=? WHERE id=?', (name, customer_id))
    else:
        cur = conn.execute(
            'INSERT INTO customers (business_id, name, phone, created_at) VALUES (?,?,?,?)',
            (business_id, name, phone, datetime.now().isoformat())
        )
        customer_id = cur.lastrowid

    conn.execute('''INSERT INTO appointments
        (business_id, customer_id, service_id, date, start_time, end_time, price, status)
        VALUES (?,?,?,?,?,?,?,'confirmed')''',
        (business_id, customer_id, service_id, date_str, time_str, end_time, service['price']))
    conn.commit()
    return redirect(url_for('admin_appointments', date=date_str))


# ---------------------------------------------------------------------------
# Admin services
# ---------------------------------------------------------------------------

@app.route('/admin/services')
@login_required
def admin_services():
    business_id = session['business_id']
    conn = get_db()
    biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()
    services = conn.execute(
        'SELECT * FROM services WHERE business_id=? ORDER BY category, id', (business_id,)
    ).fetchall()
    return render_template_string(
        ADMIN_SERVICES_HTML, biz=biz, services=services, active='services'
    )


@app.route('/admin/services/create', methods=['POST'])
@login_required
def admin_create_service():
    business_id = session['business_id']
    name = (request.form.get('name') or '').strip()
    price = request.form.get('price', type=int)
    duration = request.form.get('duration', type=int)
    category = (request.form.get('category') or '').strip()

    if name and price and duration:
        conn = get_db()
        conn.execute('''INSERT INTO services
            (business_id, name, price, duration_minutes, active, category)
            VALUES (?,?,?,?,1,?)''', (business_id, name, price, duration, category))
        conn.commit()
    return redirect(url_for('admin_services'))


@app.route('/admin/services/<int:service_id>/update', methods=['POST'])
@login_required
def admin_update_service(service_id):
    business_id = session['business_id']
    name = (request.form.get('name') or '').strip()
    price = request.form.get('price', type=int)
    duration = request.form.get('duration', type=int)
    active = 1 if request.form.get('active') == 'on' else 0

    conn = get_db()
    conn.execute('''UPDATE services SET name=?, price=?, duration_minutes=?, active=?
        WHERE id=? AND business_id=?''', (name, price, duration, active, service_id, business_id))
    conn.commit()
    return redirect(url_for('admin_services'))


@app.route('/admin/services/<int:service_id>/delete', methods=['POST'])
@login_required
def admin_delete_service(service_id):
    business_id = session['business_id']
    conn = get_db()
    conn.execute('DELETE FROM services WHERE id=? AND business_id=?', (service_id, business_id))
    conn.commit()
    return redirect(url_for('admin_services'))


# ---------------------------------------------------------------------------
# Admin customers
# ---------------------------------------------------------------------------

@app.route('/admin/customers')
@login_required
def admin_customers():
    business_id = session['business_id']
    conn = get_db()
    biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()
    customers = conn.execute('''
        SELECT c.*, COUNT(a.id) as total_turnos
        FROM customers c
        LEFT JOIN appointments a ON a.customer_id = c.id AND a.status='confirmed'
        WHERE c.business_id=?
        GROUP BY c.id
        ORDER BY c.name
    ''', (business_id,)).fetchall()
    return render_template_string(
        ADMIN_CUSTOMERS_HTML, biz=biz, customers=customers, active='customers'
    )


# ---------------------------------------------------------------------------
# Admin settings
# ---------------------------------------------------------------------------

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    business_id = session['business_id']
    conn = get_db()
    biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()

    if request.method == 'POST':
        name = request.form.get('name', biz['name'])
        whatsapp = request.form.get('whatsapp', biz['whatsapp'])
        phone = request.form.get('phone', biz['phone'])
        instagram = request.form.get('instagram', biz['instagram'])
        address = request.form.get('address', biz['address'])
        primary_color = request.form.get('primary_color', biz['primary_color'])
        secondary_color = request.form.get('secondary_color', biz['secondary_color'])

        logo_url = biz['logo_url']
        photo_tech_url = biz['photo_tech_url']
        photo_peinado_url = biz['photo_peinado_url']

        for field_name, file_key in [('logo_url', 'logo'), ('photo_tech_url', 'photo_tech'),
                                      ('photo_peinado_url', 'photo_peinado')]:
            file = request.files.get(file_key)
            if file and file.filename:
                safe_name = f"{biz['slug']}_{file_key}_{file.filename}".replace(' ', '_')
                save_path = os.path.join(UPLOAD_DIR, safe_name)
                file.save(save_path)
                url_path = f"/static/uploads/{safe_name}"
                if field_name == 'logo_url':
                    logo_url = url_path
                elif field_name == 'photo_tech_url':
                    photo_tech_url = url_path
                else:
                    photo_peinado_url = url_path

        conn.execute('''UPDATE businesses SET name=?, whatsapp=?, phone=?, instagram=?, address=?,
            primary_color=?, secondary_color=?, logo_url=?, photo_tech_url=?, photo_peinado_url=?
            WHERE id=?''', (name, whatsapp, phone, instagram, address, primary_color, secondary_color,
                             logo_url, photo_tech_url, photo_peinado_url, business_id))

        for dow in range(7):
            open_t = request.form.get(f'open_{dow}')
            close_t = request.form.get(f'close_{dow}')
            is_closed = 1 if request.form.get(f'closed_{dow}') == 'on' else 0
            conn.execute('''INSERT OR REPLACE INTO business_hours
                (id, business_id, day_of_week, open_time, close_time, is_closed)
                VALUES (
                    (SELECT id FROM business_hours WHERE business_id=? AND day_of_week=?),
                    ?, ?, ?, ?, ?)''',
                (business_id, dow, business_id, dow, open_t, close_t, is_closed))

        conn.commit()
        biz = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()

    hours = conn.execute(
        'SELECT * FROM business_hours WHERE business_id=? ORDER BY day_of_week', (business_id,)
    ).fetchall()
    hours_by_day = {h['day_of_week']: h for h in hours}

    return render_template_string(
        ADMIN_SETTINGS_HTML, biz=biz, hours_by_day=hours_by_day, day_names=DAY_NAMES,
        active='settings'
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

PUBLIC_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{{ biz['name'] }}</title>

<!-- PWA: manifest + theme -->
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="{{ biz['primary_color'] }}">

<!-- PWA: iOS (Safari no lee manifest.json para instalación) -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{{ biz['name'] }}">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">

<!-- PWA: Android / general -->
<link rel="icon" type="image/png" sizes="192x192" href="/static/icons/icon-192.png">
<link rel="icon" type="image/png" sizes="512x512" href="/static/icons/icon-512.png">

<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700;900&family=Montserrat:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  body { background:{{ biz['secondary_color'] }}; font-family:'Montserrat',sans-serif; color:#eee; }
  .serif { font-family:'Playfair Display', serif; }
  .gold { color:{{ biz['primary_color'] }}; }
  .gold-bg { background:{{ biz['primary_color'] }}; }
  .line-gold { height:1px; background:linear-gradient(to right, transparent, {{ biz['primary_color'] }}, transparent); }
  .service-row:hover { background:rgba(201,168,106,0.08); cursor:pointer; }
  .modal-bg { background:rgba(0,0,0,0.85); }
</style>
</head>
<body class="min-h-screen">

<div class="max-w-2xl mx-auto px-6 py-10 relative">

  <svg class="absolute top-4 left-2 w-16 h-16 opacity-70" viewBox="0 0 100 100" fill="none" stroke="{{ biz['primary_color'] }}" stroke-width="1">
    <path d="M20 80 Q 20 30 50 20 Q 80 30 80 80" />
    <path d="M30 70 Q 35 40 50 35" />
    <path d="M70 70 Q 65 40 50 35" />
    <circle cx="50" cy="45" r="10" />
  </svg>

  <div class="absolute top-2 right-2 w-20 h-20 rounded-full border gold flex items-center justify-center text-center text-[10px] tracking-widest" style="border-color:{{ biz['primary_color'] }};">
    BEAUTY<br>SALON
  </div>

  <div class="text-center pt-10 pb-8">
    <h1 class="serif gold text-4xl md:text-5xl font-bold tracking-wide">{{ biz['name'] }}</h1>
  </div>

  <!-- SECCION 1: corte & color -->
  <div class="line-gold mb-1"></div>
  <h2 class="serif text-center gold text-xl italic py-3">corte &amp; color</h2>
  <div class="line-gold mb-6"></div>

  <div class="flex gap-6 items-start mb-14">
    <div class="flex-shrink-0">
      <div class="w-28 h-28 rounded-full gold-bg flex items-center justify-center overflow-hidden" style="background:#000;border:2px solid {{ biz['primary_color'] }};">
        {% if biz['logo_url'] %}
          <img src="{{ biz['logo_url'] }}" class="w-full h-full object-cover">
        {% else %}
          <span class="serif gold text-3xl">{{ initials }}</span>
        {% endif %}
      </div>
    </div>
    <div class="flex-1">
      {% for s in corte_color %}
      <div class="service-row flex justify-between items-center py-3 border-b border-gray-800 px-2"
           onclick="openModal({{ s['id'] }}, '{{ s['name']|replace("'", "\\'") }}', {{ s['price'] }}, {{ s['duration_minutes'] }})">
        <span class="text-sm">{{ s['name'] }}</span>
        <span class="gold text-sm font-medium">${{ fmt_money(s['price']) }}</span>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- SECCION 2: TRABAJOS TECNICOS -->
  <div class="line-gold mb-1"></div>
  <h2 class="serif text-center gold text-lg tracking-[0.3em] py-3">TRABAJOS TÉCNICOS</h2>
  <div class="line-gold mb-6"></div>

  <div class="flex gap-6 items-start mb-14 flex-row-reverse">
    <div class="flex-shrink-0">
      <div class="w-28 h-28 rounded-full overflow-hidden" style="border:2px solid {{ biz['primary_color'] }};">
        <img src="{{ biz['photo_tech_url'] or 'https://images.unsplash.com/photo-1605497788044-5a32c7078486?w=300' }}" class="w-full h-full object-cover">
      </div>
    </div>
    <div class="flex-1">
      {% for s in tecnicos %}
      <div class="service-row flex justify-between items-center py-3 border-b border-gray-800 px-2"
           onclick="openModal({{ s['id'] }}, '{{ s['name']|replace("'", "\\'") }}', {{ s['price'] }}, {{ s['duration_minutes'] }})">
        <span class="text-sm">{{ s['name'] }}</span>
        <span class="gold text-sm font-medium">${{ fmt_money(s['price']) }}</span>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- SECCION 3: peinado & nutriciones -->
  <div class="line-gold mb-1"></div>
  <h2 class="serif text-center gold text-xl italic py-3">peinado &amp; nutriciones</h2>
  <div class="line-gold mb-6"></div>

  <div class="flex gap-6 items-start mb-14">
    <div class="flex-shrink-0">
      <div class="w-28 h-28 rounded-full overflow-hidden" style="border:2px solid {{ biz['primary_color'] }};">
        <img src="{{ biz['photo_peinado_url'] or 'https://images.unsplash.com/photo-1560869713-7d0a294a0d6c?w=300' }}" class="w-full h-full object-cover">
      </div>
    </div>
    <div class="flex-1">
      {% for s in peinado %}
      <div class="service-row flex justify-between items-center py-3 border-b border-gray-800 px-2"
           onclick="openModal({{ s['id'] }}, '{{ s['name']|replace("'", "\\'") }}', {{ s['price'] }}, {{ s['duration_minutes'] }})">
        <span class="text-sm">{{ s['name'] }}</span>
        <span class="gold text-sm font-medium">${{ fmt_money(s['price']) }}</span>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="text-center text-xs text-gray-500 pb-10">
    {% if biz['address'] %}{{ biz['address'] }} · {% endif %}
    {% if biz['instagram'] %}{{ biz['instagram'] }}{% endif %}
  </div>
</div>

<!-- MODAL -->
<div id="modal" class="fixed inset-0 modal-bg hidden items-center justify-center z-50 p-4">
  <div class="w-full max-w-md rounded-lg p-6" style="background:#111;border:1px solid {{ biz['primary_color'] }};">
    <h3 id="modalServiceName" class="serif gold text-xl mb-1"></h3>
    <p id="modalServicePrice" class="text-sm text-gray-400 mb-4"></p>

    <div id="bookingForm">
      <label class="block text-xs text-gray-400 mb-1">Fecha</label>
      <input type="date" id="dateInput" class="w-full mb-4 p-2 rounded bg-black border border-gray-700 text-white">

      <label class="block text-xs text-gray-400 mb-1">Horarios disponibles</label>
      <div id="slotsGrid" class="grid grid-cols-3 gap-2 mb-4"></div>

      <label class="block text-xs text-gray-400 mb-1">Nombre</label>
      <input type="text" id="nameInput" class="w-full mb-3 p-2 rounded bg-black border border-gray-700 text-white">

      <label class="block text-xs text-gray-400 mb-1">WhatsApp</label>
      <input type="text" id="phoneInput" placeholder="549..." class="w-full mb-5 p-2 rounded bg-black border border-gray-700 text-white">

      <div id="bookError" class="text-red-400 text-xs mb-3 hidden"></div>

      <div class="flex gap-3">
        <button onclick="confirmBooking()" class="flex-1 gold-bg text-black font-semibold py-2 rounded" style="color:#000;">Confirmar</button>
        <button onclick="closeModal()" class="flex-1 border border-gray-600 text-gray-300 py-2 rounded">Cerrar</button>
      </div>
    </div>

    <div id="confirmationBox" class="hidden text-center">
      <p class="gold serif text-lg mb-2">¡Turno confirmado!</p>
      <p id="confirmText" class="text-sm text-gray-300 mb-4"></p>
      <a id="whatsappLink" href="#" target="_blank" class="block gold-bg text-black font-semibold py-2 rounded mb-3" style="color:#000;">Confirmar por WhatsApp</a>
      <button onclick="closeModal()" class="w-full border border-gray-600 text-gray-300 py-2 rounded">Cerrar</button>
    </div>
  </div>
</div>

<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  });
}

const SLUG = "{{ biz['slug'] }}";
let currentService = null;

function openModal(id, name, price, duration) {
  currentService = { id, name, price, duration };
  document.getElementById('modalServiceName').innerText = name;
  document.getElementById('modalServicePrice').innerText = '$' + price.toLocaleString('es-AR');
  document.getElementById('bookingForm').classList.remove('hidden');
  document.getElementById('confirmationBox').classList.add('hidden');
  document.getElementById('bookError').classList.add('hidden');
  document.getElementById('slotsGrid').innerHTML = '';
  document.getElementById('nameInput').value = '';
  document.getElementById('phoneInput').value = '';

  const dateInput = document.getElementById('dateInput');
  const today = new Date().toISOString().split('T')[0];
  dateInput.min = today;
  dateInput.value = today;
  dateInput.onchange = loadSlots;

  document.getElementById('modal').classList.remove('hidden');
  document.getElementById('modal').classList.add('flex');
  loadSlots();
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
  document.getElementById('modal').classList.remove('flex');
}

async function loadSlots() {
  const date = document.getElementById('dateInput').value;
  const grid = document.getElementById('slotsGrid');
  grid.innerHTML = '<span class="text-xs text-gray-500 col-span-3">Cargando...</span>';
  try {
    const res = await fetch(`/api/business/${SLUG}/availability?service_id=${currentService.id}&date=${date}`);
    const slots = await res.json();
    grid.innerHTML = '';
    if (slots.length === 0) {
      grid.innerHTML = '<span class="text-xs text-gray-500 col-span-3">No hay horarios disponibles</span>';
      return;
    }
    slots.forEach(s => {
      const btn = document.createElement('button');
      btn.innerText = s;
      btn.type = 'button';
      btn.className = 'slot-btn text-xs py-2 rounded border border-gray-700 hover:border-yellow-600';
      btn.onclick = () => selectSlot(btn, s);
      grid.appendChild(btn);
    });
  } catch (e) {
    grid.innerHTML = '<span class="text-xs text-red-400 col-span-3">Error al cargar horarios</span>';
  }
}

let selectedSlot = null;
function selectSlot(btn, slot) {
  document.querySelectorAll('.slot-btn').forEach(b => {
    b.style.background = '';
    b.style.color = '';
  });
  btn.style.background = "{{ biz['primary_color'] }}";
  btn.style.color = '#000';
  selectedSlot = slot;
}

async function confirmBooking() {
  const errBox = document.getElementById('bookError');
  errBox.classList.add('hidden');

  const name = document.getElementById('nameInput').value.trim();
  const phone = document.getElementById('phoneInput').value.trim();
  const date = document.getElementById('dateInput').value;

  if (!selectedSlot) { errBox.innerText = 'Elegí un horario'; errBox.classList.remove('hidden'); return; }
  if (!name || !phone) { errBox.innerText = 'Completá nombre y WhatsApp'; errBox.classList.remove('hidden'); return; }

  try {
    const res = await fetch(`/api/business/${SLUG}/book`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        service_id: currentService.id,
        date: date,
        start_time: selectedSlot,
        name: name,
        phone: phone
      })
    });
    const data = await res.json();
    if (!res.ok) {
      errBox.innerText = data.error || 'Error al reservar';
      errBox.classList.remove('hidden');
      if (res.status === 409) loadSlots();
      return;
    }
    document.getElementById('bookingForm').classList.add('hidden');
    document.getElementById('confirmationBox').classList.remove('hidden');
    document.getElementById('confirmText').innerText =
      `${data.service} · ${data.date} ${data.start_time} · $${data.price.toLocaleString('es-AR')}`;
    document.getElementById('whatsappLink').href = data.whatsapp_link;
    selectedSlot = null;
  } catch (e) {
    errBox.innerText = 'Error de conexión';
    errBox.classList.remove('hidden');
  }
}
</script>
</body>
</html>
'''

ADMIN_NAV = '''
<div class="flex flex-wrap gap-2 mb-6 text-sm">
  <a href="/admin" class="px-3 py-2 rounded {{ 'bg-yellow-700 text-black' if active=='dashboard' else 'bg-gray-800 text-gray-300' }}">Inicio</a>
  <a href="/admin/appointments" class="px-3 py-2 rounded {{ 'bg-yellow-700 text-black' if active=='appointments' else 'bg-gray-800 text-gray-300' }}">Turnos</a>
  <a href="/admin/services" class="px-3 py-2 rounded {{ 'bg-yellow-700 text-black' if active=='services' else 'bg-gray-800 text-gray-300' }}">Servicios</a>
  <a href="/admin/customers" class="px-3 py-2 rounded {{ 'bg-yellow-700 text-black' if active=='customers' else 'bg-gray-800 text-gray-300' }}">Clientes</a>
  <a href="/admin/settings" class="px-3 py-2 rounded {{ 'bg-yellow-700 text-black' if active=='settings' else 'bg-gray-800 text-gray-300' }}">Configuración</a>
  <a href="/admin/logout" class="px-3 py-2 rounded bg-red-900 text-gray-200 ml-auto">Salir</a>
</div>
'''

ADMIN_STYLE = '''
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background:#0a0a0a; color:#eee; font-family:'Montserrat',sans-serif; }
  input, select { background:#000; border:1px solid #333; color:#fff; }
  table { width:100%; border-collapse:collapse; }
  th, td { padding:8px; border-bottom:1px solid #222; text-align:left; font-size:0.85rem; }
</style>
'''

LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Admin Login</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen flex items-center justify-center">
<div class="w-full max-w-sm p-8 rounded border border-gray-800">
  <h1 class="text-xl mb-6 text-center" style="color:#c9a86a;">Panel de Administración</h1>
  {% if error %}<p class="text-red-400 text-sm mb-4">{{ error }}</p>{% endif %}
  <form method="POST">
    <label class="block text-xs text-gray-400 mb-1">Usuario</label>
    <input type="text" name="username" class="w-full mb-4 p-2 rounded">
    <label class="block text-xs text-gray-400 mb-1">Contraseña</label>
    <input type="password" name="password" class="w-full mb-6 p-2 rounded">
    <button type="submit" class="w-full py-2 rounded font-semibold" style="background:#c9a86a;color:#000;">Ingresar</button>
  </form>
</div>
</body></html>
'''

ADMIN_DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Admin - {{ biz['name'] }}</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen p-4 md:p-8">
<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl mb-4" style="color:#c9a86a;">{{ biz['name'] }} · Panel</h1>
  ''' + ADMIN_NAV + '''

  <div class="grid grid-cols-2 md:grid-cols-2 gap-4 mb-8">
    <div class="p-4 rounded border border-gray-800">
      <p class="text-xs text-gray-400">Turnos totales</p>
      <p class="text-2xl" style="color:#c9a86a;">{{ stats['cnt'] }}</p>
    </div>
    <div class="p-4 rounded border border-gray-800">
      <p class="text-xs text-gray-400">Facturado total</p>
      <p class="text-2xl" style="color:#c9a86a;">${{ fmt_money(stats['total']) }}</p>
    </div>
    <div class="p-4 rounded border border-gray-800">
      <p class="text-xs text-gray-400">Turnos del mes</p>
      <p class="text-2xl" style="color:#c9a86a;">{{ month_stats['cnt'] }}</p>
    </div>
    <div class="p-4 rounded border border-gray-800">
      <p class="text-xs text-gray-400">Facturado del mes</p>
      <p class="text-2xl" style="color:#c9a86a;">${{ fmt_money(month_stats['total']) }}</p>
    </div>
  </div>

  <h2 class="text-lg mb-2" style="color:#c9a86a;">Hoy ({{ today }})</h2>
  <table class="mb-8">
    <tr><th>Hora</th><th>Cliente</th><th>Servicio</th><th>Precio</th><th></th></tr>
    {% for a in today_appts %}
    <tr>
      <td>{{ a['start_time'] }}</td>
      <td>{{ a['customer_name'] }}<br><span class="text-gray-500">{{ a['customer_phone'] }}</span></td>
      <td>{{ a['service_name'] }}</td>
      <td>${{ fmt_money(a['price']) }}</td>
      <td>
        <form method="POST" action="/admin/appointments/{{ a['id'] }}/cancel" onsubmit="return confirm('¿Cancelar turno?')">
          <button class="text-red-400 text-xs">Cancelar</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="text-gray-500">Sin turnos hoy</td></tr>
    {% endfor %}
  </table>

  <h2 class="text-lg mb-2" style="color:#c9a86a;">Próximos turnos</h2>
  <table>
    <tr><th>Fecha</th><th>Hora</th><th>Cliente</th><th>Servicio</th><th>Precio</th><th></th></tr>
    {% for a in upcoming %}
    <tr>
      <td>{{ a['date'] }}</td>
      <td>{{ a['start_time'] }}</td>
      <td>{{ a['customer_name'] }}<br><span class="text-gray-500">{{ a['customer_phone'] }}</span></td>
      <td>{{ a['service_name'] }}</td>
      <td>${{ fmt_money(a['price']) }}</td>
      <td>
        <form method="POST" action="/admin/appointments/{{ a['id'] }}/cancel" onsubmit="return confirm('¿Cancelar turno?')">
          <button class="text-red-400 text-xs">Cancelar</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="text-gray-500">Sin próximos turnos</td></tr>
    {% endfor %}
  </table>
</div>
</body></html>
'''

ADMIN_APPOINTMENTS_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Turnos - {{ biz['name'] }}</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen p-4 md:p-8">
<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl mb-4" style="color:#c9a86a;">{{ biz['name'] }} · Panel</h1>
  ''' + ADMIN_NAV + '''

  <form method="GET" class="mb-6 flex items-center gap-3">
    <label class="text-sm text-gray-400">Fecha</label>
    <input type="date" name="date" value="{{ date_str }}" class="p-2 rounded" onchange="this.form.submit()">
  </form>

  {% if error %}<p class="text-red-400 text-sm mb-4">{{ error }}</p>{% endif %}

  <table class="mb-10">
    <tr><th>Hora</th><th>Cliente</th><th>Servicio</th><th>Precio</th><th></th></tr>
    {% for a in appts %}
    <tr>
      <td>{{ a['start_time'] }} - {{ a['end_time'] }}</td>
      <td>{{ a['customer_name'] }}<br><span class="text-gray-500">{{ a['customer_phone'] }}</span></td>
      <td>{{ a['service_name'] }}</td>
      <td>${{ fmt_money(a['price']) }}</td>
      <td>
        <form method="POST" action="/admin/appointments/{{ a['id'] }}/cancel" onsubmit="return confirm('¿Cancelar turno?')">
          <button class="text-red-400 text-xs">Cancelar</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="text-gray-500">Sin turnos este día</td></tr>
    {% endfor %}
  </table>

  <h2 class="text-lg mb-3" style="color:#c9a86a;">Crear turno manual</h2>
  <form method="POST" action="/admin/appointments/create" class="grid grid-cols-2 md:grid-cols-3 gap-3 max-w-2xl">
    <input type="hidden" name="date" value="{{ date_str }}">
    <div class="col-span-1">
      <label class="block text-xs text-gray-400 mb-1">Hora</label>
      <input type="time" name="time" required class="w-full p-2 rounded">
    </div>
    <div class="col-span-1">
      <label class="block text-xs text-gray-400 mb-1">Servicio</label>
      <select name="service_id" required class="w-full p-2 rounded">
        {% for s in services %}
        <option value="{{ s['id'] }}">{{ s['name'] }} (${{ fmt_money(s['price']) }})</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-span-1">
      <label class="block text-xs text-gray-400 mb-1">Nombre</label>
      <input type="text" name="name" required class="w-full p-2 rounded">
    </div>
    <div class="col-span-1">
      <label class="block text-xs text-gray-400 mb-1">WhatsApp</label>
      <input type="text" name="phone" required class="w-full p-2 rounded">
    </div>
    <div class="col-span-1 flex items-end">
      <button type="submit" class="w-full py-2 rounded font-semibold" style="background:#c9a86a;color:#000;">Crear turno</button>
    </div>
  </form>
</div>
</body></html>
'''

ADMIN_SERVICES_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Servicios - {{ biz['name'] }}</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen p-4 md:p-8">
<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl mb-4" style="color:#c9a86a;">{{ biz['name'] }} · Panel</h1>
  ''' + ADMIN_NAV + '''

  <h2 class="text-lg mb-3" style="color:#c9a86a;">Nuevo servicio</h2>
  <form method="POST" action="/admin/services/create" class="grid grid-cols-2 md:grid-cols-4 gap-3 max-w-3xl mb-10">
    <input type="text" name="name" placeholder="Nombre" required class="p-2 rounded">
    <input type="number" name="price" placeholder="Precio" required class="p-2 rounded">
    <input type="number" name="duration" placeholder="Duración (min)" required class="p-2 rounded">
    <input type="text" name="category" placeholder="Categoría" class="p-2 rounded">
    <button type="submit" class="col-span-2 md:col-span-4 py-2 rounded font-semibold" style="background:#c9a86a;color:#000;">Agregar servicio</button>
  </form>

  <h2 class="text-lg mb-3" style="color:#c9a86a;">Servicios</h2>
  {% for s in services %}
  <form method="POST" action="/admin/services/{{ s['id'] }}/update" class="grid grid-cols-2 md:grid-cols-6 gap-2 items-center mb-2 p-2 border border-gray-800 rounded">
    <input type="text" name="name" value="{{ s['name'] }}" class="p-2 rounded">
    <input type="number" name="price" value="{{ s['price'] }}" class="p-2 rounded">
    <input type="number" name="duration" value="{{ s['duration_minutes'] }}" class="p-2 rounded">
    <label class="flex items-center gap-2 text-xs text-gray-400">
      <input type="checkbox" name="active" {{ 'checked' if s['active'] else '' }}> Activo
    </label>
    <button type="submit" class="py-2 rounded text-xs" style="background:#c9a86a;color:#000;">Guardar</button>
    <button type="submit" formaction="/admin/services/{{ s['id'] }}/delete" class="py-2 rounded text-xs bg-red-900 text-gray-200" onclick="return confirm('¿Eliminar servicio?')">Eliminar</button>
  </form>
  {% else %}
  <p class="text-gray-500">Sin servicios</p>
  {% endfor %}
</div>
</body></html>
'''

ADMIN_CUSTOMERS_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Clientes - {{ biz['name'] }}</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen p-4 md:p-8">
<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl mb-4" style="color:#c9a86a;">{{ biz['name'] }} · Panel</h1>
  ''' + ADMIN_NAV + '''

  <table>
    <tr><th>Nombre</th><th>WhatsApp</th><th>Turnos</th><th>Cliente desde</th></tr>
    {% for c in customers %}
    <tr>
      <td>{{ c['name'] }}</td>
      <td>{{ c['phone'] }}</td>
      <td>{{ c['total_turnos'] }}</td>
      <td>{{ c['created_at'][:10] if c['created_at'] else '' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="text-gray-500">Sin clientes todavía</td></tr>
    {% endfor %}
  </table>
</div>
</body></html>
'''

ADMIN_SETTINGS_HTML = '''
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Configuración - {{ biz['name'] }}</title>
''' + ADMIN_STYLE + '''
</head><body class="min-h-screen p-4 md:p-8">
<div class="max-w-3xl mx-auto">
  <h1 class="text-2xl mb-4" style="color:#c9a86a;">{{ biz['name'] }} · Panel</h1>
  ''' + ADMIN_NAV + '''

  <form method="POST" enctype="multipart/form-data">
    <h2 class="text-lg mb-3" style="color:#c9a86a;">Datos del negocio</h2>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-8">
      <div>
        <label class="block text-xs text-gray-400 mb-1">Nombre</label>
        <input type="text" name="name" value="{{ biz['name'] }}" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">WhatsApp (con código país, sin +)</label>
        <input type="text" name="whatsapp" value="{{ biz['whatsapp'] }}" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Teléfono</label>
        <input type="text" name="phone" value="{{ biz['phone'] }}" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Instagram</label>
        <input type="text" name="instagram" value="{{ biz['instagram'] }}" class="w-full p-2 rounded">
      </div>
      <div class="md:col-span-2">
        <label class="block text-xs text-gray-400 mb-1">Dirección</label>
        <input type="text" name="address" value="{{ biz['address'] }}" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Color primario</label>
        <input type="color" name="primary_color" value="{{ biz['primary_color'] }}" class="w-full p-1 rounded h-10">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Color secundario</label>
        <input type="color" name="secondary_color" value="{{ biz['secondary_color'] }}" class="w-full p-1 rounded h-10">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Logo</label>
        <input type="file" name="logo" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Foto sección técnicos</label>
        <input type="file" name="photo_tech" class="w-full p-2 rounded">
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Foto sección peinado</label>
        <input type="file" name="photo_peinado" class="w-full p-2 rounded">
      </div>
    </div>

    <h2 class="text-lg mb-3" style="color:#c9a86a;">Horarios</h2>
    <div class="space-y-2 mb-8">
      {% for dow in range(7) %}
      <div class="grid grid-cols-4 gap-2 items-center">
        <span class="text-sm">{{ day_names[dow] }}</span>
        <input type="time" name="open_{{ dow }}" value="{{ hours_by_day[dow]['open_time'] if dow in hours_by_day else '09:00' }}" class="p-2 rounded">
        <input type="time" name="close_{{ dow }}" value="{{ hours_by_day[dow]['close_time'] if dow in hours_by_day else '20:00' }}" class="p-2 rounded">
        <label class="flex items-center gap-2 text-xs text-gray-400">
          <input type="checkbox" name="closed_{{ dow }}" {{ 'checked' if dow in hours_by_day and hours_by_day[dow]['is_closed'] else '' }}> Cerrado
        </label>
      </div>
      {% endfor %}
    </div>

    <button type="submit" class="py-2 px-6 rounded font-semibold" style="background:#c9a86a;color:#000;">Guardar cambios</button>
  </form>
</div>
</body></html>
'''


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
