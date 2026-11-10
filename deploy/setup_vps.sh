#!/bin/bash
# ==============================================================================
# VPS Kurulum ve Canlıya Alma Betiği (Ubuntu / Debian)
# Proje: Döviz Borç ve Alacak Takip Sistemi (PC & Mobil Uyumlu)
# ==============================================================================

set -e

echo "=== 1. Paket Güncellemesi ve Gereksinimlerin Kurulması ==="
apt update
apt install -y python3 sqlite3 nginx ufw certbot python3-certbot-nginx cron rsync

echo "=== 2. Güvenlik Duvarı (UFW) Yapılandırması ==="
# Sadece gerekli portlara izin ver (Veritabanı ve Python portu dışarıya ASLA açılmaz)
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (Let's Encrypt ve HTTPS yönlendirmesi için)
ufw allow 443/tcp   # HTTPS (Şifreli güvenli erişim)
ufw default deny incoming
ufw default allow outgoing
echo "y" | ufw enable || true

echo "=== 3. Uygulama Dizinlerinin Hazırlanması ==="
APP_DIR="/var/www/doviz-takip"
mkdir -p "$APP_DIR"
mkdir -p "$APP_DIR/persistent_data/backups"

echo "=== 4. Systemd Servisinin Kurulması ==="
cp deploy/doviz-takip.service /etc/systemd/system/doviz-takip.service
systemctl daemon-reload
systemctl enable doviz-takip.service
systemctl restart doviz-takip.service

echo "=== 5. Nginx Web Sunucusunun Yapılandırılması ==="
cp deploy/nginx.conf /etc/nginx/sites-available/doviz-takip
ln -sf /etc/nginx/sites-available/doviz-takip /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Nginx sözdizim testi
nginx -t
systemctl reload nginx

echo "=== 6. Otomatik Günlük Harici Yedekleme Görevi (Cron) ==="
# Her gece saat 03:00'te otomatik SQLite güvenli snapshot yedeği alır
CRON_JOB="0 3 * * * /usr/bin/sqlite3 /var/www/doviz-takip/persistent_data/accounting.db \".backup '/var/www/doviz-takip/persistent_data/backups/daily_\$(date +\\%Y\\%m\\%d).db'\""
(crontab -l 2>/dev/null | grep -Fv "accounting.db" ; echo "$CRON_JOB") | crontab -

echo "=============================================================================="
echo " KURULUM BAŞARIYLA TAMAMLANDI!"
echo "=============================================================================="
echo "1. Domain adresiniz hazır olduğunda SSL sertifikası almak için:"
echo "   sudo certbot --nginx -d your-domain.com -d www.your-domain.com"
echo ""
echo "2. Varsayılan Yönetici Giriş Bilgileri:"
echo "   Kullanıcı Adı: admin"
echo "   Geçici Şifre : admin123"
echo "   (İlk girişten sonra sağ üst / sol menüden şifrenizi hemen değiştiriniz)"
echo "=============================================================================="

