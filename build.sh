#!/usr/bin/env bash
# Render Build Script for Smart Asset Location Tracker
set -o errexit

echo "=========================================================="
echo " Starting Render Cloud Build Process                     "
echo "=========================================================="

# 1. Install Python Dependencies
echo "[build] Installing Python dependencies..."
pip install --upgrade pip
pip install -r backend/requirements.txt

# 2. Collect Static Files for WhiteNoise
echo "[build] Collecting static files..."
python backend/manage.py collectstatic --noinput

# 3. Apply Database Migrations
echo "[build] Applying database migrations..."
python backend/manage.py migrate --noinput

# 4. Provision Default Asset & Initial Geofence
echo "[build] Checking default asset provisioning..."
python -c "
import os, sys, django
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from tracker.models import Asset, GeofenceSettings, LocationSource

default_id = os.getenv('DEFAULT_ASSET_ID', 'ASSET-ESP32-01')
token = os.getenv('DEVICE_SECRET_TOKEN', 'esp32_token_mca_default')

asset, created = Asset.objects.get_or_create(
    device_id=default_id,
    defaults={
        'name': 'Primary ESP32 Tracker',
        'device_token': token,
        'current_mode': LocationSource.SOFTWARE_SIMULATION,
        'last_known_latitude': 12.971598,
        'last_known_longitude': 77.594566,
    }
)
gf, gf_created = GeofenceSettings.objects.get_or_create(
    asset=asset,
    defaults={
        'center_latitude': 12.971598,
        'center_longitude': 77.594566,
        'radius_meters': float(os.getenv('DEFAULT_GEOFENCE_RADIUS_METERS', 500)),
        'is_active': True,
    }
)
print(f'[build] Default asset {default_id} ready (created={created}). Geofence ready (created={gf_created}).')
"

echo "=========================================================="
echo " Render Build Complete Successfully                       "
echo "=========================================================="
