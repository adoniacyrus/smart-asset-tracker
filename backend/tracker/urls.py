"""
URL Routing for Tracker API endpoints.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Location Endpoints
    path('location/', views.LocationIngestView.as_view(), name='tracker-location-ingest'),
    path('status/', views.TrackerStatusView.as_view(), name='tracker-status'),
    path('history/', views.LocationHistoryView.as_view(), name='tracker-history'),

    # Geofence Endpoints
    path('geofence/', views.GeofenceSettingsView.as_view(), name='tracker-geofence'),

    # Hardware Device & Synchronized Configuration Endpoints
    path('device/config/', views.DeviceConfigView.as_view(), name='tracker-device-config'),
    path('device/telemetry/', views.DeviceTelemetryView.as_view(), name='tracker-device-telemetry'),
    path('device/target/', views.SoftwareTargetStageView.as_view(), name='tracker-device-target'),
    path('device/clear-sos-ack/', views.DeviceClearSosAckView.as_view(), name='tracker-device-clear-sos-ack'),

    # SOS Endpoints
    path('sos/', views.SOSTriggerView.as_view(), name='tracker-sos-trigger'),
    path('sos/active/', views.ActiveSOSView.as_view(), name='tracker-sos-active'),
    path('sos/history/', views.SOSHistoryView.as_view(), name='tracker-sos-history'),
    path('sos/<int:pk>/resolve/', views.SOSResolveView.as_view(), name='tracker-sos-resolve'),

    # Operator / Admin Authentication Endpoints
    path('auth/login/', views.AdminLoginView.as_view(), name='tracker-auth-login'),
    path('auth/logout/', views.AdminLogoutView.as_view(), name='tracker-auth-logout'),
    path('auth/status/', views.AdminAuthStatusView.as_view(), name='tracker-auth-status'),
]

