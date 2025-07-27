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
import psutil
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

# Cek ketersediaan cpulimit
cpulimit_available = False
try:
    cpulimit_check = subprocess.run(["which", "cpulimit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cpulimit_available = cpulimit_check.returncode == 0
except:
    cpulimit_available = False

# Cek ketersediaan GPU NVIDIA
has_nvidia_gpu = False
try:
    nvidia_check = subprocess.run(["nvidia-smi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    has_nvidia_gpu = nvidia_check.returncode == 0
except:
    has_nvidia_gpu = False

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

# Panggil load_data saat startup
load_data()

def restart_if_needed():
    while True:
        with process_lock:
            live_ids = list(processes.keys())  # Create a copy to avoid modification during iteration
            for live_id in live_ids:
                process = processes.get(live_id)
                if process and process.poll() is not None:  # Proses sudah mati
                    if live_id in live_info and live_info[live_id]['status'] == 'Active':
                        # Tambahkan logging untuk diagnostik
                        logging.debug(f"Stream {live_id} mati, melakukan restart...")
                        
                        # Hapus proses lama
                        del processes[live_id]
                        
                        # Jalankan dengan prioritas yang lebih rendah
                        modified_info = live_info[live_id].copy()
                        
                        # Jalankan dengan nice untuk mengurangi beban CPU
                        threading.Thread(target=run_ffmpeg_with_nice, args=[live_id, modified_info]).start()
                
                elif live_id in live_info and live_info[live_id]['status'] == 'Active' and live_id not in processes:
                    # Kasus di mana proses hilang dari dictionary
                    logging.debug(f"Tidak ada proses untuk live_id: {live_id}, restart otomatis.")
                    
                    threading.Thread(target=run_ffmpeg_with_nice, args=[live_id, live_info[live_id]]).start()
        
        # Cek setiap 10 detik untuk memastikan restart cepat
        time.sleep(10)

def save_live_info():
    with open(live_info_json_path, 'w') as file:
        json.dump(live_info, file)

# Inisialisasi variabel setelah load_data()
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

def run_ffmpeg_with_nice(live_id, info):
    try:
        file_path = os.path.abspath(os.path.join(uploads_dir, info['video']))
        stream_key = info['streamKey']
        bitrate = info.get('bitrate', '2500k')  # Default bitrate lebih rendah
        duration = int(info.get('duration', 0))
        
        # Hitung buffer size berdasarkan bitrate
        bitrate_value = int(bitrate.replace('k', ''))
        bufsize = f"{bitrate_value * 2}k"
        maxrate = bitrate
        
        # Gunakan nice untuk mengurangi prioritas proses di Linux
        if platform.system() == 'Linux':
            if cpulimit_available:
                # Gunakan cpulimit untuk membatasi penggunaan CPU
                base_command = ["cpulimit", "-l", "150", FFMPEG_PATH]
            else:
                # Gunakan nice jika cpulimit tidak tersedia
                base_command = ["nice", "-n", "10", FFMPEG_PATH]
        elif platform.system() == 'Darwin':  # macOS
            base_command = ["nice", "-n", "10", FFMPEG_PATH]
        else:
            # Windows tidak mendukung nice atau cpulimit
            base_command = [FFMPEG_PATH]
        
        # Bangun perintah FFmpeg
        ffmpeg_args = [
            "-loglevel", "warning",
            "-thread_queue_size", "16384",
            "-stream_loop", "-1", "-re", "-i", file_path,
            "-b:v", bitrate, "-bufsize", bufsize, "-maxrate", maxrate,
            "-f", "flv", "-c:v", "copy", "-c:a", "copy",
            "-flvflags", "no_duration_filesize",
            f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
        ]
        
        # Gabungkan base_command dan ffmpeg_args
        command = base_command + ffmpeg_args
        
        # Jalankan perintah tanpa shell=True untuk keamanan
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True
        )
        
        with process_lock:
            processes[live_id] = process
        
        if duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            delay = (stop_time - datetime.now()).total_seconds()
            if delay > 5:
                threading.Timer(delay, stop_stream_manually, args=[live_id, True, True]).start()
                send_telegram_notification(f"⏳ Live '{info['title']}' akan berhenti otomatis dalam {duration} menit.")
        
        # Untuk stream jangka panjang, tambahkan log bahwa stream telah dimulai
        if duration == 0:
            logging.info(f"Stream jangka panjang '{info['title']}' telah dimulai (ID: {live_id})")
            send_telegram_notification(f"🚀 Stream jangka panjang '{info['title']}' telah dimulai dan akan berjalan terus menerus")
        
        # Simpan waktu mulai untuk monitoring
        live_info[live_id]['start_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_live_info()
        
        # Tunggu proses selesai (ini akan memblokir sampai proses berakhir)
        process.wait()
        
    except Exception as e:
        logging.error(f"FFmpeg error in run_ffmpeg_with_nice: {str(e)}")
        send_telegram_notification(f"🚨 GAGAL menjalankan live '{info['title']}': {str(e)}")

def run_ffmpeg(live_id, info):
    try:
        logging.debug(f"Starting FFmpeg for live_id: {live_id} with info: {info}")
        if live_info[live_id]['status'] == 'Scheduled':
            send_telegram_notification(f"🎥 Live terjadwal '{info['title']}' TELAH DIMULAI!")
        else:
            send_telegram_notification(f"🎥 Live '{info['title']}' TELAH AKTIF!")

        if live_id in live_info:
            live_info[live_id]['status'] = 'Active'
            live_info[live_id]['restart_count'] = 0
            live_info[live_id]['restart_timestamps'] = []
            save_live_info()

        # Gunakan fungsi run_ffmpeg_with_nice untuk menjalankan FFmpeg
        threading.Thread(target=run_ffmpeg_with_nice, args=[live_id, info]).start()

    except Exception as e:
        logging.error(f"FFmpeg error: {str(e)}")
        send_telegram_notification(f"🚨 GAGAL memulai live '{info['title']}': {str(e)}")

def stop_stream_manually(live_id, is_scheduled=False, force=False):
    logging.debug(f"Attempting to stop stream manually for live_id: {live_id}, force={force}")
    with process_lock:
        process = processes.pop(live_id, None)

    if process and process.poll() is None:
        # Hentikan proses dengan benar
        try:
            if platform.system() == 'Windows':
                # Windows menggunakan os.kill dengan sinyal CTRL_C_EVENT
                os.kill(process.pid, signal.CTRL_C_EVENT)
            else:
                # Unix menggunakan os.killpg
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            
            process.wait(timeout=5)
        except Exception as e:
            logging.error(f"Error stopping process: {str(e)}")
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                pass

    if live_id in live_info:
        live_info[live_id]['status'] = 'Stopped'
        save_live_info()

    title = live_info[live_id]['title']
    message = f"⏰ Live terjadwal '{title}' BERHENTI sesuai jadwal" if is_scheduled else f"⛔ Live '{title}' DIHENTIKAN manual"
    send_telegram_notification(message)

@app.route('/update_start_schedule/<id>', methods=['POST'])
@login_required
def update_start_schedule(id):
    if id not in live_info:
        return jsonify({'message': 'Stream tidak ditemukan!'}), 404

    try:
        data = request.json
        start_time = data.get('startTime')
        
        if not start_time:
            return jsonify({'message': 'Waktu mulai diperlukan!'}), 400
        
        # Konversi format datetime-local (YYYY-MM-DDThh:mm) ke format yang disimpan
        try:
            # Parse datetime dari input
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
            # Format untuk penyimpanan
            formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            
            # Update status dan waktu mulai
            live_info[id]['status'] = 'Scheduled'
            live_info[id]['startTime'] = formatted_time
            save_live_info()
            
            # Batalkan timer lama jika ada
            # (Ini memerlukan tracking timer, yang bisa ditambahkan jika diperlukan)
            
            # Buat timer baru untuk memulai stream pada waktu yang dijadwalkan
            schedule_time = datetime.strptime(formatted_time, "%Y-%m-%d %H:%M:%S")
            delay = max(0, (schedule_time - datetime.now()).total_seconds())
            
            if delay > 0:
                threading.Timer(delay, run_ffmpeg, args=[id, live_info[id]]).start()
                send_telegram_notification(f"✅ Live '{live_info[id]['title']}' dijadwalkan untuk mulai pada {formatted_time}.")
                return jsonify({'message': f'Jadwal mulai diperbarui! Stream akan dimulai pada {formatted_time}'})
            else:
                # Jika waktu sudah lewat, mulai stream sekarang
                threading.Thread(target=run_ffmpeg, args=[id, live_info[id]]).start()
                return jsonify({'message': 'Waktu jadwal sudah lewat, stream dimulai sekarang!'})
                
        except ValueError as e:
            logging.error(f"Error parsing date: {str(e)}")
            return jsonify({'message': f'Format tanggal tidak valid: {str(e)}'}), 400
            
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

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

def monitor_stream_health():
    while True:
        restart_counts = {}
        current_time = datetime.now()
        
        for live_id, info in live_info.items():
            if info['status'] == 'Active' and 'start_time' in info:
                try:
                    # Periksa jika stream sudah berjalan lebih dari 24 jam
                    start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
                    uptime_hours = (current_time - start_time).total_seconds() / 3600
                    
                    # Kirim notifikasi setiap 24 jam untuk stream jangka panjang
                    if uptime_hours > 24 and uptime_hours % 24 < 1:
                        send_telegram_notification(f"🕒 Live '{info['title']}' telah berjalan selama {int(uptime_hours)} jam")
                except Exception as e:
                    logging.error(f"Error calculating uptime: {str(e)}")
        
        # Cek setiap 15 menit
        time.sleep(900)

def monitor_resource_usage():
    while True:
        try:
            # Cek setiap 30 detik
            time.sleep(30)
            
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            
            # Log resource usage untuk monitoring
            logging.debug(f"Resource usage: CPU {cpu_percent}%, Memory {memory_percent}%")
            
            # Peringatan jika resource usage tinggi
            if cpu_percent > 90 or memory_percent > 90:
                message = f"⚠️ Peringatan: Penggunaan resource tinggi - CPU: {cpu_percent}%, Memory: {memory_percent}%"
                logging.warning(message)
                send_telegram_notification(message)
                
                # Jika memory sangat tinggi, hentikan stream yang paling tidak penting
                if memory_percent > 95:
                    # Cari stream dengan prioritas terendah
                    active_streams = [(id, info) for id, info in live_info.items() 
                                     if info['status'] == 'Active']
                    
                    if active_streams:
                        # Urutkan berdasarkan prioritas (jika ada) atau restart_count
                        sorted_streams = sorted(active_streams, 
                                              key=lambda x: x[1].get('priority', 0) or x[1].get('restart_count', 0))
                        
                        # Hentikan stream dengan prioritas terendah
                        if sorted_streams:
                            low_priority_id = sorted_streams[0][0]
                            stop_stream_manually(low_priority_id, force=True)
                            send_telegram_notification(f"🛑 Memory hampir penuh ({memory_percent}%). Stream '{live_info[low_priority_id]['title']}' dihentikan otomatis.")
        except Exception as e:
            logging.error(f"Error in resource monitoring: {str(e)}")

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def get_file_name_from_google_drive_url(url):
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string
        if title and "Google Drive" in title:
            return title.replace(" - Google Drive", "").strip()
        return "downloaded_video.mp4"
    except Exception as e:
        logging.error(f"Failed to get filename from Google Drive: {str(e)}")
        return f"downloaded_video_{uuid.uuid4().hex[:8]}.mp4"

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
        priority = data.get('priority', '5')  # Default priority 5 (medium)

        if not all([title, video_filename, stream_key]):
            return jsonify({'message': 'Missing parameters'}), 400

        video = next((v for v in uploaded_videos if v['filename'] == video_filename), None)
        if not video:
            return jsonify({'message': 'Video not found'}), 404

        # Cek jumlah stream aktif
        active_streams = sum(1 for info in live_info.values() if info['status'] == 'Active')
        
        # Periksa resource saat ini
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_percent = psutil.virtual_memory().percent
        
        # Peringatan jika sudah banyak stream aktif atau resource tinggi
        warning_message = None
        if active_streams >= 3:
            warning_message = f"⚠️ Peringatan: Sudah ada {active_streams} stream aktif. Menambahkan stream baru mungkin akan mempengaruhi performa."
        elif cpu_percent > 80 or memory_percent > 80:
            warning_message = f"⚠️ Peringatan: Resource sistem tinggi (CPU: {cpu_percent}%, Memory: {memory_percent}%). Menambahkan stream baru mungkin akan mempengaruhi performa."
        
        if warning_message:
            send_telegram_notification(warning_message)

        live_id = str(uuid.uuid4())
        live_info[live_id] = {
            'title': title,
            'video': video_filename,
            'streamKey': stream_key,
            'status': 'Scheduled' if schedule_date else 'Pending',
            'startTime': schedule_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'bitrate': f'{bitrate}k' if bitrate else '2500k',  # Default 2500k
            'duration': int(duration) if duration else 0,
            'priority': int(priority),
            'restart_count': 0,
            'restart_timestamps': []
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
            'id': live_id,
            'warning': warning_message
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
            with process_lock:
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
            with process_lock:
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
            with process_lock:
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
    
    # Tambahkan informasi uptime jika ada
    if 'start_time' in info and info['status'] == 'Active':
        try:
            start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
            uptime = datetime.now() - start_time
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            info['uptime'] = f"{days} hari, {hours} jam, {minutes} menit"
        except Exception as e:
            logging.error(f"Error calculating uptime: {str(e)}")
            info['uptime'] = "Tidak tersedia"
    
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
    # Tambahkan informasi uptime untuk semua stream aktif
    current_time = datetime.now()
    for info in live_info.values():
        if 'start_time' in info and info['status'] == 'Active':
            try:
                start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
                uptime = current_time - start_time
                days = uptime.days
                hours, remainder = divmod(uptime.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                info['uptime'] = f"{days} hari, {hours} jam, {minutes} menit"
            except Exception as e:
                logging.error(f"Error calculating uptime: {str(e)}")
                info['uptime'] = "Tidak tersedia"
    
    return jsonify(list(live_info.values()))

@app.route('/live_list')
@login_required
def live_list():
    # Tambahkan informasi uptime untuk tampilan
    current_time = datetime.now()
    for info in live_info.values():
        if 'start_time' in info and info['status'] == 'Active':
            try:
                start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
                uptime = current_time - start_time
                days = uptime.days
                hours, remainder = divmod(uptime.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                info['uptime'] = f"{days} hari, {hours} jam, {minutes} menit"
            except Exception as e:
                logging.error(f"Error calculating uptime: {str(e)}")
                info['uptime'] = "Tidak tersedia"
    
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

@app.route('/system_info')
@login_required
def system_info():
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        active_streams = sum(1 for info in live_info.values() if info['status'] == 'Active')
        
        # Hitung total uptime stream
        total_uptime = timedelta(0)
        current_time = datetime.now()
        for info in live_info.values():
            if 'start_time' in info and info['status'] == 'Active':
                try:
                    start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
                    total_uptime += (current_time - start_time)
                except:
                    pass
        
        days = total_uptime.days
        hours, remainder = divmod(total_uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        total_uptime_str = f"{days} hari, {hours} jam, {minutes} menit"
        
        info = {
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'memory_used': format_size(memory.used),
            'memory_total': format_size(memory.total),
            'disk_percent': disk.percent,
            'disk_used': format_size(disk.used),
            'disk_total': format_size(disk.total),
            'active_streams': active_streams,
            'total_uptime': total_uptime_str,
            'has_nvidia_gpu': has_nvidia_gpu,
            'cpulimit_available': cpulimit_available,
            'platform': platform.system(),
            'python_version': platform.python_version(),
            'ffmpeg_path': FFMPEG_PATH
        }
        
        return jsonify(info)
    except Exception as e:
        logging.error(f"Error getting system info: {str(e)}")
        return jsonify({'error': str(e)}), 500

if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

def send_telegram_notification(message):
    # Gunakan variabel global yang sudah dimuat
    if telegram_bot_token and telegram_chat_id:
        url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
        payload = {"chat_id": telegram_chat_id, "text": message}
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Telegram notification: {e}")

# Gunakan lock untuk thread-safe pada pengukuran jaringan
net_lock = threading.Lock()
last_net_io = None
last_time = None

@app.route('/system_stats')
@login_required
def system_stats():
    global last_net_io, last_time
    
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        with net_lock:
            # Get current network stats
            current_net_io = psutil.net_io_counters()
            current_time = time.time()
            
            download_speed = "0 Kbps"
            upload_speed = "0 Mbps"
            
            # Calculate speed if we have previous measurements
            if last_net_io and last_time:
                time_diff = current_time - last_time
                
                if time_diff > 0.5:  # Minimal 0.5 detik untuk akurasi
                    download_diff = current_net_io.bytes_recv - last_net_io.bytes_recv
                    upload_diff = current_net_io.bytes_sent - last_net_io.bytes_sent
                    
                    # Calculate speeds in Kbps and Mbps
                    download_kbps = (download_diff * 8) / (time_diff * 1000)  # bytes to kilobits
                    upload_mbps = (upload_diff * 8) / (time_diff * 1000000)   # bytes to megabits
                    
                    # Format with 2 decimal places
                    download_speed = f"{download_kbps:.2f} Kbps"
                    upload_speed = f"{upload_mbps:.2f} Mbps"
            
            # Update last measurements
            last_net_io = current_net_io
            last_time = current_time
        
        return jsonify({
            'cpu': f"{cpu_percent}%",
            'memory': f"{format_size(memory.used)} / {format_size(memory.total)}",
            'memory_percent': memory.percent,
            'download': download_speed,
            'upload': upload_speed
        })
    except Exception as e:
        logging.error(f"Error getting system stats: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
def format_size(size):
    """Format size in bytes to human-readable format with speed units"""
    # Convert to float if it's integer
    size = float(size)
    
    units = ['B', 'KB', 'MB', 'GB']
    unit_index = 0
    
    while size >= 1024 and unit_index < len(units)-1:
        size /= 1024
        unit_index += 1
        
    return f"{size:.1f} {units[unit_index]}"

@app.route('/set_telegram_bot', methods=['POST'])
@login_required
def set_telegram_bot():
    data = request.json
    bot_token = data.get('botToken')
    chat_id = data.get('chatId')
    if not bot_token or not chat_id:
        return jsonify({'message': 'Bot token and chat ID are required!'}), 400

    save_apibot_settings(bot_token, chat_id)
    
    # Perbarui variabel global setelah menyimpan
    global telegram_bot_token, telegram_chat_id
    telegram_bot_token = bot_token
    telegram_chat_id = chat_id
    
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

# Jalankan monitoring kesehatan stream
threading.Thread(target=monitor_stream_health, daemon=True).start()

# Jalankan monitoring resource
threading.Thread(target=monitor_resource_usage, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
