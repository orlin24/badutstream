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

# Define the uploads directory
uploads_dir = 'uploads'
os.makedirs(uploads_dir, exist_ok=True)

# Define the path to the JSON file for storing video metadata
videos_json_path = os.path.join(uploads_dir, 'videos.json')

# Load uploaded videos from the JSON file
def load_uploaded_videos():
    if os.path.exists(videos_json_path):
        with open(videos_json_path, 'r') as file:
            return json.load(file)
    return []

# Save uploaded videos to the JSON file
def save_uploaded_videos():
    with open(videos_json_path, 'w') as file:
        json.dump(uploaded_videos, file)

# Initialize uploaded videos
uploaded_videos = load_uploaded_videos()

processes = {}  # Dictionary to hold multiple processes
live_info = {}

# User credentials for login (replace with a more secure method in production)
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
        title = request.form.get('title')
        video_filename = request.form.get('video')
        stream_key = request.form.get('streamKey')
        schedule_date = request.form.get('scheduleDate')

        # Debugging: Log the received data
        print(f"Received title: {title}")
        print(f"Received video_filename: {video_filename}")
        print(f"Received stream_key: {stream_key}")
        print(f"Received schedule_date: {schedule_date}")

        # Check if all required data is present
        if not title or not video_filename or not stream_key:
            return jsonify({'message': 'Missing required fields'}), 400

        # Find the video in the uploaded_videos list
        video = next((v for v in uploaded_videos if v['filename'] == video_filename), None)
        if not video:
            return jsonify({'message': 'Video not found'}), 400

        file_path = os.path.join(uploads_dir, video_filename)

        # Generate a unique id for the live stream
        live_id = str(uuid.uuid4())
        print(f"Generated live_id: {live_id}")  # Debugging: Log generated live_id

        # Store live info
        live_info[live_id] = {
            'title': title,
            'video': video_filename,
            'streamKey': stream_key,
            'status': 'Scheduled' if schedule_date else 'Active',
            'startTime': schedule_date if schedule_date else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        def run_ffmpeg():
            # Update status to "Active"
            live_info[live_id]['status'] = 'Active'

            # FFmpeg command with the provided options
            ffmpeg_command = [
                'ffmpeg', '-stream_loop', '-1', '-readrate', '1.05', '-i', file_path, 
                '-f', 'fifo', '-fifo_format', 'flv', '-map', '0:v', '-map', '0:a', 
                '-attempt_recovery', '1', '-max_recovery_attempts', '20', 
                '-recover_any_error', '1', '-tag:v', '7', '-tag:a', '10', 
                '-recovery_wait_time', '2', '-flags', '+global_header', '-c', 'copy', 
                f'rtmp://a.rtmp.youtube.com/live2/{stream_key}'
            ]

            # Run FFmpeg command
            process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            processes[live_id] = process

        if schedule_date:
            schedule_time = datetime.strptime(schedule_date, "%Y-%m-%dT%H:%M")
            delay = (schedule_time - datetime.now()).total_seconds()
            threading.Timer(delay, run_ffmpeg).start()
        else:
            run_ffmpeg()

        return jsonify({
            'message': 'Streaming scheduled successfully!' if schedule_date else 'Streaming started successfully!',
            'id': live_id
        })
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/stop_stream/<id>', methods=['POST'])
@login_required
def stop_stream(id):
    print(f"Received request to stop stream with ID: {id}")  # Log the received ID for debugging
    if id not in processes:
        return jsonify({'message': 'No streaming process with given ID!'}), 400

    try:
        # Terminate the FFmpeg process
        process = processes[id]
        process.terminate()
        process.wait()
        del processes[id]
        live_info[id]['status'] = 'Stopped'
        return jsonify({'message': 'Streaming stopped successfully!'})
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

@app.route('/restart_stream/<id>', methods=['POST'])
@login_required
def restart_stream(id):
    if id not in live_info:
        return jsonify({'message': 'Live info not found!'}), 404

    try:
        info = live_info[id]
        file_path = os.path.join(uploads_dir, info['video'])
        stream_key = info['streamKey']

        # FFmpeg command with the provided options
        ffmpeg_command = [
            'ffmpeg', '-stream_loop', '-1', '-readrate', '1.05', '-i', file_path, 
            '-f', 'fifo', '-fifo_format', 'flv', '-map', '0:v', '-map', '0:a', 
            '-attempt_recovery', '1', '-max_recovery_attempts', '20', 
            '-recover_any_error', '1', '-tag:v', '7', '-tag:a', '10', 
            '-recovery_wait_time', '2', '-flags', '+global_header', '-c', 'copy', 
            f'rtmp://a.rtmp.youtube.com/live2/{stream_key}'
        ]

        # Run FFmpeg command
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        processes[id] = process

        info['status'] = 'Active'
        info['startTime'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({'message': 'Streaming restarted successfully!'})
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'message': f'Error: {str(e)}'}), 500

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)