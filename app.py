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
process_lock = threading.RLock()  # RLock: restart_if_needed memanggil fungsi yang mengunci ulang di thread yang sama

# Konfigurasi path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')

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

def _atomic_json_write(path, data):
    """Tulis JSON secara atomik (temp + rename) agar tidak korup saat crash."""
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w') as file:
        json.dump(data, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, path)


def save_uploaded_videos():
    _atomic_json_write(videos_json_path, uploaded_videos)

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

def _read_log_tail(path, max_bytes=4096):
    """Baca N byte terakhir file log (untuk menampilkan penyebab stream mati)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode('utf-8', errors='replace')
    except OSError:
        return ''


def record_stream_failure(live_id, process=None):
    """Simpan penyebab terakhir stream mati (exit code + log ffmpeg) ke live_info
    agar bisa dilihat user di UI, bukan sekadar status berubah menjadi Stopped."""
    info = live_info.get(live_id)
    if not info:
        return
    tail = _read_log_tail(os.path.join(uploads_dir, f'ffmpeg_{live_id}.log'))
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    parts = []
    code = process.poll() if process is not None else None
    if code is not None and code != 0:
        parts.append(f"ffmpeg keluar dengan exit code {code}")
    elif process is not None:
        parts.append("ffmpeg keluar")
    if lines:
        parts.append("Log terakhir: " + " | ".join(lines[-5:]))
    if parts:
        info['last_error'] = ". ".join(parts)[:2000]
        save_live_info()


def _restart_stream_with_limit(live_id, info):
    """Restart stream dengan batas percobaan untuk mencegah loop tak terbatas."""
    attempts = record_restart_attempt(live_id)
    if attempts > RESTART_MAX_ATTEMPTS:
        logging.warning(
            f"Stream {live_id} gagal restart {attempts}x dalam {RESTART_WINDOW_SECONDS}s, "
            f"status diubah menjadi Stopped."
        )
        if live_id in live_info:
            prev_error = live_info[live_id].get('last_error') or ''
            give_up_msg = (
                f"Dihentikan otomatis setelah gagal restart {attempts}x "
                f"dalam {RESTART_WINDOW_SECONDS}s"
            )
            live_info[live_id]['status'] = 'Stopped'
            live_info[live_id]['last_error'] = (
                (prev_error + ' | ' + give_up_msg) if prev_error else give_up_msg
            )
            save_live_info()
        return
    # Jadwalkan restart dengan jeda; tandai pending supaya watchdog tidak
    # memasang timer ganda selama jeda berlangsung.
    with process_lock:
        pending_restarts.add(live_id)
    threading.Timer(RESTART_DELAY_SECONDS, _delayed_restart, args=[live_id]).start()


def _delayed_restart(live_id):
    with process_lock:
        pending_restarts.discard(live_id)
    info = live_info.get(live_id)
    # Batal jika stream sudah di-stop user saat menunggu jeda restart
    if not info or info.get('status') != 'Active':
        logging.info(f"Restart stream {live_id} dibatalkan (status bukan Active).")
        return
    logging.info(f"Menjalankan ulang stream {live_id}...")
    threading.Thread(target=run_ffmpeg_with_nice, args=[live_id, dict(info)], daemon=True).start()


def restart_if_needed():
    while True:
        with process_lock:
            live_ids = list(processes.keys())
            for live_id in list(live_info.keys()):
                info = live_info.get(live_id)
                if not info or info.get('status') != 'Active':
                    continue
                if live_id in pending_restarts:
                    continue
                process = processes.get(live_id)
                if process and process.poll() is not None:  # Proses sudah mati
                    del processes[live_id]
                    record_stream_failure(live_id, process)
                    logging.warning(f"Stream {live_id} mati tidak wajar, dijadwalkan restart...")
                    _restart_stream_with_limit(live_id, info)
                elif live_id not in processes:
                    # Proses hilang dari dictionary (mis. crash), restart otomatis
                    logging.warning(f"Tidak ada proses untuk live_id: {live_id}, restart dijadwalkan.")
                    _restart_stream_with_limit(live_id, info)
        # Cek setiap 10 detik untuk memastikan restart cepat
        time.sleep(10)

def save_live_info():
    _atomic_json_write(live_info_json_path, live_info)

# Inisialisasi variabel setelah load_data()
uploaded_videos = load_uploaded_videos()
live_info = load_live_info()
processes = {}
pending_restarts = set()  # live_id yang sudah dijadwalkan restart (anti duplikat)
start_timers = {}
stop_timers = {}
data_lock = threading.Lock()

# Konstanta restart otomatis
RESTART_MAX_ATTEMPTS = 3
RESTART_WINDOW_SECONDS = 600
RESTART_DELAY_SECONDS = 15  # jeda antar restart agar tidak storm & tidak membakar jatah percobaan


def cancel_start_timer(live_id):
    """Batalkan timer jadwal mulai untuk live_id (jika ada)."""
    timer = start_timers.pop(live_id, None)
    if timer and timer.is_alive():
        timer.cancel()


def cancel_stop_timer(live_id):
    """Batalkan timer stop otomatis untuk live_id (jika ada)."""
    timer = stop_timers.pop(live_id, None)
    if timer and timer.is_alive():
        timer.cancel()


def kill_process_group(process, timeout=5):
    """Hentikan proses beserta seluruh child process-nya (cpulimit, ffmpeg)."""
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            pass
    except Exception:
        pass


def kill_orphaned_ffmpeg():
    """Bunuh ffmpeg zombie yang masih streaming ke YouTube dari server lama
    (tertinggal saat server di-restart ketika stream masih live)."""
    killed = 0
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmd_parts = proc.info.get('cmdline') or []
                cmd = ' '.join(cmd_parts)
                # Cocokkan via nama proses ATAU path/argv ffmpeg di cmdline
                is_ffmpeg = 'ffmpeg' in name or any(
                    'ffmpeg' in part for part in cmd_parts
                )
                if is_ffmpeg and 'rtmp://a.rtmp.youtube.com/live2' in cmd:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        try:
                            proc.terminate()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    killed += 1
                    logging.warning(f"ffmpeg zombie (pid {proc.pid}) dihentikan saat startup.")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logging.error(f"Error membersihkan ffmpeg zombie: {str(e)}")
    if killed:
        logging.warning(f"Total {killed} proses ffmpeg zombie dihentikan saat startup.")


def cleanup_stale_logs():
    """Hapus file log ffmpeg milik stream yang sudah tidak ada."""
    removed = 0
    try:
        for name in os.listdir(uploads_dir):
            if not (name.startswith('ffmpeg_') and name.endswith('.log')):
                continue
            live_id = name[len('ffmpeg_'):-len('.log')]
            if live_id not in live_info:
                try:
                    os.remove(os.path.join(uploads_dir, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    if removed:
        logging.info(f"{removed} file log ffmpeg lama dibersihkan saat startup.")


def cap_log_sizes(max_bytes=5 * 1024 * 1024):
    """Pangkas file log ffmpeg yang membengkak (cegah disk penuh)."""
    try:
        for name in os.listdir(uploads_dir):
            if not (name.startswith('ffmpeg_') and name.endswith('.log')):
                continue
            path = os.path.join(uploads_dir, name)
            try:
                if os.path.getsize(path) > max_bytes:
                    with open(path, 'w'):
                        pass
                    logging.warning(f"Log {name} dipangkas karena >5 MB.")
            except OSError:
                pass
    except OSError:
        pass


def sanitize_filename(name):
    """Bersihkan nama file dari path separator dan karakter berbahaya."""
    return os.path.basename(str(name).replace('\\', '/')).strip()


def probe_video_compatibility(file_path):
    """Cek codec via ffprobe sebelum streaming.

    Stream dikirim dengan `-c copy` ke kontainer FLV/RTMP, jadi hanya
    H.264 (video) + AAC/MP3 (audio) yang valid; codec lain (HEVC, AV1,
    VP9, Opus) membuat ffmpeg langsung mati setiap kali start.
    Return (ok, pesan_error).
    """
    if not os.path.isfile(FFMPEG_PATH):
        return False, (
            f"FFmpeg tidak ditemukan di {FFMPEG_PATH}. "
            "Instal dulu: sudo apt install ffmpeg"
        )
    ffprobe_path = os.path.join(os.path.dirname(FFMPEG_PATH), 'ffprobe')
    if not os.path.isfile(ffprobe_path):
        return True, None  # ffprobe tidak ada: lewati validasi
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name", "-of", "json", file_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30
        )
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except Exception as e:
        logging.warning(f"Gagal memeriksa codec {file_path}: {e}")
        return True, None
    vcodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
    acodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
    ok_video = vcodec in ("h264", None)
    ok_audio = acodec in ("aac", "mp3", None)
    if ok_video and ok_audio:
        return True, None
    detail = []
    if not ok_video:
        detail.append(f"video codec '{vcodec}'")
    if not ok_audio:
        detail.append(f"audio codec '{acodec}'")
    return False, (
        "Video memakai " + " dan ".join(detail) +
        " yang tidak didukung RTMP/FLV (YouTube butuh H.264 + AAC/MP3). "
        "Re-encode dulu contoh: ffmpeg -i input.mp4 -c:v libx264 -preset veryfast "
        "-crf 23 -c:a aac output.mp4"
    )


def record_restart_attempt(live_id):
    """Catat percobaan restart dan kembalikan jumlah percobaan dalam window."""
    info = live_info.get(live_id)
    if not info:
        return 0
    now = datetime.now()
    cutoff = now - timedelta(seconds=RESTART_WINDOW_SECONDS)
    timestamps = []
    for ts in info.get('restart_timestamps', []):
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                timestamps.append(ts)
        except ValueError:
            pass
    timestamps.append(now.isoformat())
    info['restart_timestamps'] = timestamps
    info['restart_count'] = len(timestamps)
    save_live_info()
    return len(timestamps)


def video_in_use(filename):
    """True jika video sedang dipakai stream aktif/terjadwal."""
    return any(
        info.get('video') == filename and info.get('status') in ('Active', 'Scheduled')
        for info in live_info.values()
    )


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
    process_started = False
    log_file = None
    try:
        file_path = os.path.abspath(os.path.join(uploads_dir, info['video']))
        stream_key = info['streamKey']
        bitrate = info.get('bitrate', '2500k')  # Default bitrate lebih rendah
        duration = int(info.get('duration', 0))
        
        # Hitung buffer size berdasarkan bitrate
        bitrate_value = int(bitrate.replace('k', ''))
        bufsize = f"{bitrate_value * 2}k"
        maxrate = bitrate
        
        # Batas CPU adaptif dihapus: nice saja sudah cukup untuk `-c copy`.
        # CATATAN PENTING: cpulimit TIDAK dipakai lagi. cpulimit men-throttle
        # proses dengan SIGSTOP/SIGCONT yang membekukan aliran data RTMP;
        # ingest YouTube memutus koneksi ketika data berhenti mengalir, lalu
        # ffmpeg mati dan restart berulang (penyebab utama stream sering stop
        # di VPS Linux).
        if platform.system() in ('Linux', 'Darwin'):
            base_command = ["nice", "-n", "10", FFMPEG_PATH]
        else:
            base_command = [FFMPEG_PATH]
        
        # Bangun perintah FFmpeg
        ffmpeg_args = [
            "-loglevel", "warning",
            "-nostdin",
            "-thread_queue_size", "2048",
            "-stream_loop", "-1", "-re", "-i", file_path,
            "-b:v", bitrate, "-bufsize", bufsize, "-maxrate", maxrate,
            "-f", "flv", "-c:v", "copy", "-c:a", "copy",
            "-flvflags", "no_duration_filesize",
            f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
        ]
        command = base_command + ffmpeg_args
        
        # Tulis log ffmpeg ke file agar bisa dilihat via /stream_logs/<id>
        log_path = os.path.join(uploads_dir, f'ffmpeg_{live_id}.log')
        log_file = open(log_path, 'w')
        
        # Jalankan perintah tanpa shell=True untuk keamanan
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True
        )
        process_started = True
        
        with process_lock:
            # Jika stream sudah di-stop/dihapus saat proses ini sedang lahir
            # (race dengan klik Stop), jangan daftarkan - langsung matikan.
            if live_id not in live_info or live_info[live_id].get('status') != 'Active':
                should_abort = True
            else:
                processes[live_id] = process
                should_abort = False

        if should_abort:
            logging.warning(
                f"Stream {live_id} sudah berhenti saat proses baru lahir, "
                f"ffmpeg dimatikan."
            )
            kill_process_group(process)
            return
        
        if duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            delay = (stop_time - datetime.now()).total_seconds()
            if delay > 5:
                cancel_stop_timer(live_id)
                stop_timers[live_id] = threading.Timer(
                    delay, stop_stream_manually, args=[live_id, True, True]
                )
                stop_timers[live_id].start()
                logging.info(f"Live '{info['title']}' akan berhenti otomatis dalam {duration} menit.")
        
        # Untuk stream jangka panjang, tambahkan log bahwa stream telah dimulai
        if duration == 0:
            logging.info(f"Stream jangka panjang '{info['title']}' telah dimulai (ID: {live_id})")
        
        # Simpan waktu mulai untuk monitoring
        if live_id in live_info:
            live_info[live_id]['start_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_live_info()
        
        # Tunggu proses selesai (ini akan memblokir sampai proses berakhir)
        process.wait()
        
    except Exception as e:
        logging.error(f"FFmpeg error in run_ffmpeg_with_nice: {str(e)}")
        if live_id in live_info:
            if not process_started and live_info[live_id].get('status') == 'Active':
                # Gagal sebelum proses jalan: jangan biarkan status menggantung di 'Active'
                live_info[live_id]['status'] = 'Stopped'
            if not process_started:
                live_info[live_id]['last_error'] = f"Gagal memulai ffmpeg: {str(e)}"[:500]
                save_live_info()
    finally:
        if log_file:
            log_file.close()

def run_ffmpeg(live_id, info):
    try:
        logging.debug(f"Starting FFmpeg for live_id: {live_id} with info: {info}")
        # Cegah start ganda: kalau proses sudah berjalan, abaikan panggilan ini
        with process_lock:
            existing = processes.get(live_id)
        if existing is not None and existing.poll() is None:
            logging.debug(f"Stream {live_id} sudah berjalan, start duplikat dilewati.")
            return

        cancel_start_timer(live_id)
        cancel_stop_timer(live_id)

        if live_id in live_info:
            live_info[live_id]['status'] = 'Active'
            live_info[live_id]['restart_count'] = 0
            live_info[live_id]['restart_timestamps'] = []
            live_info[live_id]['last_error'] = ''
            save_live_info()

        # Gunakan fungsi run_ffmpeg_with_nice untuk menjalankan FFmpeg
        threading.Thread(target=run_ffmpeg_with_nice, args=[live_id, info]).start()

    except Exception as e:
        logging.error(f"FFmpeg error: {str(e)}")

def stop_stream_manually(live_id, is_scheduled=False, force=False):
    logging.debug(f"Attempting to stop stream manually for live_id: {live_id}, force={force}")
    cancel_start_timer(live_id)
    cancel_stop_timer(live_id)

    with process_lock:
        process = processes.pop(live_id, None)
        # Set status 'Stopped' SEBELUM membunuh proses, di dalam lock yang sama
        # dengan registrasi proses baru: mencegah restart_if_needed atau proses
        # restart yang sedang lahir menyalakan ulang stream ini (race condition).
        if live_id in live_info:
            live_info[live_id]['status'] = 'Stopped'
            live_info[live_id].pop('last_error', None)  # stop manual: bersihkan error lama
            save_live_info()

    if process and process.poll() is None:
        kill_process_group(process)

    if live_id in live_info:
        title = live_info[live_id].get('title', 'Stream')
        logging.info(f"Stream '{title}' dihentikan (is_scheduled={is_scheduled}, force={force})")

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
            
            # Batalkan timer lama sebelum membuat yang baru (mencegah start ganda)
            cancel_start_timer(id)
            
            # Buat timer baru untuk memulai stream pada waktu yang dijadwalkan
            schedule_time = datetime.strptime(formatted_time, "%Y-%m-%d %H:%M:%S")
            delay = max(0, (schedule_time - datetime.now()).total_seconds())
            
            if delay > 0:
                start_timers[id] = threading.Timer(delay, run_ffmpeg, args=[id, live_info[id]])
                start_timers[id].start()
                logging.info(f"Live '{live_info[id]['title']}' dijadwalkan mulai pada {formatted_time}.")
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
        if duration < 0 or duration > 2880:
            return jsonify({'message': 'Durasi harus antara 0-2880 menit'}), 400

        live_info[id]['duration'] = duration
        save_live_info()

        # Batalkan timer stop lama: durasi baru menggantikan jadwal lama
        cancel_stop_timer(id)

        if id in processes and duration > 0:
            stop_time = datetime.now() + timedelta(minutes=duration)
            delay = (stop_time - datetime.now()).total_seconds()
            if delay > 5:
                stop_timers[id] = threading.Timer(delay, stop_stream_manually, args=[id, True, True])
                stop_timers[id].start()
                logging.info(f"Live '{live_info[id]['title']}' akan berhenti dalam {duration} menit.")

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
        # Pangkas log ffmpeg yang membengkak agar disk tidak penuh
        cap_log_sizes()
        current_time = datetime.now()
        
        for live_id, info in live_info.items():
            if info['status'] == 'Active' and 'start_time' in info:
                try:
                    # Periksa jika stream sudah berjalan lebih dari 24 jam
                    start_time = datetime.strptime(info['start_time'], "%Y-%m-%d %H:%M:%S")
                    uptime_hours = (current_time - start_time).total_seconds() / 3600
                    
                    # Log setiap kelipatan 24 jam untuk stream jangka panjang (sekali per hari)
                    day_marker = int(uptime_hours // 24)
                    if day_marker > info.get('last_24h_notify', 0):
                        info['last_24h_notify'] = day_marker
                        save_live_info()
                        logging.info(f"Live '{info['title']}' telah berjalan selama {int(uptime_hours)} jam")
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
                logging.warning(f"Penggunaan resource tinggi - CPU: {cpu_percent}%, Memory: {memory_percent}%")
                
                # Hentikan stream hanya jika memori BENAR-BENAR habis (ukuran
                # absolut, bukan persen): di VPS RAM kecil, persen > 95 sering
                # false positive sehingga stream dimatikan tanpa alasan jelas.
                mem_available_mb = memory.available / (1024 * 1024)
                if mem_available_mb < 300:
                    # Cari stream dengan prioritas terendah
                    active_streams = [(id, info) for id, info in live_info.items() 
                                     if info['status'] == 'Active']
                    
                    if active_streams:
                        # Urutkan berdasarkan prioritas (jika ada) atau restart_count
                        sorted_streams = sorted(active_streams, key=lambda x: x[1].get('priority', 5))
                        
                        # Hentikan stream dengan prioritas terendah
                        if sorted_streams:
                            low_priority_id = sorted_streams[0][0]
                            stop_stream_manually(low_priority_id, force=True)
                            logging.warning(
                                f"Memory tersisa {mem_available_mb:.0f} MB (<300 MB). "
                                f"Stream '{live_info[low_priority_id]['title']}' dihentikan otomatis."
                            )

            # Peringatan kapasitas disk (cegah disk penuh)
            try:
                disk = psutil.disk_usage(uploads_dir)
                if disk.percent > 90:
                    logging.warning(
                        f"Kapasitas disk {disk.percent}% terpakai "
                        f"({disk.free // (1024 ** 3)} GB bebas). "
                        f"Hapus video/log yang tidak terpakai."
                    )
            except OSError:
                pass
        except Exception as e:
            logging.error(f"Error in resource monitoring: {str(e)}")

def get_file_name_from_google_drive_url(url):
    try:
        response = requests.get(url, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string
        if title and "Google Drive" in title:
            return title.replace(" - Google Drive", "").strip()
        return "downloaded_video.mp4"
    except Exception as e:
        logging.error(f"Failed to get filename from Google Drive: {str(e)}")
        return f"downloaded_video_{uuid.uuid4().hex[:8]}.mp4"

# Konstanta retry download Google Drive
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAY = 15  # detik


def friendly_drive_error(detail):
    """Ubah pesan error gdown menjadi penjelasan yang mudah dipahami pengguna."""
    detail_lower = (detail or '').lower()
    if 'permission' in detail_lower and 'many accesses' in detail_lower:
        return ('Google Drive menolak download. Ada 2 kemungkinan penyebab: '
                '(1) file belum di-share sebagai "Anyone with the link" (Viewer), '
                'atau (2) URL Google Drive terkena limit download (file sering '
                'diunduh atau IP server diblokir Google). '
                'Solusi: set sharing file ke "Anyone with the link", atau '
                'gunakan URL dari Google Drive lain, lalu coba lagi.')
    if 'permission' in detail_lower or 'public link' in detail_lower:
        return ('Google Drive menolak download. Pastikan file di-share sebagai '
                '"Anyone with the link" (Viewer), lalu coba lagi. '
                'Jika sudah di-share, kemungkinan URL terkena limit download - '
                'gunakan URL dari Google Drive lain.')
    if '429' in detail_lower or 'many accesses' in detail_lower or 'too many' in detail_lower:
        return ('URL Google Drive terkena limit download (rate limit / 429). '
                'Biasanya terjadi jika file sering diunduh atau IP server '
                'diblokir Google. Solusi: gunakan URL dari Google Drive lain, '
                'tunggu beberapa saat, atau unduh dari koneksi/IP lain (mis. laptop).')
    return ('Gagal mengunduh dari Google Drive. Periksa: (1) link sudah benar, '
            '(2) file di-share "Anyone with the link", (3) jaringan server bisa '
            'mengakses Google. Detail: ' + str(detail))


def download_from_google_drive(file_url, output_path):
    """Download dari Google Drive dengan retry + backoff. Return (success, pesan_error)."""
    last_error = None
    for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
        # Bersihkan file parsial dari percobaan sebelumnya
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        try:
            result = gdown.download(url=file_url, output=output_path, quiet=False, fuzzy=True)
            if result is not None and os.path.exists(output_path):
                return True, None
            last_error = last_error or 'Google Drive menolak download (tanpa detail)'
        except Exception as e:
            last_error = str(e)
            logging.warning(f"gdown percobaan {attempt}/{DOWNLOAD_MAX_ATTEMPTS} gagal: {str(e)}")
        if attempt < DOWNLOAD_MAX_ATTEMPTS:
            logging.info(f"Coba lagi dalam {DOWNLOAD_RETRY_DELAY} detik...")
            time.sleep(DOWNLOAD_RETRY_DELAY)
    return False, friendly_drive_error(last_error)


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

        # Validasi codec: stream pakai `-c copy` ke FLV/RTMP, jadi video harus
        # H.264 + AAC/MP3. Codec lain membuat ffmpeg mati terus (restart loop).
        ok_codec, codec_error = probe_video_compatibility(
            os.path.join(uploads_dir, video['filename'])
        )
        if not ok_codec:
            return jsonify({'message': codec_error}), 400

        # Cek jumlah stream aktif
        active_streams = sum(1 for info in live_info.values() if info['status'] == 'Active')
        
        # Periksa resource saat ini
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_percent = psutil.virtual_memory().percent
        
        # Validasi bitrate dan durasi
        bitrate = str(bitrate).strip().lower() if bitrate else ''
        if bitrate:
            try:
                bitrate_value = int(bitrate.replace('k', ''))
                bitrate = f'{bitrate_value}k'
            except ValueError:
                return jsonify({'message': 'Bitrate tidak valid (contoh: 2500)'}), 400
        else:
            bitrate = '2500k'

        duration = int(duration) if duration else 0
        if duration < 0 or duration > 2880:
            return jsonify({'message': 'Durasi harus antara 0-2880 menit'}), 400

        priority = int(priority) if str(priority).isdigit() else 5

        # Peringatan jika sudah banyak stream aktif atau resource tinggi
        warning_message = None
        if active_streams >= 3:
            warning_message = f"Peringatan: Sudah ada {active_streams} stream aktif. Menambahkan stream baru mungkin akan mempengaruhi performa."
        elif cpu_percent > 80 or memory_percent > 80:
            warning_message = f"Peringatan: Resource sistem tinggi (CPU: {cpu_percent}%, Memory: {memory_percent}%). Menambahkan stream baru mungkin akan mempengaruhi performa."
        
        if warning_message:
            logging.warning(warning_message)

        live_id = str(uuid.uuid4())
        with data_lock:
            live_info[live_id] = {
                'title': title,
                'video': video_filename,
                'streamKey': stream_key,
                'status': 'Scheduled' if schedule_date else 'Pending',
                'startTime': schedule_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'bitrate': bitrate,
                'duration': duration,
                'priority': priority,
                'restart_count': 0,
                'restart_timestamps': []
            }
            save_live_info()

        if schedule_date:
            schedule_time = datetime.strptime(schedule_date, "%Y-%m-%dT%H:%M")
            delay = max(0, (schedule_time - datetime.now()).total_seconds())
            start_timers[live_id] = threading.Timer(delay, run_ffmpeg, args=[live_id, live_info[live_id]])
            start_timers[live_id].start()
            logging.info(f"Live terjadwal '{title}' akan dimulai pada {schedule_date}.")
        else:
            threading.Thread(target=run_ffmpeg, args=[live_id, live_info[live_id]]).start()
            logging.info(f"Live '{title}' telah dimulai.")

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
        bitrate = str(request.json['bitrate']).strip().lower()
        if not bitrate:
            return jsonify({'message': 'Bitrate is required'}), 400
        try:
            bitrate_value = int(bitrate.replace('k', ''))
        except ValueError:
            return jsonify({'message': 'Bitrate tidak valid (contoh: 2500)'}), 400

        live_info[id]['bitrate'] = f'{bitrate_value}k'
        save_live_info()

        if id in processes:
            with process_lock:
                process = processes.pop(id, None)
            if process and process.poll() is None:
                kill_process_group(process)
            threading.Thread(target=run_ffmpeg, args=[id, live_info[id]]).start()

        return jsonify({'message': 'Bitrate updated successfully! Stream restarted.'})
    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/stream_logs/<id>')
@login_required
def stream_logs(id):
    log_file = os.path.join(uploads_dir, f'ffmpeg_{id}.log')
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
        with process_lock:
            old_process = processes.pop(id, None)
        if old_process and old_process.poll() is None:
            kill_process_group(old_process)

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
        cancel_start_timer(id)
        cancel_stop_timer(id)

        with process_lock:
            process = processes.pop(id, None)
        if process and process.poll() is None:
            kill_process_group(process)

        with data_lock:
            del live_info[id]
            save_live_info()

        # Hapus file log ffmpeg yang sudah tidak terpakai
        try:
            os.remove(os.path.join(uploads_dir, f'ffmpeg_{id}.log'))
        except OSError:
            pass

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
    
    info = dict(live_info[id])
    info['id'] = id
    name = info.get('video', '')
    parts = name.split('_', 1)
    video_name = name
    if len(parts) > 1:
        try:
            # Awalan uuid (dengan atau tanpa strip) -> ambil nama file aslinya
            uuid.UUID(parts[0])
            video_name = parts[1]
        except ValueError:
            pass
    info['video_name'] = video_name
    
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
    # Tambahkan id dan informasi uptime untuk semua stream (tanpa memutasi data asli)
    current_time = datetime.now()
    result = []
    for live_id, info in live_info.items():
        item = dict(info)
        item['id'] = live_id
        if 'start_time' in item and item['status'] == 'Active':
            try:
                start_time = datetime.strptime(item['start_time'], "%Y-%m-%d %H:%M:%S")
                uptime = current_time - start_time
                days = uptime.days
                hours, remainder = divmod(uptime.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                item['uptime'] = f"{days} hari, {hours} jam, {minutes} menit"
            except Exception as e:
                logging.error(f"Error calculating uptime: {str(e)}")
                item['uptime'] = "Tidak tersedia"
        result.append(item)
    
    return jsonify(result)

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
            file_path = None
            file_url = request.json['file_url']
            # Hanya izinkan URL Google Drive untuk mencegah SSRF/download arbitrer
            if not file_url or ('drive.google.com' not in file_url and 'docs.google.com' not in file_url):
                return jsonify({'success': False, 'message': 'URL harus dari Google Drive'}), 400

            # Cegah disk penuh: tolak upload kalau sisa ruang terlalu sedikit
            disk = psutil.disk_usage(uploads_dir)
            if disk.free < 2 * 1024 * 1024 * 1024:  # kurang dari 2 GB bebas
                return jsonify({
                    'success': False,
                    'message': 'Ruang disk hampir penuh (sisa kurang dari 2 GB). '
                               'Hapus beberapa video yang tidak terpakai, lalu coba lagi.'
                }), 400

            original_name = sanitize_filename(get_file_name_from_google_drive_url(file_url))
            unique_filename = f"{uuid.uuid4()}_{original_name}"
            file_path = os.path.join(uploads_dir, unique_filename)

            # Download dengan retry; helper sudah membersihkan file parsial
            success, error_msg = download_from_google_drive(file_url, file_path)
            if not success:
                return jsonify({'success': False, 'message': error_msg}), 500

            file_size = os.path.getsize(file_path)
            with data_lock:
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
            # Bersihkan file parsial yang gagal ter-download
            try:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass
            return jsonify({'success': False, 'message': 'Gagal mengunduh video: ' + str(e)}), 500
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
        old_filename = sanitize_filename(request.json['old_filename'])
        new_filename = sanitize_filename(request.json['new_filename'])
        if not old_filename or not new_filename:
            return jsonify({'success': False, 'message': 'Nama file tidak valid'}), 400
        if new_filename == old_filename:
            return jsonify({'success': False, 'message': 'Nama baru sama dengan nama lama'}), 400
        if not new_filename.lower().endswith(".mp4"):
            new_filename += ".mp4"

        # Jangan izinkan rename video yang sedang dipakai stream aktif/terjadwal
        if video_in_use(old_filename):
            return jsonify({'success': False, 'message': 'Video sedang dipakai stream aktif/terjadwal'}), 400

        old_file_path = os.path.join(uploads_dir, old_filename)
        new_file_path = os.path.join(uploads_dir, new_filename)
        if not os.path.exists(old_file_path):
            return jsonify({'success': False, 'message': 'File tidak ditemukan'}), 404
        if os.path.exists(new_file_path):
            return jsonify({'success': False, 'message': 'Nama file sudah dipakai video lain'}), 400
        os.rename(old_file_path, new_file_path)

        with data_lock:
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
        filename = sanitize_filename(request.json['filename'])
        if not filename:
            return jsonify({'success': False, 'message': 'Nama file tidak valid'}), 400

        # Jangan izinkan hapus video yang sedang dipakai stream aktif/terjadwal
        if video_in_use(filename):
            return jsonify({'success': False, 'message': 'Video sedang dipakai stream aktif/terjadwal'}), 400

        file_path = os.path.join(uploads_dir, filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': 'File tidak ditemukan'}), 404
        os.remove(file_path)

        global uploaded_videos
        with data_lock:
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
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    
    while size >= 1024 and unit_index < len(units)-1:
        size /= 1024
        unit_index += 1
        
    return f"{size:.1f} {units[unit_index]}"

# Pastikan semua streaming aktif ditandai sebagai stopped saat startup
stop_all_active_streams()
# Bunuh ffmpeg zombie yang tertinggal dari server lama (restart saat stream live)
kill_orphaned_ffmpeg()
# Bersihkan file log ffmpeg milik stream yang sudah tidak ada
cleanup_stale_logs()

# Mulai pengecekan berkala untuk streaming terjadwal
periodic_check()

# Jalankan monitoring untuk restart otomatis
threading.Thread(target=restart_if_needed, daemon=True).start()

# Jalankan monitoring kesehatan stream
threading.Thread(target=monitor_stream_health, daemon=True).start()

# Jalankan monitoring resource
threading.Thread(target=monitor_resource_usage, daemon=True).start()

if __name__ == '__main__':
    try:
        from waitress import serve
        # Server produksi (thread pool) - lebih kuat untuk banyak klien & polling
        serve(app, host='0.0.0.0', port=5000, threads=32)
    except ImportError:
        # Fallback: server dev Flask (untuk pengembangan lokal)
        app.run(debug=False, host='0.0.0.0', port=5000)
