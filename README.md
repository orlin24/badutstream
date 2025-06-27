# BadutStream

BadutStream

## Installation Guide

### Prerequisites

Sebelum memulai instalasi, pastikan Anda memiliki:

- VPS Ubuntu 22.04 yang menjalankan Ubuntu/Debian
- Akses root ke VPS

### Installation Steps

Ikuti langkah-langkah berikut untuk menginstal BadutStream di VPS Anda:

1. **Clone the Repository**

   Buka terminal di VPS Anda dan jalankan perintah berikut untuk mengunduh dan menjalankan installer:

   ```bash
   wget -O installer.sh https://raw.githubusercontent.com/orlin24/badutstream/main/installer.sh
   chmod +x installer.sh
   ./installer.sh
   ```

Installer akan mengatur semua konfigurasi yang diperlukan secara otomatis.

  ```bash
cd /var/www/html/badutstream
source venv/bin/activate
python3 app.py
```

