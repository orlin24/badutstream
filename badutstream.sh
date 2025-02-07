#!/bin/bash

# Auto Installer Script for Web Server on Ubuntu/Debian with Nginx and Flask

# Check if script is run as root
if [ "$(id -u)" != "0" ]; then
    echo "This script must be run as root!" >&2
    exit 1
fi

# Update and install dependencies
apt install -y nginx git curl unzip python3 python3-pip gdown

# Install optional components (e.g., PHP & MySQL if needed)
read -p "Do you need PHP and MySQL? [y/n]: " install_php_mysql
if [[ "$install_php_mysql" == "y" ]]; then
    apt install -y php-fpm php-mysql mysql-server
    systemctl enable mysql
    systemctl start mysql
fi

# Clone the project from GitHub
repo_url="https://github.com/orlin24/badutstream.git"
read -p "Enter the directory name for the project (e.g., /var/www/project): " project_dir

# Check if directory exists and create if not
if [ ! -d "$project_dir" ]; then
    mkdir -p "$project_dir"
    echo "Directory $project_dir created."
else
    echo "Directory $project_dir already exists. Files may be overwritten."
fi

# Clone the repository
git clone "$repo_url" "$project_dir"
if [ $? -ne 0 ]; then
    echo "Failed to clone repository. Exiting."
    exit 1
fi

# Set permissions for the project directory
chown -R www-data:www-data "$project_dir"
chmod -R 755 "$project_dir"

# Install Python dependencies
pip3 install -r "$project_dir/requirements.txt"

# Create an Nginx server block
read -p "Do you want to configure Nginx for IP-based access? [y/n]: " configure_nginx
if [[ "$configure_nginx" == "y" ]]; then
    nginx_config="/etc/nginx/sites-available/project"
    cat > "$nginx_config" <<EOL
server {
    listen 80;
    server_name _;
    root $project_dir;
    index index.html index.htm;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location ~ \.ht {
        deny all;
    }
}
EOL

    # Enable the Nginx site and reload Nginx
    ln -s "$nginx_config" /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    echo "Nginx configured successfully."
fi

# Create uploads directory if not exists
uploads_dir="$project_dir/uploads"
mkdir -p "$uploads_dir"
chown -R www-data:www-data "$uploads_dir"
chmod -R 755 "$uploads_dir"

# Add a systemd service for the Flask app
service_name="flask_app"
cat > "/etc/systemd/system/$service_name.service" <<EOL
[Unit]
Description=Gunicorn instance to serve Flask App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$project_dir
ExecStart=/usr/bin/python3 -m flask run --host=0.0.0.0 --port=5000

[Install]
WantedBy=multi-user.target
EOL

# Enable and start the Flask app service
systemctl daemon-reload
systemctl enable $service_name
systemctl start $service_name

# Finish
echo "Installation complete! Your application should now be live."
echo "Access it via: http://<your-vps-ip>:5000"
