#!/bin/bash
set -e  # Menghentikan script jika ada perintah yang gagal

# Update & Upgrade Sistem
echo -e "\e[32mUpdating and upgrading system...\e[0m"
sudo apt update && sudo apt upgrade -y

# Install Git
echo -e "\e[32mInstalling Git...\e[0m"
apt install git -y

# Konfigurasi Firewall
echo -e "\e[32mConfiguring firewall...\e[0m"
sudo ufw allow OpenSSH  # Mengizinkan akses SSH (port 22)
sudo ufw allow 5000/tcp  # Untuk aplikasi Python (Flask/FastAPI) di port 5000
sudo ufw allow 1935/tcp  # Untuk RTMP server
echo -e "\e[32mEnabling UFW...\e[0m"
echo "y" | sudo ufw enable

# Pastikan direktori /var/www/html ada
echo -e "\e[32mEnsuring /var/www/html exists...\e[0m"
sudo mkdir -p /var/www/html

# Pindah ke Direktori Web
cd /var/www/html

# Clone Repository dari GitHub jika belum ada
if [ ! -d "badutstream" ]; then
    echo -e "\e[32mCloning repository...\e[0m"
    git clone https://github.com/orlin24/badutstream.git
else
    echo -e "\e[32mRepository 'badutstream' already exists. Pulling latest changes...\e[0m"
    cd badutstream
    git pull
    cd ..
fi

cd badutstream

# Beri Izin Akses yang Lebih Aman
echo -e "\e[32mSetting folder permissions to 755 and ownership to www-data...\e[0m"
sudo chmod -R 755 .
sudo chown -R www-data:www-data .

# Set Zona Waktu
echo -e "\e[32mSetting timezone to Asia/Jakarta...\e[0m"
sudo timedatectl set-timezone Asia/Jakarta

# Install Python Virtual Environment, FFmpeg, tmux, dan pip
echo -e "\e[32mInstalling python3.10-venv, ffmpeg, tmux, and python3-pip...\e[0m"
sudo apt install python3.10-venv ffmpeg tmux python3-pip -y

# Buat Virtual Environment dan Install Dependencies
echo -e "\e[32mCreating virtual environment and installing dependencies...\e[0m"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo -e "\e[32mrequirements.txt not found. Skipping dependency installation.\e[0m"
fi

# Jalankan Aplikasi di dalam tmux session (default: badutstream)
echo -e "\e[32mStarting application in tmux session 'badutstream'...\e[0m"
if [ -f "app.py" ]; then
    tmux new-session -d -s badutstream "cd $(pwd) && source venv/bin/activate && python3 app.py; exec bash"
else
    echo -e "\e[32mError: app.py not found. Skipping tmux session.\e[0m"
fi

# Mendapatkan IP VPS
IP=$(hostname -I | awk '{print $1}')

echo -e "\e[32mLoopstream Berhasil di Install. Access via: http://$IP:5000\e[0m"
echo -e "\e[33mUsername: admin\e[0m"
echo -e "\e[33mPassword: admin\e[0m"

rm -f "$(realpath "$0")"
