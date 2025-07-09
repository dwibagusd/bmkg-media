from flask import Flask, render_template, request, send_from_directory, session, redirect, url_for, flash
from fpdf import FPDF
from datetime import datetime
from docx import Document
import os
import csv

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Ganti dengan kunci acak yang lebih aman untuk produksi

# Folder Konfigurasi
AUDIO_FOLDER = 'static/audio'
PDF_FOLDER = 'static/pdf'

# ==========================
#        BERANDA
# ==========================
@app.route("/")
def home():
    # # Baca teks dari file beranda
    # teks_beranda = ""
    # beranda_path = os.path.join(app.static_folder, "beranda.txt")
    # if os.path.exists(beranda_path):
    #     with open(beranda_path, "r", encoding="utf-8") as f:
    #         teks_beranda = f.read()

    # # Ambil gambar (kecuali header) untuk slideshow
    # image_dir = os.path.join(app.static_folder, "images")
    # image_files = []
    # if os.path.exists(image_dir):
    #     all_images = os.listdir(image_dir)
    #     image_files = [
    #         img for img in all_images
    #         if img.lower().endswith((".jpg", ".jpeg", ".png", ".gif")) and img != "headerbmkg.jpg"
    #     ]
    #     image_files.sort(
    #         key=lambda x: os.path.getmtime(os.path.join(image_dir, x)),
    #         reverse=True
    #     )

    # # Ambil file press release
    # press_folder = os.path.join(app.static_folder, "press_release")
    # os.makedirs(press_folder, exist_ok=True)
    # press_files = [f for f in os.listdir(press_folder) if f.endswith('.pdf')]
    # press_file = press_files[0] if press_files else None

    # return render_template('index.html', teks=teks_beranda, press_file=press_file, image_files=image_files)
# Ambil gambar untuk slideshow seperti biasa
    image_dir = os.path.join(app.static_folder, "images")
    image_files = []
    if os.path.exists(image_dir):
        all_images = os.listdir(image_dir)
        image_files = [
            img for img in all_images
            if img.lower().endswith((".jpg", ".jpeg", ".png", ".gif")) and img != "headerbmkg.jpg"
        ]
        image_files.sort(
            key=lambda x: os.path.getmtime(os.path.join(image_dir, x)),
            reverse=True
        )

    # Ambil file press release
    press_folder = os.path.join(app.static_folder, "press_release")
    os.makedirs(press_folder, exist_ok=True)
    press_files = [f for f in os.listdir(press_folder) if f.endswith('.pdf')]
    press_file = press_files[0] if press_files else None

    return render_template('index.html', press_file=press_file, image_files=image_files)
# ==========================
#        AUTENTIKASI
# ==========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'hiluxobs':
            session['logged_in'] = True
            session['user'] = 'admin'
            flash('Berhasil login sebagai admin.', 'success')
            return redirect(url_for('home'))
        else:
            flash('Username atau password salah.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah logout.', 'info')
    return redirect(url_for('home'))

# ==========================
#      HALAMAN UMUM
# ==========================
@app.route('/permohonan')
def permohonan():
    return render_template('permohonan.html')


@app.route('/recorder')
def recorder():
    if not session.get('logged_in'):
        flash('Anda harus login sebagai admin untuk mengakses halaman ini.', 'warning')
        return redirect(url_for('login'))

    log_file = 'log_permohonan.csv'
    logs = []
    tokens = []

    if os.path.exists(log_file):
        with open(log_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            logs = list(reader)
            tokens = [{'token': row['token']} for row in logs if row.get('token')]

    return render_template('recorder.html', logs=logs, tokens=tokens)


@app.route('/historis')
def historis():
    log_file = 'log_permohonan.csv'
    logs = []

    if os.path.exists(log_file):
        with open(log_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'narasumber' not in row:
                    row['narasumber'] = '-'
                logs.append(row)

    pdf_folder = PDF_FOLDER
    pdf_files = [
        f for f in os.listdir(pdf_folder)
        if f.startswith('wawancara_') and f.endswith('.pdf')
    ]

    token_to_pdf = {}
    for pdf in pdf_files:
        filename = pdf.replace('.pdf', '')
        parts = filename.split('_')
        if len(parts) >= 4:
            token = f"{parts[1]}_{parts[2]}"
            token_to_pdf[token] = f"pdf/{pdf}"

    for log in logs:
        token = log.get('token', '')
        log['pdf_file'] = token_to_pdf.get(token, '')

    return render_template('historis.html', logs=logs)

# ==========================
#         DOWNLOAD
# ==========================
@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(os.path.join('static', 'press_release'), filename)

# ==========================
#        AUDIO RECORD
# ==========================
@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    audio = request.files['audio']
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"rekaman_{timestamp}.webm"
    path = os.path.join(AUDIO_FOLDER, filename)
    audio.save(path)
    return {'status': 'ok', 'filename': filename}

@app.route('/get_topik/<token>')
def get_topik(token):
    log_file = 'log_permohonan.csv'
    if os.path.exists(log_file):
        with open(log_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('token') == token:
                    return {'topik': row.get('topik', '')}
    return {'topik': ''}

# ==========================
#         SAVE PDF
# ==========================
@app.route('/save_pdf', methods=['POST'])
def save_pdf():
    data = request.form
    token = data['token']
    narasumber = data['narasumber']
    teks = data['transkripsi']

    log_file = 'log_permohonan.csv'
    pewawancara = waktu = instansi = jenis = topik = '-'
    logs = []

    if os.path.exists(log_file):
        with open(log_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            logs = list(reader)

        for row in logs:
            if row.get('token') == token:
                pewawancara = row.get('nama', '-')
                waktu = row.get('waktu', '-')
                instansi = row.get('perusahaan', '-')
                jenis = row.get('jenis', '-')
                topik = row.get('topik', '-')
                row['narasumber'] = narasumber
                break

        fieldnames = list(logs[0].keys())
        if 'narasumber' not in fieldnames:
            fieldnames.append('narasumber')
        if 'topik' not in fieldnames:
            fieldnames.append('topik')

        with open(log_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(logs)

    # Generate DOCX
    template_path = os.path.join('static', 'template.docx')
    doc = Document(template_path)

    replacements = {
        '{{waktu}}': waktu,
        '{{jenis}}': jenis,
        '{{pewawancara}}': pewawancara,
        '{{instansi}}': instansi,
        '{{narasumber}}': narasumber,
        '{{transkripsi}}': teks,
        '{{topik}}': topik
    }

    for p in doc.paragraphs:
        for key, val in replacements.items():
            if key in p.text:
                for run in p.runs:
                    run.text = run.text.replace(key, val)

    os.makedirs(PDF_FOLDER, exist_ok=True)
    safe_narasumber = narasumber.replace(" ", "_")
    docx_filename = f"wawancara_{token}_{safe_narasumber}.docx"
    docx_path = os.path.join(PDF_FOLDER, docx_filename)
    doc.save(docx_path)

    # Konversi ke PDF menggunakan LibreOffice
    subprocess.run([
        'libreoffice',
        '--headless',
        '--convert-to', 'pdf',
        '--outdir', PDF_FOLDER,
        docx_path
    ])

    pdf_filename = docx_filename.replace('.docx', '.pdf')
    pdf_path = os.path.join(PDF_FOLDER, pdf_filename)

    return {'pdf_path': pdf_path}


# ==========================
#       LOG PERMOHONAN
# ==========================
@app.route('/log_permohonan', methods=['POST'])
def log_permohonan():
    data = request.form
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    perusahaan = data.get('perusahaan', '').strip().replace(' ', '').lower()
    waktu = data.get('waktu', '').replace(':', '').replace('-', '').replace(' ', '')
    token = f"{perusahaan}_{waktu}"

    log_file = 'log_permohonan.csv'
    fieldnames = ['timestamp', 'nama', 'perusahaan', 'waktu', 'jenis', 'link', 'token', 'topik']

    new_entry = {
        'timestamp': now,
        'nama': data.get('nama'),
        'perusahaan': data.get('perusahaan'),
        'waktu': data.get('waktu'),
        'jenis': data.get('jenis'),
        'link': data.get('link') or '',
        'token': token,
        'topik': data.get('topik', '')
    }

    file_exists = os.path.isfile(log_file)
    with open(log_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(new_entry)

    return {'status': 'ok', 'token': token}

# ==========================
#        RUN APP
# ==========================
if __name__ == '__main__':
    app.run(debug=True)
