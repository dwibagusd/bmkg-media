from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, g
from datetime import datetime
import os
import random
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from hashlib import sha256  

# Load environment variables
load_dotenv()  # Untuk development lokal, di Vercel tidak diperlukan

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL')  # Wajib!
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD')  # Wajib!
app.config['USER_PASSWORD'] = os.environ.get('USER_PASSWORD')  # Wajib!
app.config['UPLOAD_FOLDER'] = 'static/uploads/'
app.config['ALLOWED_EXTENSIONS'] = {'wav', 'mp3', 'ogg'}
app.config['WHATSAPP_ADMIN'] = os.environ.get('WHATSAPP_ADMIN')
app.config['WHATSAPP_DEFAULT_MSG'] = 'Halo BMKG, saya ingin konfirmasi permohonan wawancara dengan token: '

# Buat folder unggahan
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Perbaikan fungsi hash password
def generate_password_hash(password):
    return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Konfigurasi koneksi database
def get_db_config():
    return {
        'user': os.getenv('DB_USER', os.environ.get('DB_USER')),
        'password': os.getenv('DB_PASSWORD', os.environ.get('DB_PASSWORD')),
        'host': os.getenv('DB_HOST', os.environ.get('DB_HOST')),
        'port': os.getenv('DB_PORT', os.environ.get('DB_PORT', '5432')),
        'dbname': os.getenv('DB_NAME', os.environ.get('DB_NAME'))
    }
    
# Konfigurasi database PostgreSQL
def get_db():
    if 'db' not in g:
        # Dapatkan URL dari environment variable Vercel
        db_url = os.environ.get('DATABASE_URL')
        
        if not db_url:
            app.logger.error("DATABASE_URL environment variable not found")
            raise RuntimeError("Database configuration error")

        # Konversi URL format jika diperlukan
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)

        # Tambahkan parameter SSL jika belum ada
        if 'sslmode=' not in db_url:
            db_url += '?sslmode=require'

        try:
            # Backup fungsi DNS asli
            import socket
            original_getaddrinfo = socket.getaddrinfo

            # Force IPv4 jika diperlukan
            def forced_ipv4_getaddrinfo(host, port, *args, **kwargs):
                try:
                    # Coba IPv4 dulu
                    return original_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
                except socket.gaierror:
                    # Fallback ke IPv6 jika IPv4 gagal
                    return original_getaddrinfo(host, port, socket.AF_INET6, *args, **kwargs)

            socket.getaddrinfo = forced_ipv4_getaddrinfo

            # Buat koneksi dengan timeout dan konfigurasi khusus
            g.db = psycopg2.connect(
                db_url,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5
            )

            # Test koneksi langsung
            with g.db.cursor() as cur:
                cur.execute("SELECT 1")
                if cur.fetchone()[0] != 1:
                    raise RuntimeError("Database connection test failed")

            app.logger.info("Database connection established successfully")

        except psycopg2.OperationalError as e:
            app.logger.error(f"Operational Error: {str(e)}")
            # Restore DNS resolver asli
            socket.getaddrinfo = original_getaddrinfo
            raise RuntimeError("Could not connect to database. Please try again later.")
            
        except Exception as e:
            app.logger.error(f"Unexpected database error: {str(e)}")
            raise RuntimeError("Database service unavailable")

    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Perbaikan sintaks untuk PostgreSQL
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS interview_requests (
            id SERIAL PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            interviewer_name TEXT NOT NULL,
            media_name TEXT NOT NULL,
            topic TEXT NOT NULL,
            method TEXT NOT NULL,
            datetime TEXT NOT NULL,
            meeting_link TEXT,
            status TEXT DEFAULT 'Pending',
            request_date TEXT NOT NULL,
            whatsapp_link TEXT
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS audio_recordings (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            interviewee TEXT NOT NULL,
            interviewer TEXT NOT NULL,
            date TEXT NOT NULL,
            filename TEXT NOT NULL,
            transcript TEXT
        )
        ''')
        
        # Gunakan password dari environment variables
        admin_pass = os.getenv('ADMIN_PASSWORD', os.environ.get('ADMIN_PASSWORD'))
        user_pass = os.getenv('USER_PASSWORD', os.environ.get('USER_PASSWORD'))

        if not admin_pass or not user_pass:
            raise RuntimeError("Admin or user password not set in environment")
        
        cursor.execute('''
            INSERT INTO users (username, password, role) 
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        ''', ('admin', generate_password_hash(admin_pass, method='pbkdf2:sha256'), 'admin'))
        
        cursor.execute('''
            INSERT INTO users (username, password, role) 
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        ''', ('user1', generate_password_hash(user_password), 'user'))
        
        db.commit()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Error initializing database: {str(e)}")

# =============================================================== #
# Data contoh
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

# =============================================================== #
# Routes
@app.route('/')
def index():
    return render_template('index.html', 
                         press_releases=press_releases[:3],
                         weather=weather_data['today'],
                         warnings=warnings)

@app.route('/press-release/<int:release_id>')
def press_release_detail(release_id):
    release = next((r for r in press_releases if r['id'] == release_id), None)
    if not release:
        flash('Press release not found', 'danger')
        return redirect(url_for('index'))
    return render_template('press_release_detail.html', release=release)

def generate_token():
    return ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=8))

@app.route('/request-interview', methods=['GET', 'POST'])
def request_interview():
    if request.method == 'POST':
        token = generate_token()
        method = request.form.get('method')
        
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
            'whatsapp_link': whatsapp_link,
            'request_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'status': 'Pending'
        }
        
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute('''
                INSERT INTO interview_requests 
                (token, interviewer_name, media_name, topic, method, datetime, meeting_link, whatsapp_link, request_date, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            
            if method == 'whatsapp':
                return redirect(whatsapp_link)
            
            flash(f'Permohonan wawancara berhasil! Token Anda: {token}', 'success')
            return redirect(url_for('request_interview'))
        
        except Exception as e:
            flash(f'Database error: {str(e)}', 'danger')
            app.logger.error(f'Database error: {str(e)}')
    
    return render_template('request_interview.html')

@app.route('/historical-data')
def historical_data_view():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    try:
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
    
    except Exception as e:
        flash(f'Database error: {str(e)}', 'danger')
        app.logger.error(f'Database error: {str(e)}')
        return redirect(url_for('index'))

@app.route('/recorder', methods=['GET', 'POST'])
def recorder():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT role FROM users WHERE username = %s', (session['user'],))
        user = cursor.fetchone()
        
        if not user or user[0] != 'admin':
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
                    VALUES (%s, %s, %s, %s, %s, %s)
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
        
        cursor.execute('SELECT * FROM audio_recordings ORDER BY date DESC')
        recordings = cursor.fetchall()
        
        return render_template('recorder.html', recordings=recordings)
    
    except Exception as e:
        flash(f'Database error: {str(e)}', 'danger')
        app.logger.error(f'Database error: {str(e)}')
        return redirect(url_for('index'))

@app.route('/generate-pdf/<int:recording_id>')
def generate_pdf(recording_id):
    if 'user' not in session:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('login'))
    
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT role FROM users WHERE username = %s', (session['user'],))
        user = cursor.fetchone()
        
        if not user or user[0] != 'admin':
            flash('Unauthorized access', 'danger')
            return redirect(url_for('index'))
        
        cursor.execute('SELECT * FROM audio_recordings WHERE id = %s', (recording_id,))
        recording = cursor.fetchone()
        
        if not recording:
            flash('Recording not found', 'danger')
            return redirect(url_for('recorder'))
        
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, 750, "Laporan Wawancara BMKG")
        
        p.setFont("Helvetica", 12)
        p.drawString(100, 720, f"Token: {recording[1]}")
        p.drawString(100, 700, f"Tanggal: {recording[4]}")
        p.drawString(100, 680, f"Pewawancara: {recording[3]}")
        p.drawString(100, 660, f"Narasumber: {recording[2]}")
        
        p.drawString(100, 620, "Transkrip Wawancara:")
        text = p.beginText(100, 600)
        text.setFont("Helvetica", 10)
        
        transcript = recording[6] or ''
        for line in transcript.split('\n'):
            for part in [line[i:i+80] for i in range(0, len(line), 80)]:
                text.textLine(part)
        
        p.drawText(text)
        p.showPage()
        p.save()
        
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"transkrip_{recording[1]}.pdf", mimetype='application/pdf')
    
    except Exception as e:
        flash(f'Error generating PDF: {str(e)}', 'danger')
        app.logger.error(f'PDF generation error: {str(e)}')
        return redirect(url_for('recorder'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            
            with get_db().cursor() as cur:
                cur.execute(
                    "SELECT username, password, role FROM users WHERE username = %s", 
                    (username,)
                )
                user = cur.fetchone()
                
                if user and check_password_hash(user[1], password):
                    session.update({
                        'user': user[0],
                        'role': user[2]
                    })
                    return redirect(url_for('index'))
                
            flash('Invalid username or password', 'danger')
            
        except KeyError:
            flash('Please fill all fields', 'danger')
        except Exception as e:
            app.logger.error(f"Login error: {str(e)}")
            flash('System error during login', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# Inisialisasi database saat aplikasi dimulai
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
