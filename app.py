from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session
from flask_cors import CORS
from functools import wraps
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
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default_secret_key')  # Load from env
CORS(app)  # Enable CORS for all routes

# Konfigurasi logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Konfigurasi path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')

# Tentukan path FFmpeg berdasarkan sistem operasi
if platform.system() == 'Linux':
    FFMPEG_PATH = '/usr/bin/ffmpeg'
elif platform.system() == 'Darwin':  # Darwin adalah nama lain untuk macOS
    FFMPEG_PATH = '/opt/homebrew/bin/ffmpeg'
else:
    raise Exception("Unsupported operating system")

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
        bitrate = info.get('bitrate', '5000k')  # Default to 5000k if bitrate is not set
        duration = int(info.get('duration', 0))  # Default to 0 (unlimited) if duration is not set

        if bitrate is None:
            bitrate = '5000k'
        
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
            # Schedule to stop the stream after the specified duration
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
        process.wait(timeout=10)
        del processes[live_id]
    if live_id in live_info:
        live_info[live_id]['status'] = 'Stopped'
    save_live_info()

def stop_all_active_streams():
    for live_id, info in live_info.items():
        if info['status'] == 'Active':
            stop_stream_manually(live_id)

def periodic_check():
    check_and_update_scheduled_streams()
    threading.Timer(60, periodic_check).start()

users = {
    "gebang": "gebang",
}

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def format_size(size):
    """Convert file size to human-readable format."""
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
        duration = data.get('duration')  # Durasi dalam menit

        if not all([title, video_filename, stream_key]):
            return jsonify({'message': 'Missing parameters'}), 400

        video = next((v for v in uploaded_videos if v['filename'] == video_filename), None)
        if not video:
            return jsonify({'message': 'Video not found'}), 404

        file_path = os.path.abspath(os.path.join(uploads_dir, video_filename))
        live_id = str(uuid.uuid4())

        live_info[live_id] = {
            'title': title,
            'video': video_filename,
            'streamKey': stream_key,
            'status': 'Scheduled' if schedule_date else 'Pending',
            'startTime': schedule_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'bitrate': f'{bitrate}k' if bitrate else '5000k',
            'duration': int(duration) if duration else 0  # Default to 0 (unlimited) if duration is not set
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
def stop_stream(id):
    if id not in live_info:
        return jsonify({'message': 'Stream not found'}), 404

    try:
        # Check if the process is running
        if id in processes:
            process = processes[id]
            process.terminate()
            process.wait(timeout=10)
            del processes[id]

        live_info[id]['status'] = 'Stopped'
        save_live_info()
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

        # Update bitrate in live_info
        live_info[id]['bitrate'] = f'{bitrate}k'
        save_live_info()

        # Restart the stream to apply the new bitrate
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
        
        # 1. Hentikan proses lama jika masih berjalan
        if id in processes:
            old_process = processes[id]
            if old_process.poll() is None:  # Proses masih aktif
                old_process.terminate()
                old_process.wait(timeout=10)
            del processes[id]

        # 2. Dapatkan parameter streaming
        file_path = os.path.abspath(os.path.join(uploads_dir, info['video']))
        stream_key = info['streamKey']
        
        # 3. Gunakan fungsi yang sama dengan start_stream
        logging.debug(f"Restarting stream {id}")
        threading.Thread(target=run_ffmpeg, args=[id, info]).start()

        # 4. Update waktu mulai
        live_info[id]['startTime'] = datetime.now().isoformat()
        save_live_info()

        return jsonify({'message': 'Stream berhasil di-restart!'})

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({'message': f'Gagal restart: {str(e)}'}), 500

@app.route('/delete_stream/<id>', methods=['POST'])
@login_required
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
    info['id'] = id  # Add ID to the data to be returned
    info['video_name'] = info['video'].split('_', 1)[-1]  # Extract the original name without the unique ID
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
            # Get Google Drive file URL from request
            file_url = request.json['file_url']
            logging.debug(f"Received file_url: {file_url}")

            # Get the original file name from Google Drive URL
            original_name = get_file_name_from_google_drive_url(file_url)
            unique_filename = f"{uuid.uuid4()}_{original_name}"
            file_path = os.path.join(uploads_dir, unique_filename)
            
            # Use URL or file ID to download with gdown
            gdown.download(url=file_url, output=file_path, quiet=False, fuzzy=True)
            logging.debug(f"Saving file to: {file_path}")

            # Get file size
            file_size = os.path.getsize(file_path)

            # Add the uploaded video to the list
            uploaded_videos.append({
                'filename': unique_filename,
                'original_name': original_name,
                'size': format_size(file_size),
                'upload_date': datetime.now().strftime("%Y-%m-%d")
            })

            # Save the uploaded videos to the JSON file
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

# Ensure that all active streams are marked as stopped on startup
stop_all_active_streams()

# Start periodic check for scheduled streams
periodic_check()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
