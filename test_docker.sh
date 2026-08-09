#!/bin/bash
# ============================================================
# Test auto-installer LoopStream di container Ubuntu 22.04
# Tanpa VPS - cukup Docker (https://www.docker.com/products/docker-desktop)
#
# Cara pakai:
#   chmod +x test_docker.sh
#   ./test_docker.sh
#
# Catatan: di dalam container tidak ada systemd, jadi installer
# otomatis memakai jalur tmux. Untuk test systemd + auto-start
# saat reboot, gunakan VM Ubuntu (mis. UTM) atau VPS asli.
# ============================================================

set -e

IMAGE="ubuntu:22.04"
NAME="badutstream-test"

echo "[*] Menarik image $IMAGE (sekitar 70 MB)..."
docker pull $IMAGE

echo "[*] Menyiapkan container..."
docker rm -f $NAME 2>/dev/null || true
docker run -d --name $NAME -p 5000:5000 $IMAGE sleep infinity

echo "[*] Menjalankan auto-installer di dalam container (bisa 2-5 menit)..."
docker exec $NAME bash -c "
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null
    apt-get install -y wget >/dev/null
    wget -q -O installer.sh https://raw.githubusercontent.com/orlin24/badutstream/main/installer.sh
    chmod +x installer.sh
    ./installer.sh
"

echo ""
echo "=============================================="
echo "  Instalasi di container SELESAI!"
echo ""
echo "  Buka di browser : http://localhost:5000"
echo "  Login           : admin / admin"
echo ""
echo "  Lihat log app   : docker exec $NAME tmux attach -t badutstream"
echo "  Keluar dari log : Ctrl+B lalu D"
echo "  Hapus container : docker rm -f $NAME"
echo "=============================================="
