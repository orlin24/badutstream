from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session, make_response, has_request_context
from flask_cors import CORS
from functools import wraps
import time
import os
import subprocess
import uuid
import json
from datetime import datetime, timedelta
import threading
import gdown
import platform
import logging
import shlex
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default_secret_key')  # Ambil dari environment variable
CORS(app)  # Aktifkan CORS untuk semua route

# Konfigurasi logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Konfigurasi direktori
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')

# Tentukan path FFmpeg sesuai sistem operasi
if platform.system() == 'Linux':
    FFMPEG_PATH = '/usr/bin/ffmpeg'
elif platform.system() == 'Darwin':  # macOS
    FFMPEG_PATH = '/opt/homebrew/bin/ffmpeg'
else:
    raise Exception("Unsupported operating system")

# URL API validasi lisensi (hanya digunakan saat input lisensi)
LICENSE_API_URL = "http://152.42.254.194:5000/validate_license"
license_file_path = os.path.join(BASE_DIR, 'license.json')

# -------------------- Manajemen Lisensi --------------------

def load_license_from_cookies():
    # Jika ada request context, ambil dari cookies.
    if has_request_context():
        license_key = request.cookies.get('license_key')
        expiry_date = request.cookies.get('expiry_date')
        if license_key and expiry_date:
            return {"license_key": license_key, "expiry_date": expiry_date}
    # Jika tidak ada context, atau data tidak tersedia di cookies, ambil dari file.
    return load_license()

def save_license_to_cookies(response, license_data):
    response.set_cookie('license_key', license_data['license_key'], max_age=30*24*60*60)  # 30 hari
    response.set_cookie('expiry_date', license_data['expiry_date'], max_age=30*24*60*60)  # 30 hari
    save_license(license_data)
    return response

def load_license():
    if os.path.exists(license_file_path):
        with open(license_file_path, 'r') as file:
            return json.load(file)
    return None

def save_license(license_data):
    with open(license_file_path, 'w') as file:
        json.dump(license_data, file)

# Fungsi validasi lisensi tidak memanggil API eksternal lagi.
# Cukup membandingkan tanggal kedaluwarsa yang tersimpan dengan waktu saat ini.
def is_license_valid():
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
    if not is_license_valid():
        logging.error("License has expired or is invalid.")
        return False
    return True

def license_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not check_license():
            return redirect(url_for('license'))
        return f(*args, **kwargs)
    return decorated_function

# -------------------- Manajemen Video & Live Streaming --------------------

def load_uploaded_videos():
    if os.path.exists(videos_json_path):
        with open(videos_json_path, 'r') as file:
            return json.load(file)
    return []

def save_uploaded_videos():
    with open(videos_json_path, 'w') as file:
        json.dump(uploaded_videos, file)

def load_live_info():
    if os.path.exists(live_info_json_path):
        with open(live_info_json_path, 'r') as file:
            return json.load(file)
    return {}

def save_live_info():
    with open(live_info_json_path, 'w') as file:
        json.dump(live_info, file)

uploaded_videos = load_uploaded_videos()
live_info = load_live_info()
processes = {}

def update_active_streams():
    for live_id, info in live_info.items():
        if info['status'] == 'Active':
            info['status'] = 'Stopped'
    save_live_info()

def check_and_update_scheduled_streams():
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
    try:
        logging.debug(f"Starting FFmpeg for live_id: {live_id} with info: {info}")
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
        
        process = subprocess.Popen(
            ffmpeg_command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=True
        )
        
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
    logging.debug(f"Attempting to stop stream manually for live_id: {live_id}")
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
    # Jangan mengakses request cookies di sini, load_license_from_cookies() sudah aman karena cek has_request_context()
    if not check_license():
        logging.error("License has expired or is invalid. Stopping all active streams.")
        for live_id, info in live_info.items():
            if info['status'] == 'Active':
                stop_stream_manually(live_id)

def periodic_check():
    if not check_license():
        stop_all_active_streams()
    check_and_update_scheduled_streams()
    threading.Timer(60, periodic_check).start()

# -------------------- Manajemen User (Login Sederhana) --------------------

users = {
    "gebang": "gebang",
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def get_file_name_from_google_drive_url(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.title.string.replace(" - Google Drive", "")

@app.route('/login', methods=['GET', 'POST'])
def login():
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
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
@license_required
def index():
    return render_template('index.html', title='Home', videos=uploaded_videos)

@app.route('/start_stream', methods=['POST'])
@login_required
@license_required
def start_stream():
    try:
        if not check_license():
            return jsonify({'message': 'License has expired or is invalid.'}), 403

        data = request.form
        title = data.get('title')
        video_filename = data.get('video')
        stream_key = data.get('streamKey')
        schedule_date = data.get('scheduleDate')
        bitrate = data.get('bitrate')
        duration = data.get('duration')  # Durasi dalam menit

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

        return jsonify({
            'message': 'Stream scheduled' if schedule_date else 'Stream started',
            'id': live_id
        })

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/stop_stream/<id>', methods=['POST'])
@login_required
@license_required
def stop_stream(id):
    if id not in live_info:
        return jsonify({'message': 'Stream not found'}), 404

    try:
        stop_stream_manually(id)
        return jsonify({'message': 'Streaming berhasil dihentikan'})
    except Exception as e:
        logging.error(f"Stop error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/update_bitrate/<id>', methods=['POST'])
@login_required
@license_required
def update_bitrate(id):
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

        return jsonify({'message': 'Bitrate updated successfully! Stream restarted to apply new bitrate.'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/stream_logs/<id>')
@login_required
@license_required
def stream_logs(id):
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

        return jsonify({'message': 'Stream berhasil di-restart!'})

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Gagal restart: {str(e)}'}), 500

@app.route('/delete_stream/<id>', methods=['POST'])
@login_required
@license_required
def delete_stream(id):
    logging.debug(f"Received request to delete stream with ID: {id}")
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

        return jsonify({'message': 'Streaming deleted successfully!'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/live_info/<id>')
@login_required
@license_required
def live_info_page(id):
    if id not in live_info:
        return redirect(url_for('live_list'))
    return render_template('live_info.html', live=live_info[id], lives=live_info)

@app.route('/get_live_info/<id>')
@login_required
@license_required
def get_live_info(id):
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
    return jsonify(list(live_info.values()))

@app.route('/live_list')
@login_required
@license_required
def live_list():
    return render_template('live_list.html', title='Live List', lives=live_info)

@app.route('/upload_video', methods=['GET', 'POST'])
@login_required
@license_required
def upload_video():
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

            return jsonify({
                'success': True,
                'message': 'Video uploaded successfully!',
                'filename': unique_filename
            })
        except Exception as e:
            logging.error(f"Error: {str(e)}")
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    return render_template('upload_video.html', title='Upload Video', videos=uploaded_videos)

@app.route('/uploads/<filename>')
@login_required
@license_required
def uploaded_file(filename):
    return send_from_directory(uploads_dir, filename)

@app.route('/get_uploaded_videos', methods=['GET'])
@login_required
@license_required
def get_uploaded_videos():
    return jsonify(uploaded_videos)

@app.route('/rename_video', methods=['POST'])
@login_required
@license_required
def rename_video():
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

        return jsonify({
            'success': True,
            'message': 'Video renamed successfully!',
            'videos': uploaded_videos
        })
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/delete_video', methods=['POST'])
@login_required
@license_required
def delete_video():
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

if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

# Pastikan semua stream aktif dihentikan saat startup
stop_all_active_streams()

# Mulai pemeriksaan periodik untuk streaming terjadwal
periodic_check()

# -------------------- Route untuk Input Lisensi --------------------
@app.route('/license', methods=['GET', 'POST'])
def license():
    if request.method == 'POST':
        license_key = request.json['license_key']
        try:
            response = requests.post(LICENSE_API_URL, json={'license_key': license_key})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

        if response.status_code == 200:
            result = response.json()
            if result.get('valid'):
                license_data = {
                    "license_key": license_key,
                    "expiry_date": result['expiry_date']
                }
                resp = make_response(jsonify({'success': True, 'message': 'License updated successfully!'}))
                save_license_to_cookies(resp, license_data)
                return resp
            else:
                return jsonify({'success': False, 'message': 'Invalid license key!'}), 400
        else:
            return jsonify({'success': False, 'message': 'Error validating license key!'}), 500
    return render_template('license.html')

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
