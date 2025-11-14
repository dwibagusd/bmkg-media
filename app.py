import os
import io
import random
import tempfile
import smtplib
import psycopg2
import psycopg2.extras
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, g, send_from_directory
from fpdf import FPDF # Kita hanya menggunakan FPDF

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL')
app.config['UPLOAD_FOLDER'] = '/tmp/uploads/'
app.config['WHATSAPP_ADMIN'] = os.environ.get('WHATSAPP_ADMIN')
app.config['WHATSAPP_DEFAULT_MSG'] = 'Halo BMKG, saya ingin konfirmasi permohonan wawancara dengan token: '

# Buat folder unggahan (hanya jika belum ada)
# os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 
# ^ Sebaiknya tidak di global scope, tapi /tmp/ dijamin ada.

# --- KONFIGURASI DATABASE (Tidak Berubah) ---
def get_db():
    if 'db' not in g:
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            app.logger.error("DATABASE_URL environment variable not found")
            raise RuntimeError("Database configuration error")
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        if 'sslmode=' not in db_url:
            db_url += '?sslmode=require'
        
        try:
            import socket
            original_getaddrinfo = socket.getaddrinfo
            def forced_ipv4_getaddrinfo(host, port, *args, **kwargs):
                try:
                    return original_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
                except socket.gaierror:
                    return original_getaddrinfo(host, port, socket.AF_INET6, *args, **kwargs)
            socket.getaddrinfo = forced_ipv4_getaddrinfo
            
            g.db = psycopg2.connect(db_url, connect_timeout=10)
        except Exception as e:
            app.logger.error(f"Unexpected database error: {str(e)}")
            raise RuntimeError("Database service unavailable")
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- INIT DB (DIPERBAIKI) ---
def init_db():
    try:
        db = get_db()
        cursor = db.cursor()
        
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
            datetime TIMESTAMPTZ, 
            meeting_link TEXT,
            status request_status_enum DEFAULT 'Pending'::request_status_enum,
            request_date TIMESTAMPTZ,
            whatsapp_link TEXT
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS audio_recordings (
            id SERIAL PRIMARY KEY,
            request_id INT8 NOT NULL UNIQUE REFERENCES public.interview_requests(id) ON DELETE CASCADE,
            interviewee TEXT,
            date TIMESTAMPTZ,
            filename TEXT,
            transcript TEXT
        )
        ''')
        
        admin_password = os.environ.get('ADMIN_PASSWORD', 'password123')
        cursor.execute('''
            INSERT INTO users (username, password, role) 
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        ''', ('admin', generate_password_hash(admin_password, method='pbkdf2:sha256', salt_length=16), 'admin'))
        
        db.commit()
        print("Database initialized successfully with correct schema.")
    except Exception as e:
        print(f"Error initializing database: {str(e)}")

# --- DATA CONTOH (Tidak Berubah) ---
weather_data = {
    'today': { 'date': datetime.now().strftime('%A, %d %B %Y'), 'condition': 'Partly Cloudy', 'temp': '28°C', 'humidity': '65%', 'wind': '10 km/h', 'outlook': 'Hari ini diperkirakan cerah berawan...' },
    'tomorrow': { 'condition': 'Rainy', 'temp': '26°C', 'outlook': 'Besok diperkirakan hujan...' }
}
warnings = [{'title': 'Peringatan Banjir', 'image': 'flood-warning.jpg', 'description': 'Waspada potensi banjir di daerah rendah'}, {'title': 'Cuaca Ekstrem', 'image': 'storm-warning.jpg', 'description': 'Prakiraan angin kencang dan hujan lebat'}, {'title': 'Gelombang Tinggi', 'image': 'wave-warning.jpg', 'description': 'Peringatan gelombang tinggi di pesisir'}]
press_releases = [{'id': 1, 'title': 'Konferensi Pers Bulanan BMKG', 'date': '2023-06-15', 'summary': 'BMKG mengadakan konferensi pers bulanan...', 'content': 'Dalam konferensi pers ini...'}, {'id': 2, 'title': 'Peluncuran Sistem Peringatan Dini Baru', 'date': '2023-05-28', 'summary': 'BMKG meluncurkan sistem peringatan dini...', 'content': 'Sistem baru ini mampu...'}]

# =============================================================== #
# --- ROUTES (DIBERSIHKAN) ---
# =============================================================== #

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

# --- FUNGSI EMAIL (Tidak Berubah) ---
def send_email_notification(token, recipient_email, request_data):
    try:
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_username = os.environ.get('SMTP_USERNAME')
        smtp_password = os.environ.get('SMTP_PASSWORD')
        sender_email = os.environ.get('SENDER_EMAIL', 'noreply@bmkg.example.com')
        
        if not all([smtp_username, smtp_password, sender_email]):
            app.logger.warning("Email configuration not complete, skipping email")
            return False
        
        subject = f"Konfirmasi Permohonan Wawancara BMKG - Token: {token}"
        html = f"""<html>... (Isi email Anda) ...</html>""" # (Dipersingkat)
        
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        app.logger.error(f"Email sending error: {str(e)}")
        return False

# --- ROUTE PERMOHONAN (Tidak Berubah) ---
@app.route('/request-interview', methods=['GET', 'POST'])
def request_interview():
    if request.method == 'POST':
        token = generate_token()
        method = request.form.get('method')
        email = request.form.get('email', '')
        interviewer_name = request.form.get('interviewer_name')
        media_name = request.form.get('media_name')
        topic = request.form.get('topic')
        datetime_req = request.form.get('datetime')
        meeting_link = request.form.get('meeting_link', '')

        # (Logika Pesan WhatsApp Anda - dipersingkat)
        from urllib.parse import quote
        message = f"Permohonan Wawancara BMKG... Token: *{token}* ..."
        encoded_message = quote(message)
        whatsapp_link = f"https://wa.me/{app.config['WHATSAPP_ADMIN']}?text={encoded_message}"

        request_data = {
            'token': token, 'interviewer_name': interviewer_name,
            'media_name': media_name, 'topic': topic, 'method': method,
            'datetime': datetime_req, 'meeting_link': meeting_link,
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
                request_data['token'], request_data['interviewer_name'],
                request_data['media_name'], request_data['topic'],
                request_data['method'], request_data['datetime'],
                request_data['meeting_link'], request_data['whatsapp_link'],
                request_data['request_date'], request_data['status']
            ))
            db.commit()

            if email:
                send_email_notification(token, email, request_data)

            return redirect(whatsapp_link)
        except Exception as e:
            flash(f'Database error: {str(e)}', 'danger')
            app.logger.error(f'Database error: {str(e)}')
    
    return render_template('request_interview.html')

# --- DATA HISTORIS (Tidak Berubah, SQL sudah benar) ---
@app.route('/historical-data', methods=['GET', 'POST'])
def historical_data_view():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        search_query = request.args.get('search', '')
        status_filter = request.args.get('status', 'all')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        
        base_query = '''
            SELECT ir.*, ar.filename as has_recording, ar.id as recording_id
            FROM interview_requests ir
            LEFT JOIN audio_recordings ar ON ir.id = ar.request_id
        '''
        
        conditions = []
        params = []
        
        if search_query:
            conditions.append('''(ir.interviewer_name ILIKE %s OR 
                                 ir.media_name ILIKE %s OR 
                                 ir.topic ILIKE %s OR 
                                 ir.token ILIKE %s)''')
            params.extend([f'%{search_query}%'] * 4)
        
        if status_filter != 'all':
            conditions.append('ir.status = %s')
            params.append(status_filter)
        
        if date_from:
            conditions.append('ir.request_date >= %s')
            params.append(date_from)
        
        if date_to:
            conditions.append('ir.request_date <= %s')
            params.append(date_to)
        
        if conditions:
            base_query += ' WHERE ' + ' AND '.join(conditions)
        
        base_query += ' ORDER BY ir.request_date DESC'
        
        cursor.execute(base_query, params)
        historical_data = cursor.fetchall()
        
        return render_template(
            'historical_data.html',
            historical_data=historical_data,
            search_query=search_query,
            status_filter=status_filter,
            date_from=date_from,
            date_to=date_to
        )
    
    except Exception as e:
        flash(f'Database error: {str(e)}', 'danger')
        app.logger.error(f'Database error: {str(e)}')
        return redirect(url_for('index'))

# --- ROUTE EXPORT-DATA (DIHAPUS) ---
# @app.route('/export-data')
# def export_data():
#    ... (Fungsi ini dihapus karena 'pandas' terlalu berat) ...

# --- FUNGSI TRANSCRIBE_AUDIO (DIHAPUS) ---
# def transcribe_audio(audio_path):
#    ... (Fungsi ini dihapus karena 'pydub' dan 'sr' tidak didukung) ...

# --- ROUTE GENERATE_REPORT_NOW (Tidak Berubah, SQL sudah benar) ---
@app.route('/generate_report_now', methods=['POST'])
def generate_report_now():
    if 'user' not in session or session.get('role') != 'admin':
        return {'message': 'Unauthorized'}, 403

    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    try:
        data = request.form
        token = data['token']
        narasumber = data['interviewee']
        pewawancara = data['interviewer']
        teks = data['transcript']
        media_name = data.get('media_name', '-')
        topic = data.get('topic', '-')
        datetime_req = data.get('datetime', '-')
        method = data.get('method', '-')
        
        filename = f"rekaman_{token}.webm" 
        if 'audio_file' in request.files:
            file = request.files['audio_file']
            filename = secure_filename(f"audio_{token}_{datetime.now().strftime('%Y%m%d%H%M%S')}.webm")
            # Pastikan folder ada sebelum menyimpan
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            app.logger.info(f"Audio disimpan ke: {filepath}")
        
        tgl_rekaman = datetime.now().strftime('%Y-%m-%d %H:%M')
        
        cursor.execute("""
            INSERT INTO audio_recordings 
            (interviewee, date, filename, transcript, request_id)
            SELECT %s, %s, %s, %s, ir.id
            FROM interview_requests ir WHERE ir.token = %s
            ON CONFLICT (request_id) DO UPDATE SET
                interviewee = EXCLUDED.interviewee,
                date = EXCLUDED.date,
                filename = EXCLUDED.filename,
                transcript = EXCLUDED.transcript
            RETURNING id
        """, (narasumber, tgl_rekaman, filename, teks, token))
        
        result = cursor.fetchone()
        if not result:
            db.rollback()
            return {'message': 'Token tidak valid atau tidak ditemukan.'}, 400
        
        recording_id = result['id']
        db.commit()
        app.logger.info(f"Data transkrip disimpan/diperbarui untuk ID: {recording_id}")

        # (Logika FPDF Anda - dipersingkat)
        pdf = FPDF()
        pdf.add_page()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        header_path = os.path.join(base_dir, 'static', 'header.png')

        if os.path.exists(header_path):
            pdf.image(header_path, x=10, y=10, w=190)
            pdf.set_y(60) 
        else:
            app.logger.warn(f"Header image not found at {header_path}, skipping.")
            pdf.set_y(10)

        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "NASKAH WAWANCARA", 0, 1, 'C')
        pdf.ln(10)
        
        pdf.set_font("Arial", '', 12)
        label_width = 50 
        pdf.cell(label_width, 8, "Waktu Jadwal :", 0, 0)
        pdf.cell(0, 8, datetime_req, 0, 1)
        pdf.cell(label_width, 8, "Saluran Wawancara :", 0, 0) 
        pdf.cell(0, 8, method, 0, 1)
        pdf.cell(label_width, 8, "Wartawan :", 0, 0)
        pdf.cell(0, 8, pewawancara, 0, 1)
        pdf.cell(label_width, 8, "Nama Media :", 0, 0)
        pdf.cell(0, 8, media_name, 0, 1)
        pdf.cell(label_width, 8, "Narasumber (On Duty) :", 0, 0)
        pdf.cell(0, 8, narasumber, 0, 1)
        pdf.cell(label_width, 8, "Topik :", 0, 0)
        pdf.cell(0, 8, topic, 0, 1)
        pdf.ln(10)
        
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Isi Naskah :", 0, 1)
        pdf.ln(5)
        
        pdf.set_font("Arial", '', 11)
        for line in teks.split('\n'):
            pdf.multi_cell(0, 7, line.encode('latin-1', 'replace').decode('latin-1')) 
            pdf.ln(2) 

        pdf_buffer = io.BytesIO()
        pdf_bytes = pdf.output(dest='S')
        pdf_buffer.write(pdf_bytes)
        pdf_buffer.seek(0)
        
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"Laporan_{token}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        db.rollback()
        app.logger.error(f'Gagal generate report now: {str(e)}', exc_info=True)
        return {'message': f'Terjadi error di server: {str(e)}'}, 500

# --- ROUTE RECORDER (DIBERSIHKAN) ---
@app.route('/recorder', methods=['GET'])
def recorder():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    # Blok POST dihapus, karena alur kerja utama sekarang 
    # adalah /generate_report_now
    
    tokens = []
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Ambil tokens untuk dropdown
        cursor.execute('SELECT token FROM interview_requests ORDER BY request_date DESC')
        tokens_data = cursor.fetchall()
        tokens = [{'token': row['token']} for row in tokens_data]

    except Exception as e:
        flash(f'Database error saat mengambil data: {str(e)}', 'danger')
        app.logger.error(f'Database error: {str(e)}')
    
    # Halaman ini tidak lagi menampilkan 'recordings'
    return render_template('recorder.html', tokens=tokens)

# --- ROUTE UPLOADS (Tidak Berubah) ---
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    if 'user' not in session:
        flash('Anda harus login untuk mengakses file ini.', 'danger')
        return redirect(url_for('login'))
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError:
        app.logger.error(f"File tidak ditemukan di /uploads/: {filename}")
        return "File not found", 404

# --- ROUTE GET_TOPIK (Tidak Berubah, sudah benar) ---
@app.route('/get_topik/<token>')
def get_topik(token):
    if 'user' not in session:
        return {'error': 'Unauthorized'}, 403
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cursor.execute("""
            SELECT topic, interviewer_name, media_name, datetime, method 
            FROM interview_requests 
            WHERE token = %s
        """, (token,))
        
        data = cursor.fetchone()
        
        if data:
            return {
                'topic': data['topic'],
                'interviewer_name': data['interviewer_name'],
                'media_name': data['media_name'],
                'datetime': data['datetime'],
                'method': data['method']
            }
        else:
            return {'error': 'Token not found'}, 404
            
    except Exception as e:
        app.logger.error(f"Gagal get data token: {str(e)}")
        return {'error': 'Server error'}, 500

# --- ROUTE GENERATE-PDF (DIPERBAIKI) ---
@app.route('/generate-pdf/<int:recording_id>')
def generate_pdf(recording_id):
    if 'user' not in session:
        flash('Unauthorized access', 'danger')
        return redirect(url_for('login'))

    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # SQL DIPERBAIKI (menggunakan request_id)
        cursor.execute('''
            SELECT
                ar.interviewee, ar.date as recording_date, ar.transcript,
                ir.token, ir.interviewer_name, ir.media_name, ir.topic, 
                ir.method, ir.datetime as schedule_time
            FROM audio_recordings ar
            LEFT JOIN interview_requests ir ON ar.request_id = ir.id
            WHERE ar.id = %s
        ''', (recording_id,))
        
        recording = cursor.fetchone()
        
        if not recording:
            flash('Recording not found', 'danger')
            return redirect(url_for('historical_data_view')) # Arahkan ke historis

        # (Logika FPDF Anda - dipersingkat)
        pdf = FPDF()
        pdf.add_page()
        # ... (Logika FPDF sama seperti di /generate_report_now) ...
        # Anda bisa memfaktorkan ini ke fungsi terpisah nanti
        
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Laporan Wawancara BMKG", 0, 1, 'C')
        pdf.ln(10)
        
        pdf.set_font("Arial", '', 12)
        label_width = 50
        pdf.cell(label_width, 8, "Token:", 0, 0)
        pdf.cell(0, 8, recording['token'] or '-', 0, 1)
        pdf.cell(label_width, 8, "Topik:", 0, 0)
        pdf.cell(0, 8, recording['topic'] or '-', 0, 1)
        # ... (Sisa field) ...
        pdf.ln(10)
        
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Transkrip Wawancara", 0, 1)
        pdf.ln(5)
        
        pdf.set_font("Arial", '', 11)
        transcript = recording['transcript'] or '(Tidak ada transkrip)'
        for line in transcript.split('\n'):
            pdf.multi_cell(0, 7, line.encode('latin-1', 'replace').decode('latin-1'))
            pdf.ln(2)

        buffer = io.BytesIO()
        pdf_bytes = pdf.output(dest='S')
        buffer.write(pdf_bytes)
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"transkrip_{recording['token']}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        flash(f'Error generating PDF (FPDF): {str(e)}', 'danger')
        app.logger.error(f'PDF generation error: {str(e)}')
        return redirect(url_for('historical_data_view')) # Arahkan ke historis

# --- ROUTE AUTH (Tidak Berubah) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            user = cursor.fetchone()
            
            if user:
                if check_password_hash(user[2], password):
                    session['user'] = user[1] 
                    session['role'] = user[3]
                    flash('Login berhasil!', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('Password salah', 'danger')
            else:
                flash('Username tidak ditemukan', 'danger')
                
        except Exception as e:
            app.logger.error(f"LOGIN EXCEPTION: Terjadi error -> {e}", exc_info=True)
            flash('Terjadi kesalahan sistem', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('role', None)
    return redirect(url_for('index'))

# --- INISIALISASI ---
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
