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

# 2. Collect Static Files for WhiteNoise (no DB required)
echo "[build] Collecting static files..."
python backend/manage.py collectstatic --noinput

echo "=========================================================="
echo " Render Build Complete Successfully                       "
echo "=========================================================="
