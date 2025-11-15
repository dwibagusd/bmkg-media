import os
import io
import random
import tempfile
import smtplib
import psycopg2
import psycopg2.extras
import json
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, g, send_from_directory
from fpdf import FPDF 

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL')
app.config['UPLOAD_FOLDER'] = '/tmp/uploads/' # /tmp/ adalah satu-satunya folder Vercel bisa tulis
app.config['WHATSAPP_ADMIN'] = os.environ.get('WHATSAPP_ADMIN')
app.config['WHATSAPP_DEFAULT_MSG'] = 'Halo BMKG, saya ingin konfirmasi permohonan wawancara dengan token: '

# --- KONFIGURASI DATABASE ---
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
            # Mengatasi masalah DNS Vercel/IPv6
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
        
        # Buat tipe ENUM HANYA JIKA BELUM ADA
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'request_status_enum') THEN
                    CREATE TYPE request_status_enum AS ENUM ('Pending', 'Approved', 'Completed', 'Canceled');
                END IF;
            END$$;
        """)
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
        ''')
        
        # Menggunakan tipe data yang BENAR (TIMESTAMPTZ, ENUM)
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
        
        # Menggunakan skema yang BENAR (request_id)
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

        # Tabel untuk cache WordCloud
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS keyword_results (
            word TEXT PRIMARY KEY,
            weight FLOAT4 NOT NULL
        )
        ''')
        
        admin_password = os.environ.get('ADMIN_PASSWORD', 'password123')
        cursor.execute('''
            INSERT INTO users (username, password, role) 
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        ''', ('admin', generate_password_hash(admin_password, method='pbkdf2:sha256', salt_length=16), 'admin'))
        
        db.commit()
        print("Database initialized successfully.")
    except psycopg2.errors.DuplicateObject:
        # Jika tipe ENUM atau tabel sudah ada, lanjutkan saja
        print("Database types/tables already exist, skipping creation.")
        db.rollback()
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        db.rollback()

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
        html = f"""<html>...(Isi email Anda)...</html>""" # Dipersingkat
        
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

# --- ROUTE PERMOHONAN (DIPERBAIKI untuk TIMESTAMPTZ) ---
@app.route('/request-interview', methods=['GET', 'POST'])
def request_interview():
    if request.method == 'POST':
        token = generate_token()
        method = request.form.get('method')
        email = request.form.get('email', '')
        interviewer_name = request.form.get('interviewer_name')
        media_name = request.form.get('media_name')
        topic = request.form.get('topic')
        datetime_req_str = request.form.get('datetime')
        meeting_link = request.form.get('meeting_link', '')

        # Konversi string datetime ke objek datetime
        datetime_req = None
        if datetime_req_str:
            try:
                # Pastikan format input adalah ISO (YYYY-MM-DDTHH:MM)
                datetime_req = datetime.fromisoformat(datetime_req_str)
            except ValueError:
                flash('Format datetime tidak valid. Gunakan format YYYY-MM-DDTHH:MM.', 'danger')
                return render_template('request_interview.html')
        
        request_date = datetime.now() # Gunakan objek datetime

# Format string waktu agar lebih mudah dibaca (jika ada)
        waktu_str = datetime_req_str
        if datetime_req:
             # Ubah dari "2025-11-14T15:00" menjadi "Jumat, 14 Nov 2025 15:00"
             try:
                 waktu_str = datetime_req.strftime('%A, %d %b %Y %H:%M')
             except Exception:
                 pass # Biarkan string aslinya jika format gagal

        # Buat template pesan WhatsApp yang lengkap
        message = f"""Permohonan Wawancara BMKG
        Halo Admin BMKG 👋,
        Berikut detail permohonan wawancara yang baru saja diajukan:
        - Token: *{token}*
        - Nama Pewawancara: *{interviewer_name}*
        - Media: *{media_name}*
        - Topik Wawancara: *{topic}*
        - Tanggal & Waktu: *{waktu_str}*
        - Metode: *{method.capitalize()}*
        - Meeting Link (jika ada): {meeting_link if meeting_link else '-'}
        Mohon tindak lanjut konfirmasi jadwal.
        Terima kasih 🙏"""

        # Encode pesan agar formatnya aman di URL WhatsApp
        from urllib.parse import quote
        encoded_message = quote(message)
        whatsapp_link = f"https://wa.me/{app.config['WHATSAPP_ADMIN']}?text={encoded_message}"

        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute('''
                INSERT INTO interview_requests 
                (token, interviewer_name, media_name, topic, method, datetime, meeting_link, whatsapp_link, request_date, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                token, interviewer_name, media_name, topic, method,
                datetime_req, meeting_link, whatsapp_link,
                request_date, 'Pending'
            ))
            db.commit()

            if email:
                request_data_email = { 'interviewer_name': interviewer_name, 'media_name': media_name, 'topic': topic, 'method': method, 'datetime': datetime_req_str }
                send_email_notification(token, email, request_data_email)

            return redirect(whatsapp_link)
        except Exception as e:
            db.rollback()
            flash(f'Database error: {str(e)}', 'danger')
            app.logger.error(f'Database error: {str(e)}')
    
    return render_template('request_interview.html')

# --- DATA HISTORIS (SQL sudah benar) ---
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
            conditions.append('''(ir.interviewer_name ILIKE %s OR ir.media_name ILIKE %s OR ir.topic ILIKE %s OR ir.token ILIKE %s)''')
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

# --- ROUTE GENERATE_REPORT_NOW ---
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
        datetime_req_str = data.get('datetime', '-')
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
        
        tgl_rekaman = datetime.now() # Gunakan objek datetime
        
        # SQL SUDAH BENAR
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

        pdf = FPDF()
        pdf.add_page()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        font_path = os.path.join(base_dir, 'static', 'Arial.ttf')
        if not os.path.exists(font_path):
            app.logger.error(f"File font tidak ditemukan di: {font_path}")
        try:
            pdf.add_font('Arial', '', font_path, uni=True)
        except Exception as e:
            app.logger.error(f"Gagal memuat font: {e}")
            pdf.set_font("Arial", 'B', 16)
            
        header_path = os.path.join(base_dir, 'static', 'header.png')
        if os.path.exists(header_path):
            pdf.image(header_path, x=0, y=0 w=210)
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
        pdf.cell(0, 8, datetime_req_str, 0, 1) # Gunakan string
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
            pdf.multi_cell(0, 7, line) 
            pdf.ln(2) 

        pdf_buffer = io.BytesIO()
        pdf_bytes = pdf.output()
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

# --- ROUTE RECORDER (HANYA GET) ---
@app.route('/recorder')
def recorder():
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    tokens = []
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT token FROM interview_requests ORDER BY request_date DESC')
        tokens_data = cursor.fetchall()
        tokens = [{'token': row['token']} for row in tokens_data]

    except Exception as e:
        flash(f'Database error saat mengambil data: {str(e)}', 'danger')
        app.logger.error(f'Database error: {str(e)}')
    
    return render_template('recorder.html', tokens=tokens)

# --- ROUTE UPLOADS ---
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    if 'user' not in session:
        flash('Anda harus login untuk mengakses file ini.', 'danger')
        return redirect(url_for('login'))
    try:
        # Pastikan folder ada
        folder = app.config['UPLOAD_FOLDER']
        if not os.path.isdir(folder):
             os.makedirs(folder, exist_ok=True)
        return send_from_directory(folder, filename, as_attachment=False)
    except FileNotFoundError:
        app.logger.error(f"File tidak ditemukan di /uploads/: {filename}")
        return "File not found", 404

# --- ROUTE GET_TOPIK (DIPERBAIKI untuk Waktu) ---
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
                # Konversi datetime ke string ISO agar bisa di-JSON
                'datetime': data['datetime'].isoformat() if data['datetime'] else None,
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
        
        # SQL DIPERBAIKI
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
            return redirect(url_for('historical_data_view'))

        pdf = FPDF()
        pdf.add_page()
        base_dir = os.path.dirname(os.path.abspath(__file__))

        font_path = os.path.join(base_dir, 'static', 'Arial.ttf')
        if not os.path.exists(font_path):
            app.logger.error(f"File font tidak ditemukan di: {font_path}")
        try:
            pdf.add_font('Arial', '', font_path, uni=True)
        except Exception as e:
            app.logger.error(f"Gagal memuat font: {e}")
            pdf.set_font("Arial", 'B', 16)
        
        header_path = os.path.join(base_dir, 'static', 'header.png')
        if os.path.exists(header_path):
            pdf.image(header_path, x=0, y=0, w=210)
            pdf.set_y(60) 
        else:
            pdf.set_y(10)

        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "NASKAH WAWANCARA (Historis)", 0, 1, 'C')
        pdf.ln(10)
        
        pdf.set_font("Arial", '', 12)
        label_width = 50 
        
        # Konversi datetime ke string
        schedule_time_str = recording['schedule_time'].isoformat() if recording['schedule_time'] else '-'
        
        pdf.cell(label_width, 8, "Waktu Jadwal :", 0, 0)
        pdf.cell(0, 8, schedule_time_str, 0, 1)
        pdf.cell(label_width, 8, "Saluran Wawancara :", 0, 0) 
        pdf.cell(0, 8, recording['method'] or '-', 0, 1)
        pdf.cell(label_width, 8, "Wartawan :", 0, 0)
        pdf.cell(0, 8, recording['interviewer_name'] or '-', 0, 1)
        pdf.cell(label_width, 8, "Nama Media :", 0, 0)
        pdf.cell(0, 8, recording['media_name'] or '-', 0, 1)
        pdf.cell(label_width, 8, "Narasumber (On Duty) :", 0, 0)
        pdf.cell(0, 8, recording['interviewee'] or '-', 0, 1)
        pdf.cell(label_width, 8, "Topik :", 0, 0)
        pdf.cell(0, 8, recording['topic'] or '-', 0, 1)
        pdf.ln(10)
        
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Isi Naskah :", 0, 1)
        pdf.ln(5)
        
        pdf.set_font("Arial", '', 11)
        transcript = recording['transcript'] or '(Tidak ada transkrip)'
        for line in transcript.split('\n'):
            pdf.multi_cell(0, 7, line)
            pdf.ln(2)

        buffer = io.BytesIO()
        pdf_bytes = pdf.output()
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
        return redirect(url_for('historical_data_view'))

# --- DASBOR NLP (Arsitektur Terpisah) ---

@app.route('/dashboard')
def dashboard():
    """
    Halaman Dasbor VERSI RINGAN.
    Hanya membaca data yang sudah diolah dari tabel 'keyword_results'.
    """
    if 'user' not in session:
        flash('Please login to view this page', 'danger')
        return redirect(url_for('login'))
    
    keywords_json = "[]" # Default
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cursor.execute("SELECT word, (weight * 100) as weight FROM public.keyword_results ORDER BY weight DESC")
        keyword_data = [dict(row) for row in cursor.fetchall()]
        
        if not keyword_data:
            flash('Data analisis belum tersedia. Jalankan skrip NLP lokal.', 'info')
        
        keywords_json = json.dumps(keyword_data)
        return render_template('dashboard.html', keywords_json=keywords_json)
        
    except Exception as e:
        flash(f'Error memuat dashboard: {str(e)}', 'danger')
        app.logger.error(f'Dashboard error: {str(e)}', exc_info=True)
        return render_template('dashboard.html', keywords_json="[]")


@app.route('/search_by_keyword')
def search_by_keyword():
    """
    API untuk fitur klik (Ini tidak perlu NLP, hanya SQL).
    """
    if 'user' not in session:
        return {'results': []}, 403

    keyword = request.args.get('q')
    if not keyword:
        return {'results': []}, 400

    results = []
    try:
        db = get_db()
        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cursor.execute("""
            SELECT ir.token, ar.interviewee, ir.interviewer_name, ar.transcript
            FROM audio_recordings ar
            JOIN interview_requests ir ON ar.request_id = ir.id
            WHERE ar.transcript ILIKE %s
            ORDER BY ar.date DESC
        """, (f'%{keyword}%',))
        
        results = [dict(row) for row in cursor.fetchall()]
        return {'results': results}

    except Exception as e:
        app.logger.error(f'Search by keyword error: {str(e)}', exc_info=True)
        return {'results': [], 'error': str(e)}, 500

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

# Nonaktifkan app.run() untuk Vercel
# if __name__ == "__main__":
#     port = int(os.environ.get("PORT", 5000))
#     app.run(host='0.0.0.0', port=port, debug=True)









