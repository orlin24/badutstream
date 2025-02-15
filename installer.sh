#!/bin/bash

# Update & Upgrade Sistem
echo "Updating and upgrading system..."
sudo apt update && sudo apt upgrade -y

# Install Nginx
echo "Installing Nginx..."
sudo apt install nginx -y
sudo systemctl enable nginx

echo "Configuring firewall..."
sudo ufw allow OpenSSH      # Mengizinkan akses SSH (port 22)
sudo ufw allow 5000/tcp     # Jika ada aplikasi Python (Flask/FastAPI) di port 5000
sudo ufw allow 1935/tcp     # Mengizinkan RTMP server
echo "Enabling UFW..."
echo "y" | sudo ufw enable

# Pindah ke Direktori Web
cd /var/www/html

# Clone Repository dari GitHub
echo "Cloning repository..."
git clone https://github.com/orlin24/badutstream.git
cd badutstream

# Beri Izin Akses 777 ke Folder (pastikan ini sesuai kebutuhan)
echo "Setting folder permissions to 777..."
sudo chmod -R 777 /var/www/html/badutstream

# Set Zona Waktu
echo "Setting timezone to Asia/Jakarta..."
sudo timedatectl set-timezone Asia/Jakarta

# Install Python Virtual Environment, FFmpeg, tmux, dan pip
echo "Installing Python virtual environment, FFmpeg, tmux, and pip..."
sudo apt install python3.10-venv ffmpeg tmux python3-pip -y

# Buat Virtual Environment dan Install Dependencies
echo "Creating virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# -------------------------------
# Jalankan Aplikasi di dalam tmux session (default: badutstream)
# -------------------------------
echo "Starting application in tmux session 'badutstream'..."
tmux new-session -d -s badutstream "cd /var/www/html/badutstream && source venv/bin/activate && python3 app.py; exec bash"
echo "Application started in tmux session 'badutstream'."
echo "Untuk attach session, jalankan: tmux attach-session -t badutstream"

# Jika ingin menjalankan aplikasi di terminal utama, aktifkan baris berikut
# echo "Starting application in current terminal..."
# python3 app.py
