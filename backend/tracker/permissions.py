"""
Security and Access Control for IoT Tracker APIs.
Enforces hardware token authentication for ESP32 and administrative control
for dashboard configuration and SOS resolution.
"""

from django.conf import settings
from rest_framework import permissions


def verify_device_credentials(request, asset):
    """
    Verifies that the incoming request has valid device credentials for the target asset.
    Checks 'X-Device-Token' header or 'device_token' in the request body.
    """
    token = request.headers.get('X-Device-Token')
    if not token and hasattr(request, 'data') and isinstance(request.data, dict):
        token = request.data.get('device_token')

    if not token:
        return False, "Missing device authentication token ('X-Device-Token' header or 'device_token' parameter)."

    # Verify against asset's provisioned secret
    if token != asset.device_token:
        return False, "Invalid device credentials for the specified asset."

    return True, None


def verify_dashboard_admin(request):
    """
    Enforces Django session authentication for operator dashboard operations.
    Rejects unauthenticated requests. No hardcoded or predictable keys accepted.
    CSRF validation is enforced automatically by Django on all state-changing requests.
    """
    # 1. Authenticated Django User via Session
    if request.user and request.user.is_authenticated:
        return True, None

    # 2. Authenticated Admin Session flag (set via Operator login)
    if hasattr(request, 'session') and request.session.get('is_dashboard_admin'):
        return True, None

    return False, "Unauthorized. Valid operator session required."

