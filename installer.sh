#!/bin/bash
set -e  # Menghentikan script jika ada perintah yang gagal

# Update & Upgrade Sistem
echo "Updating and upgrading system..."
sudo apt update && sudo apt upgrade -y

# Konfigurasi Firewall
echo "Configuring firewall..."
sudo ufw allow OpenSSH  # Mengizinkan akses SSH (port 22)
sudo ufw allow 5000/tcp  # Untuk aplikasi Python (Flask/FastAPI) di port 5000
sudo ufw allow 1935/tcp  # Untuk RTMP server
echo "Enabling UFW..."
echo "y" | sudo ufw enable

# Pastikan direktori /var/www/html ada
echo "Ensuring /var/www/html exists..."
sudo mkdir -p /var/www/html

# Pindah ke Direktori Web
cd /var/www/html

# Clone Repository dari GitHub jika belum ada
if [ ! -d "badutstream" ]; then
    echo "Cloning repository..."
    git clone https://github.com/orlin24/badutstream.git
else
    echo "Repository 'badutstream' already exists. Pulling latest changes..."
    cd badutstream
    git pull
    cd ..
fi

cd badutstream

# Beri Izin Akses 777 ke Folder (pastikan sesuai kebutuhan keamanan)
echo "Setting folder permissions to 777..."
sudo chmod -R 777 .

# Set Zona Waktu
echo "Setting timezone to Asia/Jakarta..."
sudo timedatectl set-timezone Asia/Jakarta

# Install Python Virtual Environment, FFmpeg, tmux, dan pip
echo "Installing python3.10-venv, ffmpeg, tmux, and python3-pip..."
sudo apt install python3.10-venv ffmpeg tmux python3-pip -y

# Buat Virtual Environment dan Install Dependencies
echo "Creating virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "requirements.txt not found. Skipping dependency installation."
fi

# Jalankan Aplikasi di dalam tmux session (default: badutstream)
echo "Starting application in tmux session 'badutstream'..."
tmux new-session -d -s badutstream "cd $(pwd) && source venv/bin/activate && python3 app.py; exec bash"

# Mendapatkan IP VPS
IP=$(hostname -I | awk '{print $1}')

echo "Application started. Access it via: http://$IP:5000"
