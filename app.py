from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session
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
import signal
import locale
import shlex
import requests
from bs4 import BeautifulSoup

# Matikan log HTTP bawaan Flask
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # Hanya tampilkan error, tidak ada INFO atau DEBUG

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'e3f1a2b4c6d8e0f9a7b5c3d1e9f2a4c6d8b0e1f3a5c7d9e2b4c6d8a0f1e3b5')  # Load from env
CORS(app)  # Enable CORS for all routes

# Konfigurasi logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Gunakan lock untuk menghindari race condition saat menghapus proses dari dictionary
process_lock = threading.Lock()

# Konfigurasi path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')
apibot_json_path = os.path.join(uploads_dir, 'apibot.json')

# Definisikan fungsi load_apibot_settings dan save_apibot_settings di sini
def load_apibot_settings():
    """Memuat pengaturan bot dari file apibot.json."""
    if os.path.exists(apibot_json_path):
        with open(apibot_json_path, 'r') as file:
            return json.load(file)
    return {}

def save_apibot_settings(bot_token, chat_id):
    """Menyimpan pengaturan bot ke file apibot.json."""
    settings = {
        'botToken': bot_token,
        'chatId': chat_id
    }
    with open(apibot_json_path, 'w') as file:
        json.dump(settings, file)

# Baru setelah ini panggil load_apibot_settings()
telegram_bot_settings = load_apibot_settings()
telegram_bot_token = telegram_bot_settings.get('botToken')
telegram_chat_id = telegram_bot_settings.get('chatId')

# Tentukan path FFmpeg berdasarkan sistem operasi
if platform.system() == 'Linux':
    FFMPEG_PATH = '/usr/bin/ffmpeg'
elif platform.system() == 'Darwin':  # Darwin adalah nama lain untuk macOS
    FFMPEG_PATH = '/opt/homebrew/bin/ffmpeg'
else:
    raise Exception("Unsupported operating system")

# ==============================
# 🔹 AUTHENTIKASI & LOGIN
# ==============================

users = {
    "admin": "admin",
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
        
# Load data saat startup
def load_data():
    global uploaded_videos, live_info
    if os.path.exists(videos_json_path):
        with open(videos_json_path, 'r') as file:
            uploaded_videos = json.load(file)
    if os.path.exists(live_info_json_path):
        with open(live_info_json_path, 'r') as file:
            live_info = json.load(file)

def restart_if_needed():
    restart_counts = {}  # Dictionary untuk melacak jumlah restart
    while True:
        with process_lock:
            for live_id in list(processes.keys()):
                process = processes.get(live_id)
                if process and process.poll() is not None:  # Proses sudah mati
                    if live_id in live_info and live_info[live_id]['status'] == 'Active':
                        restart_counts[live_id] = restart_counts.get(live_id, 0) + 1
                        if restart_counts[live_id] <= 5:  # Batas 5 restart
                            logging.debug(f"Restarting live_id: {live_id} (attempt {restart_counts[live_id]})")
                            del processes[live_id]
                            threading.Thread(target=run_ffmpeg, args=[live_id, live_info[live_id]]).start()
                            send_telegram_notification(f"⚠️ Live '{live_info[live_id]['title']}' mati dan direstart (percobaan {restart_counts[live_id]})")
                        else:
                            live_info[live_id]['status'] = 'Stopped'
                            save_live_info()
                            send_telegram_notification(f"❌ Live '{live_info[live_id]['title']}' gagal setelah 5 restart.")
                elif live_id in live_info and live_info[live_id]['status'] == 'Active' and live_id not in processes:
                    restart_counts[live_id] = restart_counts.get(live_id, 0) + 1
                    if restart_counts[live_id] <= 999999999999999999999999999:
                        logging.debug(f"Tidak ada proses untuk live_id: {live_id}, restart otomatis.")
                        threading.Thread(target=run_ffmpeg, args=[live_id, live_info[live_id]]).start()
                        send_telegram_notification(f"⚠️ Live '{live_info[live_id]['title']}' hilang dan direstart (percobaan {restart_counts[live_id]})")
        time.sleep(10)  # Cek setiap 10 detik

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

@app.template_filter('datetime')
def format_datetime(value):
    try:
        locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
        if 'T' in value:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        else:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d-%b-%Y %H:%M")
    except Exception as e:
        logging.error(f"Error formatting date: {str(e)}")
        return value

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
        if live_info[live_id]['status'] == 'Scheduled':
            send_telegram_notification(f"🎥 Live terjadwal '{info['title']}' TELAH DIMULAI!")
        else:
            send_telegram_notification(f"🎥 Live '{info['title']}' TELAH AKTIF!")

        if live_id in live_info:
            live_info[live_id]['status'] = 'Active'
            save_live_info()

        file_path = os.path.abspath(os.path.join(uploads_dir, info['video']))
        stream_key = info['streamKey']
        bitrate = info.get('bitrate', '5000k')
        duration = int(info.get('duration', 0))

        # Tambahkan parameter untuk memastikan koneksi RTMP ditutup dengan benar
        ffmpeg_command = f"{FFMPEG_PATH} -loglevel quiet -stream_loop -1 -re -i {shlex.quote(file_path)} " \
                         f"-b:v {bitrate} -f flv -c:v copy -c:a copy " \
                         f"-flvflags no_duration_filesize " \
                         f"rtmp://a.rtmp.youtube.com/live2/{shlex.quote(stream_key)}"

        # Arahkan output ke /dev/null (Linux/macOS) atau NUL (Windows) untuk menghindari file log
        if platform.system() == 'Windows':
            DEV_NULL = 'NUL'
        else:
            DEV_NULL = '/dev/null'

        process = subprocess.Popen(
            ffmpeg_command,
            stdout=subprocess.DEVNULL,  # Arahkan stdout ke /dev/null atau NUL
            stderr=subprocess.DEVNULL,  # Arahkan stderr ke /dev/null atau NUL
            text=True,
            bufsize=1,
            shell=True,
            preexec_fn=os.setsid if platform.system() != 'Windows' else None
        )
        processes[live_id] = process

        if duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            delay = (stop_time - datetime.now()).total_seconds()
            if delay > 5:
                threading.Timer(delay, stop_stream_manually, args=[live_id, True, True]).start()
                send_telegram_notification(f"⏳ Live '{info['title']}' akan berhenti otomatis dalam {duration} menit.")

        process.wait()

    except Exception as e:
        logging.error(f"FFmpeg error: {str(e)}")

def stop_stream_manually(live_id, is_scheduled=False, force=False):
    logging.debug(f"Attempting to stop stream manually for live_id: {live_id}, force={force}")
    with process_lock:
        process = processes.pop(live_id, None)

    if process and process.poll() is None:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=5)

        if force and process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)

    if live_id in live_info:
        live_info[live_id]['status'] = 'Stopped'
        save_live_info()

    title = live_info[live_id]['title']
    message = f"⏰ Live terjadwal '{title}' BERHENTI sesuai jadwal" if is_scheduled else f"⛔ Live '{title}' DIHENTIKAN manual"
    send_telegram_notification(message)

@app.route('/update_stop_schedule/<id>', methods=['POST'])
@login_required
def update_stop_schedule(id):
    if id not in live_info:
        return jsonify({'message': 'Stream tidak ditemukan!'}), 404

    try:
        data = request.json
        duration = int(data.get('duration', 0))
        live_info[id]['duration'] = duration
        save_live_info()

        if id in processes and duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            delay = (stop_time - datetime.now()).total_seconds()
            if delay > 5:
                threading.Timer(delay, stop_stream_manually, args=[id, True, True]).start()
                send_telegram_notification(f"⏳ Live '{live_info[id]['title']}' diperbarui, akan berhenti dalam {duration} menit.")

        return jsonify({'message': 'Jadwal stop otomatis diperbarui!'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

def stop_all_active_streams():
    for live_id, info in live_info.items():
        if info['status'] == 'Active':
            stop_stream_manually(live_id, force=True)

def periodic_check():
    check_and_update_scheduled_streams()
    threading.Timer(60, periodic_check).start()

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

@app.route('/')
@login_required
def index():
    return render_template('index.html', title='Home', videos=uploaded_videos)

@app.route('/start_stream', methods=['POST'])
@login_required
def start_stream():
    try:
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
            send_telegram_notification(f"✅ Live terjadwal '{title}' akan dimulai pada {schedule_date}.")
        else:
            threading.Thread(target=run_ffmpeg, args=[live_id, live_info[live_id]]).start()
            send_telegram_notification(f"✅ Live '{title}' telah dimulai.")

        return jsonify({
            'message': 'Stream scheduled' if schedule_date else 'Stream started',
            'id': live_id
        })

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/stop_stream/<id>', methods=['POST'])
@login_required
def stop_stream(id):
    if id not in live_info:
        return jsonify({'message': 'Stream not found'}), 404

    try:
        stop_stream_manually(id, force=True)
        return jsonify({'message': 'Streaming berhasil dihentikan'})
    except Exception as e:
        logging.error(f"Stop error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/update_bitrate/<id>', methods=['POST'])
@login_required
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
            threading.Thread(target=run_ffmpeg, args=[id, live_info[id]]).start()

        return jsonify({'message': 'Bitrate updated successfully! Stream restarted.'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/stream_logs/<id>')
@login_required
def stream_logs(id):
    log_file = f'ffmpeg_{id}.log'
    if not os.path.exists(log_file):
        return jsonify({'message': 'Log file not found'}), 404
        
    with open(log_file, 'r') as f:
        logs = f.read()
    
    return jsonify({'logs': logs})

@app.route('/restart_stream/<id>', methods=['POST'])
@login_required
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

        threading.Thread(target=run_ffmpeg, args=[id, info]).start()
        return jsonify({'message': 'Stream berhasil di-restart!'})

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Gagal restart: {str(e)}'}), 500

@app.route('/delete_stream/<id>', methods=['POST'])
@login_required
def delete_stream(id):
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
def live_info_page(id):
    if id not in live_info:
        return redirect(url_for('live_list'))
    return render_template('live_info.html', live=live_info[id], lives=live_info)

@app.route('/get_live_info/<id>')
@login_required
def get_live_info(id):
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404
    
    info = live_info[id]
    info['id'] = id
    info['video_name'] = info['video'].split('_', 1)[-1]
    try:
        locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
        if 'T' in info['startTime']:
            dt = datetime.strptime(info['startTime'], "%Y-%m-%dT%H:%M")
        else:
            dt = datetime.strptime(info['startTime'], "%Y-%m-%d %H:%M:%S")
        info['formatted_start'] = dt.strftime("%d-%b-%Y %H:%M")
    except Exception as e:
        logging.error(f"Error formatting date: {str(e)}")
        info['formatted_start'] = info['startTime']
    return jsonify(info)

@app.route('/all_live_info')
@login_required
def all_live_info():
    return jsonify(list(live_info.values()))

@app.route('/live_list')
@login_required
def live_list():
    return render_template('live_list.html', title='Live List', lives=live_info)

@app.route('/upload_video', methods=['GET', 'POST'])
@login_required
def upload_video():
    if request.method == 'POST':
        try:
            file_url = request.json['file_url']
            original_name = get_file_name_from_google_drive_url(file_url)
            unique_filename = f"{uuid.uuid4()}_{original_name}"
            file_path = os.path.join(uploads_dir, unique_filename)
            gdown.download(url=file_url, output=file_path, quiet=False, fuzzy=True)

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
def uploaded_file(filename):
    return send_from_directory(uploads_dir, filename)

@app.route('/get_uploaded_videos', methods=['GET'])
@login_required
def get_uploaded_videos():
    return jsonify(uploaded_videos)

@app.route('/rename_video', methods=['POST'])
@login_required
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

def send_telegram_notification(message):
    settings = load_apibot_settings()
    bot_token = settings.get('botToken')
    chat_id = settings.get('chatId')
    if bot_token and chat_id:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Telegram notification: {e}")

@app.route('/set_telegram_bot', methods=['POST'])
@login_required
def set_telegram_bot():
    data = request.json
    bot_token = data.get('botToken')
    chat_id = data.get('chatId')
    if not bot_token or not chat_id:
        return jsonify({'message': 'Bot token and chat ID are required!'}), 400

    save_apibot_settings(bot_token, chat_id)
    return jsonify({'message': 'Telegram bot settings saved successfully!'})

@app.route('/telegram_bot')
@login_required
def telegram_bot():
    settings = load_apibot_settings()
    return render_template('telegrambot.html', botToken=settings.get('botToken', ''), chatId=settings.get('chatId', ''))

# Pastikan semua streaming aktif ditandai sebagai stopped saat startup
stop_all_active_streams()

# Mulai pengecekan berkala untuk streaming terjadwal
periodic_check()

# Jalankan monitoring untuk restart otomatis
threading.Thread(target=restart_if_needed, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
