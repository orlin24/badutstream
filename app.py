from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session, make_response, has_request_context
from flask_cors import CORS
from functools import wraps
import os
import time
import subprocess
import uuid
import json
import threading
import shlex
import logging
import platform
from datetime import datetime, timedelta
import requests
import gdown
from bs4 import BeautifulSoup

# Untuk verifikasi tanda tangan digital
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'bangjoss24-!a#b$c%d^e&f*g(h)1234567890')  # Gunakan secret key yang kuat
CORS(app)  # Mengaktifkan CORS

# Konfigurasi logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ Konfigurasi Direktori & FFmpeg ------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')

# Tentukan lokasi FFmpeg berdasarkan sistem operasi
if platform.system() == 'Linux':
    FFMPEG_PATH = '/usr/bin/ffmpeg'
elif platform.system() == 'Darwin':  # macOS
    FFMPEG_PATH = '/opt/homebrew/bin/ffmpeg'
elif platform.system() == 'Windows':
    # Contoh: jika FFmpeg diinstal di C:\ffmpeg\bin\
    FFMPEG_PATH = r'C:\ffmpeg\bin\ffmpeg.exe'
else:
    raise Exception("Unsupported operating system")

# ------------------ Konfigurasi Lisensi ------------------

# URL API validasi lisensi (server panel)
LICENSE_API_URL = "http://152.42.254.194:5000/validate_license"
# File lokal untuk menyimpan data lisensi
license_file_path = os.path.join(BASE_DIR, 'license.json')

# ------------------ Digital Signature Verification ------------------

def verify_license_signature(license_data, signature_b64, public_key_pem):
    """
    Verifikasi tanda tangan digital dari data lisensi.
    
    license_data: Dictionary, misalnya:
        {"license_key": "WSWR-DCEZ-0MAW-D6XC", "expiry_date": "2025-04-25T04:33:00"}
    signature_b64: Signature dalam bentuk string Base64 yang diberikan oleh server.
    public_key_pem: Public key dalam format PEM (tipe bytes) yang digunakan untuk verifikasi.
    
    Mengembalikan True jika verifikasi berhasil, False jika gagal.
    """
    # Serialisasi data lisensi menjadi string JSON dengan pengurutan kunci
    message = json.dumps(license_data, sort_keys=True).encode('utf-8')
    # Decode signature dari Base64
    signature = base64.b64decode(signature_b64)
    # Muat public key dari format PEM
    public_key = serialization.load_pem_public_key(public_key_pem)
    try:
        public_key.verify(
            signature,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        logging.error(f"Digital signature verification failed: {str(e)}")
        return False

# ------------------ Fungsi Manajemen Lisensi ------------------

def load_license_from_cookies():
    """
    Mengambil data lisensi dari cookies jika ada (hanya saat ada request context).
    Jika tidak, ambil data dari file.
    """
    if has_request_context():
        license_key = request.cookies.get('license_key')
        expiry_date = request.cookies.get('expiry_date')
        if license_key and expiry_date:
            return {"license_key": license_key, "expiry_date": expiry_date}
    return load_license()

def save_license_to_cookies(response, license_data):
    """
    Menyimpan data lisensi ke cookies dengan masa berlaku 30 hari,
    dan juga menyimpan data ke file lokal.
    """
    response.set_cookie('license_key', license_data['license_key'], max_age=30*24*60*60)
    response.set_cookie('expiry_date', license_data['expiry_date'], max_age=30*24*60*60)
    save_license(license_data)
    return response

def load_license():
    """Membaca data lisensi dari file license.json jika ada."""
    if os.path.exists(license_file_path):
        with open(license_file_path, 'r') as f:
            return json.load(f)
    return None

def save_license(license_data):
    """Menyimpan data lisensi ke file license.json."""
    with open(license_file_path, 'w') as f:
        json.dump(license_data, f)

def is_license_valid():
    """
    Memeriksa apakah lisensi valid dengan membandingkan tanggal kedaluwarsa
    yang tersimpan dengan waktu saat ini.
    Tidak memanggil API eksternal.
    """
    license_data = load_license_from_cookies()
    if not license_data:
        return False
    try:
        expiry_date = datetime.fromisoformat(license_data["expiry_date"])
        return datetime.now() < expiry_date
    except Exception as e:
        logging.error(f"Error parsing expiry_date: {str(e)}")
        return False

def check_license():
    """Fungsi pembantu yang mengembalikan False jika lisensi tidak valid."""
    if not is_license_valid():
        logging.error("License has expired or is invalid.")
        return False
    return True

def license_required(f):
    """
    Decorator untuk melindungi route agar hanya dapat diakses jika lisensi valid.
    Jika tidak valid, pengguna akan diarahkan ke halaman /license.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not check_license():
            return redirect(url_for('license'))
        return f(*args, **kwargs)
    return decorated_function

# ------------------ Manajemen Video & Live Streaming ------------------

def load_uploaded_videos():
    """Membaca daftar video yang diunggah dari file videos.json."""
    if os.path.exists(videos_json_path):
        with open(videos_json_path, 'r') as f:
            return json.load(f)
    return []

def save_uploaded_videos():
    """Menyimpan daftar video yang diunggah ke file videos.json."""
    with open(videos_json_path, 'w') as f:
        json.dump(uploaded_videos, f)

def load_live_info():
    """Membaca informasi streaming dari file live_info.json."""
    if os.path.exists(live_info_json_path):
        with open(live_info_json_path, 'r') as f:
            return json.load(f)
    return {}

def save_live_info():
    """Menyimpan informasi streaming ke file live_info.json."""
    with open(live_info_json_path, 'w') as f:
        json.dump(live_info, f)

uploaded_videos = load_uploaded_videos()
live_info = load_live_info()
processes = {}

def update_active_streams():
    """Mengubah status semua stream aktif menjadi Stopped."""
    for live_id, info in live_info.items():
        if info['status'] == 'Active':
            info['status'] = 'Stopped'
    save_live_info()

def check_and_update_scheduled_streams():
    """Memeriksa apakah ada stream yang dijadwalkan dan waktu sudah tiba, lalu menjalankannya."""
    current_time = datetime.now()
    for live_id, info in live_info.items():
        if info['status'] == 'Scheduled':
            try:
                schedule_time = datetime.strptime(info['startTime'], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                schedule_time = datetime.strptime(info['startTime'], "%Y-%m-%dT%H:%M")
            if current_time >= schedule_time:
                run_ffmpeg(live_id, info)

def run_ffmpeg(live_id, info):
    """
    Menjalankan FFmpeg untuk streaming video.
    Mengatur status streaming, menjalankan perintah FFmpeg, dan menangani log.
    """
    try:
        logging.debug(f"Starting FFmpeg for live_id: {live_id}")
        live_info[live_id]['status'] = 'Active'
        save_live_info()
        file_path = os.path.abspath(os.path.join(uploads_dir, info['video']))
        stream_key = info['streamKey']
        bitrate = info.get('bitrate', '5000k')
        duration = int(info.get('duration', 0))
        ffmpeg_command = f"{FFMPEG_PATH} -stream_loop -1 -re -i {shlex.quote(file_path)} " \
                         f"-b:v {bitrate} -f flv -c:v copy -c:a copy " \
                         f"rtmp://a.rtmp.youtube.com/live2/{shlex.quote(stream_key)}"
        log_file = open(f'ffmpeg_{live_id}.log', 'w')
        process = subprocess.Popen(ffmpeg_command, stdout=log_file, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, shell=True)
        processes[live_id] = process
        logging.debug(f"Running FFmpeg command: {ffmpeg_command}")
        if duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            threading.Timer((stop_time - datetime.now()).total_seconds(), stop_stream_manually, args=[live_id]).start()
        process.wait()
    except Exception as e:
        logging.error(f"FFmpeg error: {str(e)}")
    finally:
        if live_id in processes:
            del processes[live_id]
        live_info[live_id]['status'] = 'Stopped'
        save_live_info()
        log_file.close()

def stop_stream_manually(live_id):
    """Menghentikan streaming secara manual."""
    logging.debug(f"Stopping stream manually for live_id: {live_id}")
    if live_id in processes:
        process = processes[live_id]
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        del processes[live_id]
    if live_id in live_info:
        live_info[live_id]['status'] = 'Stopped'
    save_live_info()
    time.sleep(10)

def stop_all_active_streams():
    """Menghentikan semua stream aktif jika lisensi tidak valid."""
    if not check_license():
        logging.error("License invalid. Stopping all active streams.")
        for live_id, info in live_info.items():
            if info['status'] == 'Active':
                stop_stream_manually(live_id)

def periodic_check():
    """Memeriksa setiap 60 detik untuk menjalankan stream yang dijadwalkan."""
    if not check_license():
        stop_all_active_streams()
    check_and_update_scheduled_streams()
    threading.Timer(60, periodic_check).start()

# ------------------ Manajemen User (Login Sederhana) ------------------

users = {"gebang": "gebang"}

def login_required(f):
    """Decorator agar route hanya dapat diakses oleh pengguna yang sudah login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def format_size(size):
    """Mengonversi ukuran file ke format yang mudah dibaca."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def get_file_name_from_google_drive_url(url):
    """Mengambil nama file asli dari halaman Google Drive."""
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.title.string.replace(" - Google Drive", "")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Halaman login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username] == password:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Proses logout."""
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
@license_required
def index():
    """Halaman utama yang menampilkan daftar video yang diunggah."""
    return render_template('index.html', title='Home', videos=uploaded_videos)

@app.route('/start_stream', methods=['POST'])
@login_required
@license_required
def start_stream():
    """Memulai streaming video."""
    try:
        if not check_license():
            return jsonify({'message': 'License invalid.'}), 403
        data = request.form
        title = data.get('title')
        video_filename = data.get('video')
        stream_key = data.get('streamKey')
        schedule_date = data.get('scheduleDate')
        bitrate = data.get('bitrate')
        duration = data.get('duration')
        if not all([title, video_filename, stream_key]):
            return jsonify({'message': 'Missing parameters'}), 400
        video = next((v for v in uploaded_videos if v['filename'] == video_filename), None)
        if not video:
            return jsonify({'message': 'Video not found'}), 404
        live_id = str(uuid.uuid4())
        live_info[live_id] = {
            'title': title,
            'video': video_filename,
            'streamKey': stream_key,
            'status': 'Scheduled' if schedule_date else 'Pending',
            'startTime': schedule_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'bitrate': f'{bitrate}k' if bitrate else '5000k',
            'duration': int(duration) if duration else 0
        }
        save_live_info()
        if schedule_date:
            schedule_time = datetime.strptime(schedule_date, "%Y-%m-%dT%H:%M")
            delay = max(0, (schedule_time - datetime.now()).total_seconds())
            threading.Timer(delay, run_ffmpeg, args=[live_id, live_info[live_id]]).start()
        else:
            threading.Thread(target=run_ffmpeg, args=[live_id, live_info[live_id]]).start()
        return jsonify({'message': 'Stream started' if not schedule_date else 'Stream scheduled', 'id': live_id})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/stop_stream/<id>', methods=['POST'])
@login_required
@license_required
def stop_stream(id):
    """Menghentikan streaming video berdasarkan ID."""
    if id not in live_info:
        return jsonify({'message': 'Stream not found'}), 404
    try:
        stop_stream_manually(id)
        return jsonify({'message': 'Stream stopped'})
    except Exception as e:
        logging.error(f"Stop error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/update_bitrate/<id>', methods=['POST'])
@login_required
@license_required
def update_bitrate(id):
    """Mengubah bitrate streaming dan merestart streaming."""
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404
    try:
        bitrate = request.json['bitrate']
        if not bitrate:
            return jsonify({'message': 'Bitrate is required'}), 400
        live_info[id]['bitrate'] = f'{bitrate}k'
        save_live_info()
        if id in processes:
            process = processes[id]
            process.terminate()
            process.wait(timeout=10)
            del processes[id]
            logging.debug(f"Restarting stream {id} with new bitrate {bitrate}k")
            threading.Thread(target=run_ffmpeg, args=[id, live_info[id]]).start()
        return jsonify({'message': 'Bitrate updated and stream restarted'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/stream_logs/<id>')
@login_required
@license_required
def stream_logs(id):
    """Mengambil log streaming dari file log."""
    log_file = f'ffmpeg_{id}.log'
    if not os.path.exists(log_file):
        return jsonify({'message': 'Log file not found'}), 404
    with open(log_file, 'r') as f:
        logs = f.read()
    return jsonify({'logs': logs})

@app.route('/restart_stream/<id>', methods=['POST'])
@login_required
@license_required
def restart_stream(id):
    """Merestart streaming video."""
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404
    try:
        info = live_info[id]
        if id in processes:
            old_process = processes[id]
            if old_process.poll() is None:
                old_process.terminate()
                old_process.wait(timeout=10)
            del processes[id]
        logging.debug(f"Restarting stream {id}")
        threading.Thread(target=run_ffmpeg, args=[id, info]).start()
        live_info[id]['startTime'] = datetime.now().isoformat()
        save_live_info()
        return jsonify({'message': 'Stream restarted'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/delete_stream/<id>', methods=['POST'])
@login_required
@license_required
def delete_stream(id):
    """Menghapus data streaming berdasarkan ID."""
    logging.debug(f"Received request to delete stream {id}")
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404
    try:
        if id in processes:
            process = processes[id]
            process.terminate()
            process.wait()
            del processes[id]
        del live_info[id]
        save_live_info()
        return jsonify({'message': 'Stream deleted'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/live_info/<id>')
@login_required
@license_required
def live_info_page(id):
    """Menampilkan halaman informasi streaming tertentu."""
    if id not in live_info:
        return redirect(url_for('live_list'))
    return render_template('live_info.html', live=live_info[id], lives=live_info)

@app.route('/get_live_info/<id>')
@login_required
@license_required
def get_live_info(id):
    """Mengambil informasi streaming berdasarkan ID."""
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404
    info = live_info[id]
    info['id'] = id
    info['video_name'] = info['video'].split('_', 1)[-1]
    return jsonify(info)

@app.route('/all_live_info')
@login_required
@license_required
def all_live_info():
    """Mengambil semua informasi streaming."""
    return jsonify(list(live_info.values()))

@app.route('/live_list')
@login_required
@license_required
def live_list():
    """Menampilkan halaman daftar streaming."""
    return render_template('live_list.html', title='Live List', lives=live_info)

@app.route('/upload_video', methods=['GET', 'POST'])
@login_required
@license_required
def upload_video():
    """Halaman untuk mengunggah video."""
    if request.method == 'POST':
        try:
            file_url = request.json['file_url']
            logging.debug(f"Received file_url: {file_url}")
            original_name = get_file_name_from_google_drive_url(file_url)
            unique_filename = f"{uuid.uuid4()}_{original_name}"
            file_path = os.path.join(uploads_dir, unique_filename)
            gdown.download(url=file_url, output=file_path, quiet=False, fuzzy=True)
            logging.debug(f"Saving file to: {file_path}")
            file_size = os.path.getsize(file_path)
            uploaded_videos.append({
                'filename': unique_filename,
                'original_name': original_name,
                'size': format_size(file_size),
                'upload_date': datetime.now().strftime("%Y-%m-%d")
            })
            save_uploaded_videos()
            return jsonify({'success': True, 'message': 'Video uploaded successfully!', 'filename': unique_filename})
        except Exception as e:
            logging.error(f"Error: {str(e)}")
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    return render_template('upload_video.html', title='Upload Video', videos=uploaded_videos)

@app.route('/uploads/<filename>')
@login_required
@license_required
def uploaded_file(filename):
    """Mengambil file video yang sudah diunggah."""
    return send_from_directory(uploads_dir, filename)

@app.route('/get_uploaded_videos', methods=['GET'])
@login_required
@license_required
def get_uploaded_videos():
    """Mengambil daftar video yang diunggah."""
    return jsonify(uploaded_videos)

@app.route('/rename_video', methods=['POST'])
@login_required
@license_required
def rename_video():
    """Mengganti nama file video."""
    try:
        old_filename = request.json['old_filename']
        new_filename = request.json['new_filename']
        if not new_filename.lower().endswith(".mp4"):
            new_filename += ".mp4"
        old_file_path = os.path.join(uploads_dir, old_filename)
        new_file_path = os.path.join(uploads_dir, new_filename)
        os.rename(old_file_path, new_file_path)
        for video in uploaded_videos:
            if video['filename'] == old_filename:
                video['filename'] = new_filename
                video['original_name'] = new_filename
                break
        save_uploaded_videos()
        return jsonify({'success': True, 'message': 'Video renamed successfully!', 'videos': uploaded_videos})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/delete_video', methods=['POST'])
@login_required
@license_required
def delete_video():
    """Menghapus file video."""
    try:
        filename = request.json['filename']
        file_path = os.path.join(uploads_dir, filename)
        os.remove(file_path)
        global uploaded_videos
        uploaded_videos = [video for video in uploaded_videos if video['filename'] != filename]
        save_uploaded_videos()
        return jsonify({'success': True, 'message': 'Video deleted successfully!', 'videos': uploaded_videos})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

# Pastikan direktori uploads ada
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

# Saat startup, hentikan semua streaming aktif (dari sesi sebelumnya)
stop_all_active_streams()

# Mulai pemeriksaan periodik untuk streaming terjadwal
periodic_check()

# ------------------ Route Input Lisensi dengan Verifikasi Tanda Tangan Digital ------------------

@app.route('/license', methods=['GET', 'POST'])
def license():
    """
    Halaman input lisensi.
    Saat POST, aplikasi mengirim kunci lisensi ke server panel untuk validasi.
    Server panel mengembalikan data lisensi beserta signature.
    Aplikasi kemudian memverifikasi signature menggunakan public key.
    Jika valid, data lisensi disimpan ke cookies dan file.
    """
    if request.method == 'POST':
        license_key = request.json['license_key']
        try:
            response = requests.post(LICENSE_API_URL, json={'license_key': license_key})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

        if response.status_code == 200:
            result = response.json()
            if result.get('valid'):
                signature = result.get('signature')
                if not signature:
                    return jsonify({'success': False, 'message': 'Signature not found in license data.'}), 400

                license_data = {"license_key": license_key, "expiry_date": result['expiry_date']}
                # Gunakan public key yang Anda miliki
                PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuLFrxdhPGINosqGH150t
jxim//cVyGAs8IghTC4JgTheavHggSNlkXTP7iWIj0TuYmFJ66YxmmfcraQUtAa7
+R8DjLXb0vKIwEA6Kjgmo9eMpkcGjFGWUu+DpnFTTxpqODOSi/eXBUJlsXbAlP0Q
wcPf+HalK+S239V2wLlFw/owXCPFRPoHSVoeiuS4V4X7aV0tdLySkT2MVO4jK3Gd
YcDDG1HMsZhOFWQKqpg2/mh71yuGy8maJMdkfH8H/id92OZ985quHMmrGlQOEGXd
HAf4+1seDYI/1WODEbnsCINeeJznmYD2e8EIb7r0z9nCsYm6/+97DVdyIk47hn1d
7wIDAQAB
-----END PUBLIC KEY-----"""
                if not verify_license_signature(license_data, signature, PUBLIC_KEY_PEM):
                    return jsonify({'success': False, 'message': 'License data has been tampered with!'}), 400

                license_data_full = {"license_key": license_key, "expiry_date": result['expiry_date']}
                resp = make_response(jsonify({'success': True, 'message': 'License updated successfully!'}))
                save_license_to_cookies(resp, license_data_full)
                return resp
            else:
                return jsonify({'success': False, 'message': 'Invalid license key!'}), 400
        else:
            return jsonify({'success': False, 'message': 'Error validating license key!'}), 500
    return render_template('license.html')

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
