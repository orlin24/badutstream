from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session
from flask_cors import CORS
from functools import wraps
import subprocess
import os
import uuid
import json
from datetime import datetime, timedelta
import threading
import gdown

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Required for session
CORS(app)  # Enable CORS for all routes

# Konfigurasi path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
uploads_dir = os.path.join(BASE_DIR, 'uploads')
os.makedirs(uploads_dir, exist_ok=True)

videos_json_path = os.path.join(uploads_dir, 'videos.json')
live_info_json_path = os.path.join(uploads_dir, 'live_info.json')
FFMPEG_PATH = '/opt/homebrew/bin/ffmpeg'  # Path absolut ke FFmpeg

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
            'startTime': schedule_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_live_info()

        def run_ffmpeg():
            try:
                live_info[live_id]['status'] = 'Active'
                save_live_info()
                
                ffmpeg_command = [
                    FFMPEG_PATH,
                    '-stream_loop', '-1',
                    '-readrate', '1.05',
                    '-i', file_path,
                    '-f', 'fifo',
                    '-fifo_format', 'flv',
                    '-map', '0:v',
                    '-map', '0:a',
                    '-attempt_recovery', '1',
                    '-max_recovery_attempts', '20',
                    '-recover_any_error', '1',
                    '-tag:v', '7',
                    '-tag:a', '10',
                    '-recovery_wait_time', '2',
                    '-flags', '+global_header',
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-b:v', '2500k',
                    '-maxrate', '2500k',
                    '-bufsize', '5000k',
                    '-g', '100', # Mengatur keyframe interval untuk 4 detik (25 fps * 4 detik = 100)
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-f', 'flv',
                    f'rtmp://a.rtmp.youtube.com/live2/{stream_key}'
                ]

                log_file = open(f'ffmpeg_{live_id}.log', 'w')
                
                process = subprocess.Popen(
                    ffmpeg_command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                
                processes[live_id] = process
                process.wait()
                
            except Exception as e:
                print(f"FFmpeg error: {str(e)}")
            finally:
                live_info[live_id]['status'] = 'Ended'
                save_live_info()
                log_file.close()

        if schedule_date:
            schedule_time = datetime.strptime(schedule_date, "%Y-%m-%dT%H:%M")
            delay = max(0, (schedule_time - datetime.now()).total_seconds())
            threading.Timer(delay, run_ffmpeg).start()
        else:
            threading.Thread(target=run_ffmpeg).start()

        return jsonify({
            'message': 'Stream scheduled' if schedule_date else 'Stream started',
            'id': live_id
        })

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'message': str(e)}), 500

@app.route('/stop_stream/<id>', methods=['POST'])
@login_required
def stop_stream(id):
    if id not in processes:
        return jsonify({'message': 'Stream not found'}), 404

    try:
        process = processes[id]
        process.terminate()
        process.wait(timeout=10)
        del processes[id]
        live_info[id]['status'] = 'Stopped'
        save_live_info()
        return jsonify({'message': 'Stream stopped'})
    except Exception as e:
        print(f"Stop error: {str(e)}")
        return jsonify({'message': str(e)}), 500

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
        def run_ffmpeg():
            try:
                live_info[id]['status'] = 'Active'
                save_live_info()
                
                ffmpeg_command = [
                    FFMPEG_PATH,
                    '-stream_loop', '-1',
                    '-readrate', '1.05',
                    '-i', file_path,
                    '-f', 'fifo',
                    '-fifo_format', 'flv',
                    '-map', '0:v',
                    '-map', '0:a',
                    '-attempt_recovery', '1',
                    '-max_recovery_attempts', '20',
                    '-recover_any_error', '1',
                    '-tag:v', '7',
                    '-tag:a', '10',
                    '-recovery_wait_time', '2',
                    '-flags', '+global_header',
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-b:v', '2500k',
                    '-maxrate', '2500k',
                    '-bufsize', '5000k',
                    '-g', '100', # Mengatur keyframe interval untuk 4 detik (25 fps * 4 detik = 100)
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-f', 'flv',
                    f'rtmp://a.rtmp.youtube.com/live2/{stream_key}'
                ]

                with open(f'ffmpeg_{id}.log', 'a') as log_file:
                    process = subprocess.Popen(
                        ffmpeg_command,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    processes[id] = process
                    process.wait()
                    
            except Exception as e:
                print(f"FFmpeg error: {str(e)}")
            finally:
                live_info[id]['status'] = 'Ended'
                save_live_info()

        # 4. Jalankan dalam thread terpisah
        threading.Thread(target=run_ffmpeg).start()
        
        # 5. Update waktu mulai
        live_info[id]['startTime'] = datetime.now().isoformat()
        save_live_info()

        return jsonify({'message': 'Stream berhasil di-restart!'})

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'message': f'Gagal restart: {str(e)}'}), 500

@app.route('/delete_stream/<id>', methods=['POST'])
@login_required
def delete_stream(id):
    print(f"Received request to delete stream with ID: {id}")  # Log the received ID for debugging
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404

    try:
        # Terminate the FFmpeg process if it is running
        if id in processes:
            process = processes[id]
            process.terminate()
            process.wait()
            del processes[id]

        # Remove the live info record
        del live_info[id]
        save_live_info()

        return jsonify({'message': 'Streaming deleted successfully!'})
    except Exception as e:
        print(f"Error: {str(e)}")
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
            print(f"Received file_url: {file_url}")  # Debug log

            # Use URL or file ID to download with gdown
            unique_filename = f"{uuid.uuid4()}.mp4"
            file_path = os.path.join(uploads_dir, unique_filename)
            gdown.download(url=file_url, output=file_path, quiet=False, fuzzy=True)
            print(f"Saving file to: {file_path}")  # Debug log

            # Get file size
            file_size = os.path.getsize(file_path)

            # Add the uploaded video to the list
            uploaded_videos.append({
                'filename': unique_filename,
                'original_name': file_url,
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
            print(f"Error: {str(e)}")
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

        # Ensure new filename has .mp4 extension
        if not new_filename.lower().endswith(".mp4"):
            new_filename += ".mp4"

        old_file_path = os.path.join(uploads_dir, old_filename)
        new_file_path = os.path.join(uploads_dir, new_filename)

        # Rename the file
        os.rename(old_file_path, new_file_path)

        # Update the uploaded_videos list
        for video in uploaded_videos:
            if video['filename'] == old_filename:
                video['filename'] = new_filename
                video['original_name'] = new_filename
                break

        # Save the uploaded videos to the JSON file
        save_uploaded_videos()

        return jsonify({
            'success': True,
            'message': 'Video renamed successfully!',
            'videos': uploaded_videos
        })
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/delete_video', methods=['POST'])
@login_required
def delete_video():
    try:
        filename = request.json['filename']
        file_path = os.path.join(uploads_dir, filename)

        # Delete the file
        os.remove(file_path)

        # Update the uploaded_videos list
        global uploaded_videos
        uploaded_videos = [video for video in uploaded_videos if video['filename'] != filename]

        # Save the uploaded videos to the JSON file
        save_uploaded_videos()

        return jsonify({'success': True, 'message': 'Video deleted successfully!', 'videos': uploaded_videos})
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

update_active_streams()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)