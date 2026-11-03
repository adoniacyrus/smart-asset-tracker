#!/bin/sh
set -e

echo "=========================================================="
echo " Starting Smart Asset Tracker Containerized Web Service  "
echo "=========================================================="

# 1. Wait for MySQL Database to become fully available
echo "[entrypoint] Checking MySQL database readiness at ${DB_HOST:-db}:${DB_PORT:-3306}..."

MAX_TRIES=30
COUNT=0

until python -c "
import sys, os
import MySQLdb

host = os.getenv('DB_HOST', 'db')
port = int(os.getenv('DB_PORT', '3306'))
user = os.getenv('DB_USER', 'tracker_user')
passwd = os.getenv('DB_PASSWORD', '')
db = os.getenv('DB_NAME', 'smart_tracker_db')

try:
    conn = MySQLdb.connect(host=host, port=port, user=user, passwd=passwd, db=db)
    conn.close()
    sys.exit(0)
except Exception as e:
    sys.exit(1)
" 2>/dev/null; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_TRIES" ]; then
        echo "[entrypoint] ERROR: Timed out waiting for MySQL database at ${DB_HOST:-db}:${DB_PORT:-3306}."
        exit 1
    fi
    echo "[entrypoint] Waiting for database to accept connections ($COUNT/$MAX_TRIES)..."
    sleep 2
done

echo "[entrypoint] MySQL is available and accepting connections."

# 2. Run Database Migrations
echo "[entrypoint] Applying database migrations..."
python manage.py migrate --noinput

# 3. Collect Static Files for WhiteNoise
echo "[entrypoint] Collecting static assets for WhiteNoise..."
python manage.py collectstatic --noinput

# 4. Seed Default Asset & Geofence if not already present
echo "[entrypoint] Checking default asset provisioning..."
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from tracker.models import Asset, GeofenceSettings, LocationSource

default_id = os.getenv('DEFAULT_ASSET_ID', 'ASSET-ESP32-01')
if not Asset.objects.filter(device_id=default_id).exists():
    token = os.getenv('DEVICE_SECRET_TOKEN', 'esp32_token_mca_default')
    asset = Asset.objects.create(
        device_id=default_id,
        name='Primary ESP32 Tracker',
        device_token=token,
        current_mode=LocationSource.SOFTWARE_SIMULATION,
        last_known_latitude=12.971598,
        last_known_longitude=77.594566,
    )
    GeofenceSettings.objects.create(
        asset=asset,
        center_latitude=12.971598,
        center_longitude=77.594566,
        radius_meters=float(os.getenv('DEFAULT_GEOFENCE_RADIUS_METERS', 500)),
        is_active=True,
    )
    print(f'[entrypoint] Successfully seeded default asset {default_id} and initial geofence.')
else:
    print(f'[entrypoint] Asset {default_id} already provisioned.')
"

# 5. Execute the Passed Container Command (Gunicorn WSGI)
echo "[entrypoint] Executing container command: $@"
exec "$@"
