#!/bin/bash
set -euo pipefail
# First boot on the dedicated Amazon Linux ARM collector. No trading services.
dnf install -y git nginx python3 awscli
id poly-metars >/dev/null 2>&1 || useradd --system --home-dir /var/lib/poly-metars --shell /sbin/nologin poly-metars
install -d -o poly-metars -g poly-metars /var/lib/poly-metars
if [ ! -d /opt/poly-metars/.git ]; then
    git clone https://github.com/olibren/poly-metars.git /opt/poly-metars
fi
cd /opt/poly-metars
python3 -m unittest discover -s tests -q
install -m 0644 deploy/poly-metars.service /etc/systemd/system/poly-metars.service
install -m 0644 deploy/poly-metars-backup.service /etc/systemd/system/poly-metars-backup.service
install -m 0644 deploy/poly-metars-backup.timer /etc/systemd/system/poly-metars-backup.timer
# Replace only the new instance's default web server configuration.
cat > /etc/nginx/nginx.conf <<'NGINX'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /run/nginx.pid;
include /usr/share/nginx/modules/*.conf;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    include /etc/nginx/conf.d/*.conf;
}
NGINX
install -m 0644 deploy/nginx.conf /etc/nginx/conf.d/poly-metars.conf
nginx -t
systemctl daemon-reload
systemctl enable --now poly-metars nginx
# The provisioning step creates /etc/poly-metars-backup with the bucket name.
if [ -f /etc/poly-metars-backup ]; then
    systemctl enable --now poly-metars-backup.timer
fi
