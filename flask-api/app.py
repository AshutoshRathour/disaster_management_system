from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
import sqlite3
import json
import os
import hashlib
import secrets
import threading
import time
import random

from py_backend import (
    backend_init, backend_add_city, backend_add_road,
    backend_shortest_path_json, backend_graph_json,
    backend_allocate_resources, backend_add_request,
    backend_update_city_damage
)

app = Flask(__name__, static_folder='../frontend', static_url_path='/static')
app.secret_key = secrets.token_hex(32)
CORS(app)

# ─── Database ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'disaster_relief.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS cities (
        id INTEGER PRIMARY KEY,
        name TEXT,
        population INTEGER,
        damage_level INTEGER,
        resources INTEGER,
        latitude REAL,
        longitude REAL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS roads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        src INTEGER,
        dest INTEGER,
        distance INTEGER
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city_id INTEGER,
        priority INTEGER,
        required_resources INTEGER,
        status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_detection', 'true')")

    # DB migrations for newly added columns
    cursor = c.execute("PRAGMA table_info(cities)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'temperature' not in columns:
        c.execute("ALTER TABLE cities ADD COLUMN temperature REAL DEFAULT 25.0")
    if 'rainfall' not in columns:
        c.execute("ALTER TABLE cities ADD COLUMN rainfall REAL DEFAULT 0.0")
    if 'seismic_activity' not in columns:
        c.execute("ALTER TABLE cities ADD COLUMN seismic_activity REAL DEFAULT 0.0")
    if 'wind_speed' not in columns:
        c.execute("ALTER TABLE cities ADD COLUMN wind_speed REAL DEFAULT 10.0")
    if 'sensor_status' not in columns:
        c.execute("ALTER TABLE cities ADD COLUMN sensor_status TEXT DEFAULT 'stable'")

    cursor = c.execute("PRAGMA table_info(requests)")
    req_columns = [col[1] for col in cursor.fetchall()]
    if 'food' not in req_columns:
        c.execute("ALTER TABLE requests ADD COLUMN food INTEGER DEFAULT 0")
    if 'water' not in req_columns:
        c.execute("ALTER TABLE requests ADD COLUMN water INTEGER DEFAULT 0")
    if 'medical_kits' not in req_columns:
        c.execute("ALTER TABLE requests ADD COLUMN medical_kits INTEGER DEFAULT 0")
    if 'rescue_teams' not in req_columns:
        c.execute("ALTER TABLE requests ADD COLUMN rescue_teams INTEGER DEFAULT 0")

    conn.commit()
    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


# ─── Initialize ─────────────────────────────────────────────────────────────
backend_init()
init_db()


def restore_state():
    conn = get_db()
    c = conn.cursor()

    c.execute('SELECT * FROM cities ORDER BY id')
    for row in c.fetchall():
        backend_add_city(
            row['name'], row['population'], row['damage_level'],
            row['resources'], row['latitude'], row['longitude']
        )
        # Sync initial DB values to in-memory graph
        backend_update_city_damage(row['id'], row['damage_level'])

    c.execute('SELECT * FROM roads')
    for row in c.fetchall():
        backend_add_road(row['src'], row['dest'], row['distance'])

    c.execute('SELECT * FROM requests WHERE status = ? ORDER BY id', ('pending',))
    for row in c.fetchall():
        backend_add_request(row['city_id'], row['priority'], row['required_resources'])

    conn.close()


restore_state()


# ─── Background Simulation Thread ───────────────────────────────────────────
def sensor_simulation_loop():
    time.sleep(5)
    while True:
        try:
            conn = get_db()
            c = conn.cursor()

            row = c.execute("SELECT value FROM settings WHERE key = 'auto_detection'").fetchone()
            auto_detection_enabled = (row['value'] == 'true') if row else False

            cities_rows = c.execute("SELECT * FROM cities").fetchall()

            for city in cities_rows:
                city_id = city['id']
                name = city['name']
                pop = city['population']
                prev_temp = city['temperature'] if city['temperature'] is not None else 25.0
                prev_rain = city['rainfall'] if city['rainfall'] is not None else 10.0
                prev_seismic = city['seismic_activity'] if city['seismic_activity'] is not None else 1.5
                prev_wind = city['wind_speed'] if city['wind_speed'] is not None else 15.0
                prev_damage = city['damage_level']

                # Mean-reverting weather fluctuations to simulate natural recovery
                # temp target = 25.0, rain target = 10.0, seismic target = 0.5, wind target = 15.0
                temp = prev_temp + random.uniform(-4.0, 4.0) + (25.0 - prev_temp) * 0.1
                rain = prev_rain + random.uniform(-25.0, 25.0) + (10.0 - prev_rain) * 0.15
                seismic = prev_seismic + random.uniform(-0.6, 0.6) + (0.5 - prev_seismic) * 0.2
                wind = prev_wind + random.uniform(-15.0, 15.0) + (15.0 - prev_wind) * 0.15

                # Apply bounds
                temp = max(10.0, min(52.0, temp))
                rain = max(0.0, min(260.0, rain))
                seismic = max(0.0, min(9.0, seismic))
                wind = max(0.0, min(140.0, wind))

                temp = round(temp, 1)
                rain = round(rain, 1)
                seismic = round(seismic, 1)
                wind = round(wind, 1)

                disaster_reason = []
                if rain > 150.0:
                    disaster_reason.append(f"Heavy Rainfall ({rain}mm > 150mm)")
                if seismic > 6.0:
                    disaster_reason.append(f"High Seismic Activity ({seismic} Richter > 6.0)")
                if temp > 47.0:
                    disaster_reason.append(f"Extreme Heat ({temp}°C > 47°C)")
                if temp < -5.0:
                    disaster_reason.append(f"Extreme Cold ({temp}°C < -5°C)")
                if wind > 100.0:
                    disaster_reason.append(f"Severe Wind Speed ({wind}km/h > 100km/h)")

                is_disaster = len(disaster_reason) > 0
                new_status = 'stable'
                new_damage = prev_damage

                if is_disaster:
                    new_status = 'High Risk/Disaster'
                    new_damage = max(prev_damage, 9)

                    if auto_detection_enabled:
                        existing = c.execute(
                            "SELECT id FROM requests WHERE city_id = ? AND status = 'pending'",
                            (city_id,)
                        ).fetchone()

                        if not existing:
                            pop_factor = max(100, int(pop / 1000))
                            food = random.randint(pop_factor, pop_factor * 3)
                            water = random.randint(pop_factor * 2, pop_factor * 6)
                            medical_kits = random.randint(int(pop_factor / 10), int(pop_factor / 3))
                            rescue_teams = random.randint(1, max(2, int(pop_factor / 100)))

                            required = food + water + medical_kits + (rescue_teams * 50)
                            priority = random.randint(8, 10)

                            cursor = c.execute(
                                '''INSERT INTO requests 
                                (city_id, priority, required_resources, status, food, water, medical_kits, rescue_teams) 
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                                (city_id, priority, required, 'pending', food, water, medical_kits, rescue_teams)
                            )
                            req_id = cursor.lastrowid

                            backend_add_request(city_id, priority, required)

                            reasons_str = ", ".join(disaster_reason)
                            c.execute(
                                'INSERT INTO logs (action, details) VALUES (?, ?)',
                                ('auto_disaster_alert', f"Auto-Detection: Alert triggered for {name} due to {reasons_str}. Logged Request #{req_id} (Priority: {priority}).")
                            )
                else:
                    # Gradually recover/reduce damage level back to baseline (2) if sensors are stable
                    if prev_damage > 2:
                        new_damage = prev_damage - 1

                c.execute(
                    '''UPDATE cities SET 
                       temperature = ?, rainfall = ?, seismic_activity = ?, wind_speed = ?, 
                       sensor_status = ?, damage_level = ?
                       WHERE id = ?''',
                    (temp, rain, seismic, wind, new_status, new_damage, city_id)
                )
                backend_update_city_damage(city_id, new_damage)

            conn.commit()
            conn.close()
        except Exception as err:
            print(f"Error in simulation thread: {err}")
            try:
                conn.close()
            except:
                pass
        time.sleep(15)


sim_thread = threading.Thread(target=sensor_simulation_loop, daemon=True)
sim_thread.start()



# ─── Auth helpers ────────────────────────────────────────────────────────────
def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


# ─── Page routes ─────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    return app.send_static_file('index.html')


@app.route('/login')
def login_page():
    return app.send_static_file('login.html')


@app.route('/signup')
def signup_page():
    return app.send_static_file('signup.html')


@app.route('/dashboard')
@login_required
def dashboard():
    return app.send_static_file('index.html')


@app.route('/alerts')
@login_required
def alerts_page():
    return app.send_static_file('alerts.html')


@app.route('/cities')
@login_required
def cities_page():
    return app.send_static_file('cities.html')


@app.route('/roads')
@login_required
def roads_page():
    return app.send_static_file('roads.html')


@app.route('/requests')
@login_required
def requests_page():
    return app.send_static_file('requests.html')


@app.route('/allocate')
@login_required
def allocate_page():
    return app.send_static_file('allocate.html')


@app.route('/logs')
@login_required
def logs_page():
    return app.send_static_file('logs.html')


@app.route('/map')
@login_required
def map_page():
    return app.send_static_file('map.html')


@app.route('/emergency')
@login_required
def emergency_page():
    return app.send_static_file('emergency.html')


# ─── Auth API ────────────────────────────────────────────────────────────────
@app.route('/api/signup', methods=['POST'])
def signup():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    confirm = request.form.get('confirm_password', '')

    if not username or not email or not password:
        return redirect('/signup?error=All+fields+are+required')

    if password != confirm:
        return redirect('/signup?error=Passwords+do+not+match')

    if len(password) < 6:
        return redirect('/signup?error=Password+must+be+at+least+6+characters')

    conn = get_db()
    try:
        conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                     (username, email, hash_password(password)))
        conn.commit()
        conn.close()
        return redirect('/login?success=Account+created+successfully')
    except sqlite3.IntegrityError:
        conn.close()
        return redirect('/signup?error=Username+or+email+already+exists')


@app.route('/api/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ? AND password_hash = ?',
                        (username, hash_password(password))).fetchone()
    conn.close()

    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect('/')
    else:
        return redirect('/login?error=Invalid+username+or+password')


@app.route('/api/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/me', methods=['GET'])
def get_current_user():
    if 'user_id' in session:
        return jsonify({'logged_in': True, 'username': session['username']})
    return jsonify({'logged_in': False})


# ─── Settings API ────────────────────────────────────────────────────────────
@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def handle_settings():
    conn = get_db()
    if request.method == 'POST':
        data = request.json
        for key, val in data.items():
            conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(val)))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    else:
        rows = conn.execute('SELECT * FROM settings').fetchall()
        conn.close()
        settings = {row['key']: row['value'] for row in rows}
        return jsonify(settings)


# ─── City API ────────────────────────────────────────────────────────────────
@app.route('/api/city/add', methods=['POST'])
@login_required
def add_city():
    data = request.json
    name = data['name']
    pop = data['population']
    damage = data['damage_level']
    res = data['resources']
    lat = data['latitude']
    lon = data['longitude']

    city_id = backend_add_city(name, pop, damage, res, lat, lon)

    conn = get_db()
    conn.execute('''INSERT INTO cities 
                 (id, name, population, damage_level, resources, latitude, longitude, temperature, rainfall, seismic_activity, wind_speed, sensor_status) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, 25.0, 0.0, 0.0, 10.0, 'stable')''',
                 (city_id, name, pop, damage, res, lat, lon))
    conn.execute('INSERT INTO logs (action, details) VALUES (?, ?)',
                 ('add_city', f'Added city: {name}'))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'id': city_id})

 
@app.route('/api/city/list', methods=['GET'])
@login_required
def list_cities():
    conn = get_db()
    rows = conn.execute('SELECT * FROM cities').fetchall()
    conn.close()
    return jsonify({'cities': [dict(row) for row in rows]})


# ─── Road API ────────────────────────────────────────────────────────────────
@app.route('/api/road/add', methods=['POST'])
@login_required
def add_road():
    data = request.json
    src = data['src']
    dest = data['dest']
    dist = data['distance']

    backend_add_road(src, dest, dist)

    conn = get_db()
    conn.execute('INSERT INTO roads (src, dest, distance) VALUES (?, ?, ?)',
                 (src, dest, dist))
    conn.execute('INSERT INTO logs (action, details) VALUES (?, ?)',
                 ('add_road', f'Added road: {src} -> {dest} ({dist} km)'))
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@app.route('/api/road/list', methods=['GET'])
@login_required
def list_roads():
    conn = get_db()
    rows = conn.execute('SELECT * FROM roads').fetchall()
    conn.close()
    return jsonify({'roads': [dict(row) for row in rows]})


# ─── Request API ─────────────────────────────────────────────────────────────
@app.route('/api/request/add', methods=['POST'])
@login_required
def add_request():
    data = request.json
    city_id = data['city_id']
    priority = data['priority']
    
    # Detailed resources or default
    food = int(data.get('food', 0))
    water = int(data.get('water', 0))
    medical_kits = int(data.get('medical_kits', 0))
    rescue_teams = int(data.get('rescue_teams', 0))

    # Calculate aggregate resource needs
    required = food + water + medical_kits + (rescue_teams * 50)
    
    if required == 0 and 'required_resources' in data:
        required = int(data['required_resources'])
        # roughly divide
        food = int(required * 0.3)
        water = int(required * 0.5)
        medical_kits = int(required * 0.15)
        rescue_teams = max(1, int(required * 0.05 / 50))

    try:
        conn = get_db()
        cursor = conn.execute(
            '''INSERT INTO requests 
               (city_id, priority, required_resources, status, food, water, medical_kits, rescue_teams) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (city_id, priority, required, 'pending', food, water, medical_kits, rescue_teams)
        )
        req_id = cursor.lastrowid
        conn.execute('INSERT INTO logs (action, details) VALUES (?, ?)',
                     ('add_request', f'Added disaster request #{req_id} for city {city_id} (Food: {food}, Water: {water}, Medical: {medical_kits}, Rescue: {rescue_teams})'))
        conn.commit()
        conn.close()

        backend_add_request(city_id, priority, required)

        return jsonify({'success': True, 'request_id': req_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/request/list', methods=['GET'])
@login_required
def list_requests():
    conn = get_db()
    rows = conn.execute('''
        SELECT r.id, r.city_id, c.name as city_name, r.priority,
               r.required_resources as required, r.status,
               r.food, r.water, r.medical_kits, r.rescue_teams
        FROM requests r
        LEFT JOIN cities c ON r.city_id = c.id
        ORDER BY r.priority DESC
    ''').fetchall()
    conn.close()
    return jsonify({'requests': [dict(row) for row in rows]})


# ─── Allocation API ─────────────────────────────────────────────────────────
@app.route('/api/allocate', methods=['POST'])
@login_required
def allocate():
    try:
        result_json = backend_allocate_resources()
        result = json.loads(result_json)

        conn = get_db()
        for alloc in result['allocations']:
            if alloc['status'] == 'allocated':
                conn.execute('UPDATE requests SET status = ? WHERE id = ?',
                             ('allocated', alloc['request_id']))
                conn.execute('INSERT INTO logs (action, details) VALUES (?, ?)',
                             ('allocate', f"Allocated {alloc['allocated']} resources "
                              f"from {alloc['support_city']} to {alloc['affected_city']}"))
        conn.commit()
        conn.close()

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'allocations': []}), 500


# ─── Logs API ────────────────────────────────────────────────────────────────
@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    conn = get_db()
    rows = conn.execute('SELECT * FROM logs ORDER BY created_at DESC LIMIT 50').fetchall()
    conn.close()
    return jsonify({'logs': [dict(row) for row in rows]})


# ─── Graph / Shortest Path API ──────────────────────────────────────────────
@app.route('/api/graph-info', methods=['GET'])
@login_required
def graph_info():
    result_json = backend_graph_json()
    result = json.loads(result_json)
    return jsonify(result)


@app.route('/api/shortest-path', methods=['GET'])
@login_required
def shortest_path():
    src = int(request.args.get('src'))
    dest = int(request.args.get('dest'))

    result_json = backend_shortest_path_json(src, dest)
    result = json.loads(result_json)

    if result.get('success'):
        conn = get_db()
        conn.execute('INSERT INTO logs (action, details) VALUES (?, ?)',
                     ('shortest_path', f"Computed path from {src} to {dest}: {result['distance']} km"))
        conn.commit()
        conn.close()

    return jsonify(result)


# ─── Emergency Numbers API ──────────────────────────────────────────────────
@app.route('/api/emergency-numbers', methods=['GET'])
@login_required
def emergency_numbers():
    numbers = [
        {"name": "National Disaster Response Force (NDRF)", "number": "011-24363260", "category": "Disaster", "icon": "shield-alt"},
        {"name": "National Emergency Number", "number": "112", "category": "Emergency", "icon": "phone-alt"},
        {"name": "Police", "number": "100", "category": "Law Enforcement", "icon": "user-shield"},
        {"name": "Fire Brigade", "number": "101", "category": "Fire", "icon": "fire-extinguisher"},
        {"name": "Ambulance", "number": "102", "category": "Medical", "icon": "ambulance"},
        {"name": "Disaster Management (NDMA)", "number": "1078", "category": "Disaster", "icon": "house-damage"},
        {"name": "Women Helpline", "number": "1091", "category": "Safety", "icon": "female"},
        {"name": "Child Helpline", "number": "1098", "category": "Safety", "icon": "child"},
        {"name": "Road Accident Emergency", "number": "1073", "category": "Accident", "icon": "car-crash"},
        {"name": "Earthquake / Flood / Disaster", "number": "011-26701728", "category": "Disaster", "icon": "water"},
        {"name": "Indian Red Cross Society", "number": "011-23359379", "category": "Relief", "icon": "plus-square"},
        {"name": "Air Ambulance", "number": "9540161344", "category": "Medical", "icon": "helicopter"},
    ]
    return jsonify({'numbers': numbers})


# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug_mode, port=5000)
