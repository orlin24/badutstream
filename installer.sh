#!/bin/bash

# Fungsi untuk validasi lisensi
validate_license() {
    local license_key=$1
    local api_url="http://209.97.167.85:5000/validate_license"
    local response=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"license_key\": \"${license_key}\"}" ${api_url})
    echo "Response from API: $response"  # Debugging line
    local status=$(echo $response | jq -r '.status')

    if [ "$status" == "valid" ]; then
        return 0
    else
        return 1
    fi
}

# Memeriksa dan menginstal jq jika tidak ditemukan
if ! command -v jq &> /dev/null
then
    echo "jq could not be found. Installing jq..."
    sudo apt-get update
    sudo apt-get install -y jq
fi

# Memeriksa apakah API Flask berjalan
echo "Checking if Flask API is running..."
if ! curl -s http://209.97.167.85:5000/ &> /dev/null
then
    echo "Flask API is not running. Please start the Flask API and try again."
    exit 1
fi

# Meminta License Key dari pengguna
read -p "Masukkan License Key Anda: " license_key

# Validasi License Key
echo "Memvalidasi License Key..."
if validate_license $license_key; then
    echo "Lisensi valid. Melanjutkan instalasi..."
else
    echo "Lisensi tidak valid. Instalasi diblokir."
    exit 1
fi

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
apt install python3-pip
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask flask_cors gdown

# Jalankan Aplikasi
echo "Starting application..."
python3 app.py
