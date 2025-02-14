#!/bin/bash

# -------------------------------
# Update & Upgrade Sistem
# -------------------------------
echo "Updating and upgrading system..."
sudo apt update && sudo apt upgrade -y

# -------------------------------
# Install Nginx
# -------------------------------
echo "Installing Nginx..."
sudo apt install nginx -y
sudo systemctl enable nginx

# -------------------------------
# Konfigurasi Firewall
# -------------------------------
echo "Configuring firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw allow 1935/tcp
sudo ufw allow 5000
echo "Enabling UFW..."
echo "y" | sudo ufw enable

# -------------------------------
# Pindah ke Direktori Web
# -------------------------------
cd /var/www/html

# -------------------------------
# Clone Repository dari GitHub
# -------------------------------
echo "Cloning repository..."
git clone https://github.com/orlin24/badutstream.git
# Asumsikan direktori hasil clone adalah "badutsream". Sesuaikan jika perlu.
cd badutsream

# -------------------------------
# Beri Izin Akses 777 ke Folder
# -------------------------------
echo "Setting folder permissions to 777..."
sudo chmod -R 777 /var/www/html/badutsream

# -------------------------------
# Set Zona Waktu
# -------------------------------
echo "Setting timezone to Asia/Jakarta..."
sudo timedatectl set-timezone Asia/Jakarta

# -------------------------------
# Install Python Virtual Environment, FFmpeg, Tmux & pip
# -------------------------------
echo "Installing Python virtual environment, FFmpeg, tmux, and pip..."
sudo apt install python3.10-venv -y
sudo apt install ffmpeg -y
sudo apt install tmux -y
sudo apt install python3-pip -y

# -------------------------------
# Buat Virtual Environment dan Install Dependencies
# -------------------------------
echo "Creating virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
# Menggunakan file requirements.txt untuk menginstal dependencies
pip install -r requirements.txt

# -------------------------------
# Jalankan Aplikasi di dalam tmux session awal (default: badutsream)
# -------------------------------
echo "Starting application in tmux session 'badutsream'..."
tmux new-session -d -s badutsream "cd /var/www/html/badutsream && source venv/bin/activate && python3 app.py; exec bash"
echo "Application started in tmux session 'badutsream'."
echo "Untuk attach session, jalankan: tmux attach-session -t badutsream"

# -------------------------------
# BAGIAN MENU
# -------------------------------
echo "Creating menu script..."

# Buat file menu.sh di /usr/local/bin
sudo tee /usr/local/bin/menu.sh > /dev/null << 'EOF'
#!/bin/bash
# Menu untuk mengelola tmux session aplikasi

# Meminta pengguna memasukkan nama session yang diinginkan
read -p "Masukkan nama session yang diinginkan: " SESSION_NAME
APP_DIR="/var/www/html/badutsream"

display_menu() {
    clear
    echo "===================================="
    echo "Pilih opsi:"
    echo "1) Jalankan aplikasi (buat tmux session baru)"
    echo "2) Masuk ke tmux session '$SESSION_NAME'"
    echo "3) Hentikan tmux session '$SESSION_NAME'"
    echo "4) Keluar"
    echo "===================================="
}

run_app() {
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "Session '$SESSION_NAME' sudah berjalan."
    else
        echo "Memulai aplikasi di tmux session '$SESSION_NAME'..."
        tmux new-session -d -s "$SESSION_NAME" "cd $APP_DIR && source venv/bin/activate && python3 app.py; exec bash"
        echo "Aplikasi telah dijalankan di tmux session '$SESSION_NAME'."
    fi
    read -n 1 -s -r -p "Tekan sembarang tombol untuk kembali ke menu..."
}

attach_session() {
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "Menghubungkan ke tmux session '$SESSION_NAME'..."
        tmux attach-session -t "$SESSION_NAME"
    else
        echo "Session '$SESSION_NAME' tidak ditemukan."
        read -n 1 -s -r -p "Tekan sembarang tombol untuk kembali ke menu..."
    fi
}

kill_session() {
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "Menghentikan tmux session '$SESSION_NAME'..."
        tmux kill-session -t "$SESSION_NAME"
        echo "Session telah dihentikan."
    else
        echo "Session '$SESSION_NAME' tidak ditemukan."
    fi
    read -n 1 -s -r -p "Tekan sembarang tombol untuk kembali ke menu..."
}

while true; do
    display_menu
    read -p "Masukkan pilihan (1/2/3/4): " pilihan
    case $pilihan in
        1)
            run_app
            ;;
        2)
            attach_session
            ;;
        3)
            kill_session
            ;;
        4)
            echo "Keluar..."
            exit 0
            ;;
        *)
            echo "Pilihan tidak valid."
            read -n 1 -s -r -p "Tekan sembarang tombol untuk kembali ke menu..."
            ;;
    esac
done
EOF

# Ubah permission menu.sh agar dapat dieksekusi
sudo chmod +x /usr/local/bin/menu.sh

echo "Menu script telah dibuat di /usr/local/bin/menu.sh."
echo "Untuk menjalankannya, cukup ketik 'menu.sh' di terminal."

# -------------------------------
# BAGIAN CUSTOM WELCOME MESSAGE (MOTD)
# -------------------------------
echo "Menginstal dan mengkonfigurasi custom welcome message dengan Neofetch, Figlet, dan Lolcat..."
sudo apt install neofetch figlet lolcat -y

sudo tee /etc/update-motd.d/99-neofetch > /dev/null << 'EOF'
#!/bin/bash
# Tampilkan tulisan besar "Badutstream" dengan figlet dan lolcat
figlet Badutstream | lolcat
echo ""
# Tampilkan informasi sistem dengan neofetch
neofetch
echo ""
# Tampilkan informasi kontak dalam teks besar
figlet "Tools: BadutStream Yt" | lolcat
figlet "Kontak: https://t.me/joss_ganteng" | lolcat
echo ""
# Tampilkan Running on http://IP-VPS:5000
IP=\$(curl -s ifconfig.me)
figlet "Running on http://\$IP:5000" | lolcat
echo ""
# Pesan untuk menjalankan menu
echo "Untuk menjalankannya, cukup ketik 'menu.sh' di terminal." | lolcat
EOF

sudo chmod +x /etc/update-motd.d/99-neofetch

echo "Custom welcome message telah dikonfigurasi. Logout dan login kembali untuk melihat hasilnya!"
