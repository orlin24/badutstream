#!/bin/bash
set -e  # Berhenti jika ada perintah yang gagal

# ============================================================
# LoopStream - Auto Installer
# Cara pakai:
#   wget -O installer.sh https://raw.githubusercontent.com/orlin24/badutstream/main/installer.sh
#   chmod +x installer.sh
#   ./installer.sh
# ============================================================

export DEBIAN_FRONTEND=noninteractive

GREEN='\033[32m'
YELLOW='\033[33m'
CYAN='\033[36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }

# ---- Deteksi root / sudo ----
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    APP_USER="${SUDO_USER:-root}"
else
    SUDO="sudo"
    APP_USER="$USER"
fi

APP_DIR="/var/www/html/badutstream"
REPO_URL="https://github.com/orlin24/badutstream.git"

# ============================================================
# 1. Update sistem + instal dependensi
# ============================================================
info "Memperbarui sistem..."
$SUDO apt update -y
$SUDO apt upgrade -y || warn "apt upgrade gagal, melanjutkan instalasi..."

info "Menginstal Git, FFmpeg, cpulimit, python3..."
$SUDO apt install -y git ffmpeg cpulimit python3-pip python3-venv
ok "Dependensi sistem terpasang."

# ============================================================
# 2. Firewall (opsional, tidak fatal kalau gagal)
# ============================================================
if command -v ufw >/dev/null 2>&1; then
    info "Mengonfigurasi firewall (UFW)..."
    $SUDO ufw allow OpenSSH || true
    $SUDO ufw allow 5000/tcp || true   # Aplikasi web
    $SUDO ufw allow 1935/tcp || true   # RTMP (kalau dipakai)
    echo "y" | $SUDO ufw enable || true
    ok "Firewall dikonfigurasi."
else
    warn "UFW tidak ditemukan, dilewati."
fi

# ============================================================
# 3. Ambil kode aplikasi
# ============================================================
$SUDO mkdir -p /var/www/html
if [ ! -d "$APP_DIR/.git" ]; then
    info "Meng-clone repository..."
    $SUDO git clone "$REPO_URL" "$APP_DIR"
else
    info "Repository sudah ada, memperbarui ke versi terbaru..."
    cd "$APP_DIR"
    git fetch origin
    git reset --hard origin/HEAD
fi
ok "Kode aplikasi siap."

# Pastikan kepemilikan folder milik user yang menjalankan app
$SUDO chown -R "$APP_USER:$APP_USER" "$APP_DIR"
$SUDO chmod -R 755 "$APP_DIR"
cd "$APP_DIR"

# ============================================================
# 4. Zona waktu
# ============================================================
info "Mengatur zona waktu Asia/Jakarta..."
$SUDO timedatectl set-timezone Asia/Jakarta || true
ok "Zona waktu diatur."

# ============================================================
# 5. Virtual environment + dependensi Python
# ============================================================
if [ ! -x venv/bin/python ]; then
    info "Membuat virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
info "Menginstal dependensi Python..."
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    warn "requirements.txt tidak ditemukan, melewati instalasi dependensi."
fi
ok "Dependensi Python terpasang."

# ============================================================
# 6. Jalankan otomatis (systemd kalau ada, fallback tmux)
# ============================================================
if command -v systemctl >/dev/null 2>&1 && [ "$(ps -p 1 -o comm=)" = "systemd" ]; then
    info "Membuat service systemd 'badutstream' (auto-restart saat reboot/crash)..."
    SERVICE_FILE="/etc/systemd/system/badutstream.service"
    $SUDO tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=LoopStream - YouTube Live Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable badutstream
    $SUDO systemctl restart badutstream
    ok "Service systemd aktif (auto-restart + auto-start saat boot)."
    SERVICE_NOTE="Lihat log: journalctl -u badutstream -f"
else
    warn "systemd tidak tersedia, memakai tmux (tidak auto-start saat reboot)."
    $SUDO apt install -y tmux || true
    $SUDO pkill -f "$APP_DIR/venv/bin/python $APP_DIR/app.py" 2>/dev/null || true
    tmux kill-session -t badutstream 2>/dev/null || true
    tmux new-session -d -s badutstream "cd $APP_DIR && source venv/bin/activate && python3 app.py; exec bash"
    SERVICE_NOTE="Lihat log: tmux attach -t badutstream"
fi

# ============================================================
# 7. Informasi akses
# ============================================================
IP=$(hostname -I | awk '{print $1}')
echo ""
echo "======================================================"
echo "  LoopStream berhasil diinstal!"
echo ""
echo "  URL      : http://$IP:5000"
echo "  Username : admin"
echo "  Password : admin"
echo ""
echo "  $SERVICE_NOTE"
echo "  Ganti password default setelah login!"
echo "======================================================"

# Hapus installer setelah sukses
rm -f "$(realpath "$0")"
