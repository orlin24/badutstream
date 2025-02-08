#!/bin/bash

# Update & Upgrade Sistem
echo "Updating and upgrading system..."
sudo apt update && sudo apt upgrade -y

# Install Nginx
echo "Installing Nginx..."
sudo apt install nginx -y
sudo systemctl enable nginx

# Konfigurasi Firewall
echo "Configuring firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw allow 1935/tcp
sudo ufw allow 5000
echo "Enabling UFW..."
echo "y" | sudo ufw enable

# Pindah ke Direktori Web
cd /var/www/html

# Clone Repository dari GitHub
echo "Cloning repository..."
git clone https://github.com/orlin24/badutstream.git
cd badutstream

# Beri Izin Akses 777 ke Folder
echo "Setting folder permissions to 777..."
sudo chmod -R 777 /var/www/html/badutstream

# Set Zona Waktu
echo "Setting timezone to Asia/Jakarta..."
sudo timedatectl set-timezone Asia/Jakarta

# Install Python Virtual Environment & FFmpeg
echo "Installing Python virtual environment and FFmpeg..."
sudo apt install python3.10-venv -y
sudo apt install ffmpeg -y

# Buat Virtual Environment dan Install Dependencies
echo "Creating virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask flask_cors gdown

# Jalankan Aplikasi
echo "Starting application..."
python3 app.py
