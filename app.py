# Import Library
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from datetime import datetime, timedelta
import os
import json
import random
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
# import sqlite3
from flask import g
import psycopg2 # untuk PostgreSQL
from flask import g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['UPLOAD_FOLDER'] = 'static/uploads/'
app.config['DATABASE'] = 'bmkg_media.db'
app.config['ALLOWED_EXTENSIONS'] = {'wav', 'mp3', 'ogg'}
app.config['WHATSAPP_ADMIN'] = '6281932689046'  # Nomor WhatsApp admin dengan kode negara
app.config['WHATSAPP_DEFAULT_MSG'] = 'Halo BMKG, saya ingin konfirmasi permohonan wawancara dengan token: '

# Buat folder unggahan
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# In-memory database for demo purposes
users = {
    'admin': {'password': 'password123', 'role': 'admin'},
    'user1': {'password': 'user123', 'role': 'user'}
}


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# Konfigurasi database
DATABASE = 'bmkg_media.db'

# def get_db():
#     db = getattr(g, '_database', None)
#     if db is None:
#         db = g._database = pymysql.connect(
#             host='localhost',
#             user='bmkg_user',
#             password='password',
#             database='bmkg_media',
#             cursorclass=pymysql.cursors.DictCursor
#         )
#     return db

# @app.teardown_appcontext
# def close_connection(exception):
#     db = getattr(g, '_database', None)
#     if db is not None:
#         db.close()

# def init_db():
#     with app.app_context():
#         db = get_db()
#         cursor = db.cursor()
        
#         # Buat tabel jika belum ada
#         cursor.execute('''
#         CREATE TABLE IF NOT EXISTS users (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             username TEXT UNIQUE NOT NULL,
#             password TEXT NOT NULL,
#             role TEXT NOT NULL
#         )
#         ''')
        
#         cursor.execute('''
#         CREATE TABLE IF NOT EXISTS interview_requests (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             token TEXT UNIQUE NOT NULL,
#             interviewer_name TEXT NOT NULL,
#             media_name TEXT NOT NULL,
#             topic TEXT NOT NULL,
#             method TEXT NOT NULL,
#             datetime TEXT NOT NULL,
#             meeting_link TEXT,
#             status TEXT DEFAULT 'Pending',
#             request_date TEXT NOT NULL
#         )
#         ''')
        
#         cursor.execute('''
#         CREATE TABLE IF NOT EXISTS audio_recordings (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             token TEXT NOT NULL,
#             interviewee TEXT NOT NULL,
#             interviewer TEXT NOT NULL,
#             date TEXT NOT NULL,
#             filename TEXT NOT NULL,
#             transcript TEXT,
#             FOREIGN KEY (token) REFERENCES interview_requests (token)
#         )
#         ''')
#         cursor.execute('''
#                        ALTER TABLE interview_requests ADD COLUMN whatsapp_link TEXT
#                        ''')
        
#         # Insert admin user jika belum ada
#         try:
#             cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
#                           ('admin', 'password123', 'admin'))
#             cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
#                           ('user1', 'user123', 'user'))
#         except sqlite3.IntegrityError:
#             pass  # User sudah ada
            
#         db.commit()

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(os.environ['DATABASE_URL'])
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # Buat tabel jika belum ada
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS interview_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            interviewer_name TEXT NOT NULL,
            media_name TEXT NOT NULL,
            topic TEXT NOT NULL,
            method TEXT NOT NULL,
            datetime TEXT NOT NULL,
            meeting_link TEXT,
            status TEXT DEFAULT 'Pending',
            request_date TEXT NOT NULL
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS audio_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            interviewee TEXT NOT NULL,
            interviewer TEXT NOT NULL,
            date TEXT NOT NULL,
            filename TEXT NOT NULL,
            transcript TEXT,
            FOREIGN KEY (token) REFERENCES interview_requests (token)
        )
        ''')
        
        cursor.execute('''
                       ALTER TABLE interview_requests ADD COLUMN whatsapp_link TEXT
                       ''')
        
        # Insert admin user jika belum ada
        try:
            cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
                          ('admin', 'password123', 'admin'))
            cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
                          ('user1', 'user123', 'user'))
        except sqlite3.IntegrityError:
            pass  # User sudah ada
            
        db.commit()


# Migrasi data
def migrate_data():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # Migrasi users
        for username, data in users.items():
            try:
                cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
                              (username, data['password'], data['role']))
            except sqlite3.IntegrityError:
                pass  # User sudah ada
        
        # Migrasi interview requests
        for request in interview_requests:
            cursor.execute('''
                INSERT OR IGNORE INTO interview_requests 
                (token, interviewer_name, media_name, topic, method, datetime, meeting_link, status, request_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request['token'],
                request['interviewer_name'],
                request['media_name'],
                request['topic'],
                request['method'],
                request['datetime'],
                request.get('meeting_link', ''),
                request['status'],
                request['request_date']
            ))
        
        # Migrasi audio recordings
        for recording in audio_recordings:
            cursor.execute('''
                INSERT OR IGNORE INTO audio_recordings 
                (token, interviewee, interviewer, date, filename, transcript)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                recording['token'],
                recording['interviewee'],
                recording['interviewer'],
                recording['date'],
                recording['filename'],
                recording['transcript']
            ))
        
        db.commit()
        
# =============================================================== #
# =============================================================== #

weather_data = {
    'today': {
        'date': datetime.now().strftime('%A, %d %B %Y'),
        'condition': 'Partly Cloudy',
        'temp': '28°C',
        'humidity': '65%',
        'wind': '10 km/h',
        'outlook': 'Hari ini diperkirakan cerah berawan dengan kemungkinan hujan ringan di sore hari. Warga diimbau untuk tetap waspada terhadap perubahan cuaca.'
    },
    'tomorrow': {
        'condition': 'Rainy',
        'temp': '26°C',
        'outlook': 'Besok diperkirakan hujan dengan intensitas sedang. Warga diharapkan membawa payung atau jas hujan saat beraktivitas di luar ruangan.'
    }
}

warnings = [
    {'title': 'Peringatan Banjir', 'image': 'flood-warning.jpg', 'description': 'Waspada potensi banjir di daerah rendah'},
    {'title': 'Cuaca Ekstrem', 'image': 'storm-warning.jpg', 'description': 'Prakiraan angin kencang dan hujan lebat'},
    {'title': 'Gelombang Tinggi', 'image': 'wave-warning.jpg', 'description': 'Peringatan gelombang tinggi di pesisir'}
]

press_releases = [
    {
        'id': 1,
        'title': 'Konferensi Pers Bulanan BMKG',
        'date': '2023-06-15',
        'summary': 'BMKG mengadakan konferensi pers bulanan untuk membahas perkembangan iklim terkini.',
        'content': 'Dalam konferensi pers ini, BMKG akan memaparkan analisis kondisi cuaca dan iklim selama sebulan terakhir serta prakiraan untuk bulan depan...'
    },
    {
        'id': 2,
        'title': 'Peluncuran Sistem Peringatan Dini Baru',
        'date': '2023-05-28',
        'summary': 'BMKG meluncurkan sistem peringatan dini terbaru dengan teknologi canggih.',
        'content': 'Sistem baru ini mampu mendeteksi potensi bencana meteorologi lebih cepat dan akurat...'
    }
]

interview_requests = []
historical_data = []
audio_recordings = []

def generate_token():
    return ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=8))

#
@app.context_processor
def inject_users():
    return dict(users=users)

@app.route('/')
def index():
    """Homepage with press releases and weather info"""
    return render_template('index.html', 
                         press_releases=press_releases[:3],
                         weather=weather_data['today'],
                         warnings=warnings)

# Press Release
@app.route('/press-release/<int:release_id>')
def press_release_detail(release_id):
    """Press release detail page"""
    release = next((r for r in press_releases if r['id'] == release_id), None)
    if not release:
        flash('Press release not found', 'danger')
        return redirect(url_for('index'))
    return render_template('press_release_detail.html', release=release)

# Request Interview
@app.route('/request-interview', methods=['GET', 'POST'])
def request_interview():
    if request.method == 'POST':
        token = generate_token()
        method = request.form.get('method')
        
        # Metode WhatsApp generate link
        whatsapp_link = ''
        if method == 'whatsapp':
            message = f"{app.config['WHATSAPP_DEFAULT_MSG']}{token}"
            whatsapp_link = f"https://wa.me/{app.config['WHATSAPP_ADMIN']}?text={message}"
        
        request_data = {
            'token': token,
            'interviewer_name': request.form.get('interviewer_name'),
            'media_name': request.form.get('media_name'),
            'topic': request.form.get('topic'),
            'method': method,
            'datetime': request.form.get('datetime'),
            'meeting_link': request.form.get('meeting_link', ''),
            'whatsapp_link': whatsapp_link,  # Simpan link WhatsApp
            'request_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'status': 'Pending'
        }
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO interview_requests 
            (token, interviewer_name, media_name, topic, method, datetime, meeting_link, whatsapp_link, request_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_data['token'],
            request_data['interviewer_name'],
            request_data['media_name'],
            request_data['topic'],
            request_data['method'],
            request_data['datetime'],
            request_data['meeting_link'],
            request_data['whatsapp_link'],
            request_data['request_date'],
            request_data['status']
        ))
        db.commit()
        
        # Metode WhatsApp redirect ke chat
        if method == 'whatsapp':
            return redirect(whatsapp_link)
        
        flash(f'Permohonan wawancara berhasil! Token Anda: {token}', 'success')
        return redirect(url_for('request_interview'))
    
    return render_template('request_interview.html')

# Historical Data
@app.route('/historical-data')
def historical_data_view():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute('''
        SELECT ir.*, ar.filename as has_recording
        FROM interview_requests ir
        LEFT JOIN audio_recordings ar ON ir.token = ar.token
        ORDER BY ir.request_date DESC
    ''')
    historical_data = cursor.fetchall()
    
    return render_template('historical_data.html', historical_data=historical_data)

# Recorder
@app.route('/recorder', methods=['GET', 'POST'])
def recorder():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    
    # Cek role user
    cursor.execute('SELECT role FROM users WHERE username = ?', (session['user'],))
    user = cursor.fetchone()
    
    if not user or user['role'] != 'admin':
        flash('Unauthorized access', 'danger')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        if 'audio_file' not in request.files:
            flash('No audio file uploaded', 'danger')
            return redirect(request.url)
        
        file = request.files['audio_file']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            cursor.execute('''
                INSERT INTO audio_recordings 
                (token, interviewee, interviewer, date, filename, transcript)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                request.form.get('token'),
                request.form.get('interviewee'),
                request.form.get('interviewer'),
                datetime.now().strftime('%Y-%m-%d %H:%M'),
                filename,
                request.form.get('transcript', '')
            ))
            db.commit()
            
            flash('Recording saved successfully!', 'success')
            return redirect(url_for('recorder'))
    
    # Ambil data rekaman dari database
    cursor.execute('SELECT * FROM audio_recordings ORDER BY date DESC')
    recordings = cursor.fetchall()
    
    return render_template('recorder.html', recordings=recordings)

# Generate PDF
@app.route('/generate-pdf/<int:recording_id>')
def generate_pdf(recording_id):
    if 'user' not in session:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    
    # Cek role user
    cursor.execute('SELECT role FROM users WHERE username = ?', (session['user'],))
    user = cursor.fetchone()
    
    if not user or user['role'] != 'admin':
        flash('Unauthorized access', 'danger')
        return redirect(url_for('index'))
    
    cursor.execute('SELECT * FROM audio_recordings WHERE id = ?', (recording_id,))
    recording = cursor.fetchone()
    
    if not recording:
        flash('Recording not found', 'danger')
        return redirect(url_for('recorder'))
    
    # Create PDF (sama seperti sebelumnya)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    
    # Add content to PDF
    p.setFont("Helvetica-Bold", 16)
    p.drawString(100, 750, "Laporan Wawancara BMKG")
    
    p.setFont("Helvetica", 12)
    p.drawString(100, 720, f"Token: {recording['token']}")
    p.drawString(100, 700, f"Tanggal: {recording['date']}")
    p.drawString(100, 680, f"Pewawancara: {recording['interviewer']}")
    p.drawString(100, 660, f"Narasumber: {recording['interviewee']}")
    
    p.drawString(100, 620, "Transkrip Wawancara:")
    text = p.beginText(100, 600)
    text.setFont("Helvetica", 10)
    
    # Add transcript with word wrap
    for line in recording['transcript'].split('\n'):
        for part in [line[i:i+80] for i in range(0, len(line), 80)]:
            text.textLine(part)
    
    p.drawText(text)
    
    p.showPage()
    p.save()
    
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"transkrip_{recording['token']}.pdf", mimetype='application/pdf')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db = get_db()
        cursor = db.cursor()
        
        try:
            # Cari user di database
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            
            if user:
                # Verifikasi password yang di-hash
                if check_password_hash(user['password'], password):
                    session['user'] = user['username']
                    flash('Login berhasil!', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('Password salah', 'danger')
            else:
                flash('Username tidak ditemukan', 'danger')
                
        except sqlite3.Error as e:
            flash('Terjadi kesalahan sistem', 'danger')
            app.logger.error(f'Database error: {str(e)}')
    
    return render_template('login.html')

# Logout
@app.route('/logout')
def logout():
    """Logout"""
    session.pop('user', None)
    return redirect(url_for('index'))

# # Saat membuat user baru (untuk register)
# hashed_pw = generate_password_hash(password)

# # Saat memverifikasi login
# if check_password_hash(user['password'], password):
#     # Login berhasil


if __name__ == "__main__":
    app.run(debug=True)
    # from waitress import serve
    # serve(app, host="0.0.0.0", port=8080)
    # migrate_data()
    with app.app_context():
        init_db()
    # init_db()
