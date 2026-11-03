"""
REST API Views for Smart Asset Location Tracker.
Provides endpoints for location telemetry ingestion, live status polling,
history queries, dynamic geofence management, and SOS emergency alerts.
"""

from decimal import Decimal
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import (
    Asset,
    GeofenceSettings,
    LocationRecord,
    LocationSource,
    GeofenceTransition,
    SOSEvent,
    SOSStatus,
    SOSTriggerSource,
    SoftwareLocationTarget,
    TargetStatus,
)
from .serializers import (
    AssetSerializer,
    GeofenceSettingsSerializer,
    LocationRecordSerializer,
    SOSEventSerializer,
    LocationIngestSerializer,
    SOSIngestSerializer,
    GeofenceUpdateSerializer,
    SOSResolveSerializer,
    SoftwareLocationTargetSerializer,
    SoftwareTargetStageSerializer,
    DeviceTelemetrySerializer,
    DeviceClearSosAckSerializer,
)
from .services import (
    record_location,
    process_sos_event,
    resolve_sos_event,
    haversine_distance,
    evaluate_geofence,
)
from .permissions import (
    verify_device_credentials,
    verify_dashboard_admin,
)


def get_target_asset(device_id=None):
    """
    Helper to resolve the target Asset by device_id or fallback to default asset.
    """
    if device_id:
        return Asset.objects.filter(device_id=device_id).first()

    default_id = getattr(settings, 'DEFAULT_ASSET_ID', 'ASSET-ESP32-01')
    asset = Asset.objects.filter(device_id=default_id).first()
    if not asset:
        asset = Asset.objects.first()
    return asset


# ------------------------------------------------------------------------------
# Location Endpoints
# ------------------------------------------------------------------------------

class LocationIngestView(APIView):
    """
    POST /api/tracker/location/
    Ingests location telemetry from the physical ESP32 or simulation modes.
    """
    def post(self, request):
        serializer = LocationIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data['device_id']
        source_type = data['source_type']

        asset = Asset.objects.filter(device_id=device_id).first()
        if not asset:
            return Response(
                {"error": f"Asset with device_id '{device_id}' does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Security check: REAL_GPS requires valid hardware credentials
        if source_type == LocationSource.REAL_GPS:
            authorized, err_msg = verify_device_credentials(request, asset)
            if not authorized:
                return Response(
                    {"error": "Unauthorized hardware device", "details": err_msg},
                    status=status.HTTP_401_UNAUTHORIZED
                )

        try:
            record = record_location(
                asset=asset,
                latitude=data['latitude'],
                longitude=data['longitude'],
                source_type=source_type,
                altitude=data.get('altitude'),
                speed=data.get('speed'),
                satellites_count=data.get('satellites_count', 0),
                timestamp=data.get('timestamp'),
            )
        except ValueError as e:
            return Response(
                {"error": "Location processing failed", "details": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "status": "success",
                "record_id": record.id,
                "device_id": asset.device_id,
                "source_type": record.source_type,
                "latitude": float(record.latitude),
                "longitude": float(record.longitude),
                "distance_from_center_meters": record.distance_from_center_meters,
                "is_inside_geofence": record.is_inside_geofence,
                "geofence_transition": record.geofence_transition,
                "timestamp": record.timestamp.isoformat(),
            },
            status=status.HTTP_201_CREATED
        )


class TrackerStatusView(APIView):
    """
    GET /api/tracker/status/
    Returns current asset status, coordinates, distance, geofence, and SOS alerts.
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        asset = get_target_asset(device_id)

        if not asset:
            return Response(
                {"error": "No active asset found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Distance calculation if geofence exists
        distance_meters = None
        geofence_data = None
        if hasattr(asset, 'geofence_settings') and asset.geofence_settings.is_active:
            gf = asset.geofence_settings
            geofence_data = {
                "center_latitude": float(gf.center_latitude),
                "center_longitude": float(gf.center_longitude),
                "radius_meters": gf.radius_meters,
                "is_active": gf.is_active,
            }
            if asset.last_known_latitude is not None and asset.last_known_longitude is not None:
                distance_meters = haversine_distance(
                    asset.last_known_latitude,
                    asset.last_known_longitude,
                    gf.center_latitude,
                    gf.center_longitude,
                )

        # Check for active SOS alert
        active_sos = asset.sos_events.filter(status=SOSStatus.ACTIVE).first()
        active_sos_data = None
        if active_sos:
            active_sos_data = {
                "id": active_sos.id,
                "timestamp": active_sos.timestamp.isoformat(),
                "trigger_source": active_sos.trigger_source,
                "has_gps_fix": active_sos.has_gps_fix,
                "latitude": float(active_sos.latitude) if active_sos.latitude else None,
                "longitude": float(active_sos.longitude) if active_sos.longitude else None,
            }

        # Latest operator-commanded location target
        latest_target = asset.software_location_targets.order_by('-created_at').first()
        operator_commanded = None
        if latest_target:
            operator_commanded = {
                "id": latest_target.id,
                "latitude": float(latest_target.latitude),
                "longitude": float(latest_target.longitude),
                "status": latest_target.status,
                "sequence_number": latest_target.sequence_number,
                "created_at": latest_target.created_at.isoformat(),
            }

        # Hardware connectivity evaluation (last seen within 15 seconds is online)
        now = timezone.now()
        last_seen_age = int((now - asset.last_seen_at).total_seconds()) if asset.last_seen_at else None
        last_tel_age = int((now - asset.last_telemetry_at).total_seconds()) if asset.last_telemetry_at else None
        is_online = (last_seen_age is not None and last_seen_age <= 15)

        # Boundary states representation
        backend_boundary = "INSIDE" if asset.is_inside_geofence is True else ("OUTSIDE" if asset.is_inside_geofence is False else None)
        esp32_boundary = "INSIDE" if asset.esp32_is_inside is True else ("OUTSIDE" if asset.esp32_is_inside is False else None)

        # Software targets lifecycle summary (surfacing failed/expired targets clearly)
        failed_target = asset.software_location_targets.filter(status=TargetStatus.FAILED).order_by('-created_at').first()
        targets_summary = {
            "pending_count": asset.software_location_targets.filter(status=TargetStatus.PENDING).count(),
            "dispatched_count": asset.software_location_targets.filter(status=TargetStatus.DISPATCHED).count(),
            "consumed_count": asset.software_location_targets.filter(status=TargetStatus.CONSUMED).count(),
            "failed_count": asset.software_location_targets.filter(status=TargetStatus.FAILED).count(),
            "latest_failed_target": SoftwareLocationTargetSerializer(failed_target).data if failed_target else None,
        }

        return Response({
            "device_id": asset.device_id,
            "name": asset.name,
            "current_mode": asset.current_mode,
            "is_active": asset.is_active,
            "last_known_latitude": float(asset.last_known_latitude) if asset.last_known_latitude else None,
            "last_known_longitude": float(asset.last_known_longitude) if asset.last_known_longitude else None,
            "last_known_source": asset.last_known_source,
            "last_known_timestamp": asset.last_known_timestamp.isoformat() if asset.last_known_timestamp else None,
            "is_inside_geofence": asset.is_inside_geofence,
            "distance_from_center_meters": distance_meters,
            "geofence": geofence_data,
            "active_sos": active_sos_data is not None,
            "active_sos_details": active_sos_data,

            # Configuration versioning & synchronization
            "config_version_requested": asset.config_version,
            "config_version_acknowledged": asset.last_processed_config_version,
            "is_sync_pending": (asset.config_version != asset.last_processed_config_version),

            # Separate operator-commanded vs ESP32-confirmed coordinates
            "operator_commanded_location": operator_commanded,
            "targets_summary": targets_summary,
            "esp32_confirmed_location": {
                "latitude": float(asset.esp32_latitude) if asset.esp32_latitude is not None else None,
                "longitude": float(asset.esp32_longitude) if asset.esp32_longitude is not None else None,
            },

            # Separate backend vs ESP32 distances and boundary states
            "backend_calculated_distance": distance_meters,
            "esp32_calculated_distance": asset.esp32_distance_meters,
            "backend_boundary_state": backend_boundary,
            "esp32_boundary_state": esp32_boundary,
            "boundary_mismatch": asset.boundary_mismatch,

            # ESP32 physical output states reported over telemetry
            "hardware_outputs": {
                "led_green": asset.led_green_state,
                "led_red": asset.led_red_state,
                "buzzer": asset.buzzer_state,
                "sos_active": asset.esp32_sos_active,
            },

            # Live hardware connection heartbeat
            "hardware_connection": {
                "is_online": is_online,
                "last_seen_at": asset.last_seen_at.isoformat() if asset.last_seen_at else None,
                "last_seen_age_seconds": last_seen_age,
                "last_telemetry_at": asset.last_telemetry_at.isoformat() if asset.last_telemetry_at else None,
                "last_telemetry_age_seconds": last_tel_age,
            },
        })


class LocationHistoryView(APIView):
    """
    GET /api/tracker/history/
    Queries time-series location history with optional filters (source_type, transition, limit).
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        source_type = request.query_params.get('source_type')
        transition = request.query_params.get('transition')
        try:
            limit = min(int(request.query_params.get('limit', 50)), 500)
        except ValueError:
            limit = 50

        queryset = LocationRecord.objects.select_related('asset').all()

        if device_id:
            queryset = queryset.filter(asset__device_id=device_id)
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if transition:
            queryset = queryset.filter(geofence_transition=transition)

        records = queryset.order_by('-timestamp')[:limit]
        serializer = LocationRecordSerializer(records, many=True)
        return Response(serializer.data)


# ------------------------------------------------------------------------------
# Geofence Endpoints
# ------------------------------------------------------------------------------

class GeofenceSettingsView(APIView):
    """
    GET /api/tracker/geofence/
    PUT /api/tracker/geofence/
    Reads or updates the dynamic circular geofence boundary configuration.
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        asset = get_target_asset(device_id)

        if not asset:
            return Response(
                {"error": "Asset not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if not hasattr(asset, 'geofence_settings'):
            return Response(
                {"error": "No geofence configured for this asset."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = GeofenceSettingsSerializer(asset.geofence_settings)
        return Response(serializer.data)

    def put(self, request):
        # Administrative Access Control
        authorized, err_msg = verify_dashboard_admin(request)
        if not authorized:
            return Response(
                {"error": "Forbidden", "details": err_msg},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GeofenceUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data.get('device_id')
        asset = get_target_asset(device_id)

        if not asset:
            return Response(
                {"error": f"Asset '{device_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Update or create GeofenceSettings
        geofence, created = GeofenceSettings.objects.get_or_create(
            asset=asset,
            defaults={
                'center_latitude': data['center_latitude'],
                'center_longitude': data['center_longitude'],
                'radius_meters': data['radius_meters'],
                'is_active': data.get('is_active', True),
            }
        )

        if not created:
            geofence.center_latitude = data['center_latitude']
            geofence.center_longitude = data['center_longitude']
            geofence.radius_meters = data['radius_meters']
            geofence.is_active = data.get('is_active', True)
            geofence.save()

        # Increment config_version so ESP32 detects geofence changes on next poll
        asset.config_version += 1

        # Update asset's boundary status silently WITHOUT creating false transition history
        if asset.last_known_latitude is not None and asset.last_known_longitude is not None:
            dist = haversine_distance(
                asset.last_known_latitude,
                asset.last_known_longitude,
                geofence.center_latitude,
                geofence.center_longitude,
            )
            asset.is_inside_geofence = (dist <= geofence.radius_meters)
            asset.save(update_fields=['is_inside_geofence', 'config_version'])
        else:
            asset.save(update_fields=['config_version'])

        out_serializer = GeofenceSettingsSerializer(geofence)
        return Response(out_serializer.data, status=status.HTTP_200_OK)


# ------------------------------------------------------------------------------
# SOS Endpoints
# ------------------------------------------------------------------------------

class SOSTriggerView(APIView):
    """
    POST /api/tracker/sos/
    Triggers an emergency SOS alert from ESP32 or Dashboard simulation.
    """
    def post(self, request):
        serializer = SOSIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data['device_id']
        trigger_source = data['trigger_source']

        asset = Asset.objects.filter(device_id=device_id).first()
        if not asset:
            return Response(
                {"error": f"Asset with device_id '{device_id}' does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Security check: Hardware button SOS requires valid device token
        if trigger_source == SOSTriggerSource.HARDWARE_BUTTON:
            authorized, err_msg = verify_device_credentials(request, asset)
            if not authorized:
                return Response(
                    {"error": "Unauthorized hardware device", "details": err_msg},
                    status=status.HTTP_401_UNAUTHORIZED
                )

        # Prevent simulation from submitting as REAL_GPS
        location_source = data.get('location_source')
        if trigger_source == SOSTriggerSource.DASHBOARD_SIMULATION and location_source == LocationSource.REAL_GPS:
            return Response(
                {"error": "Dashboard simulation cannot claim REAL_GPS location source."},
                status=status.HTTP_400_BAD_REQUEST
            )

        sos_event = process_sos_event(
            asset=asset,
            trigger_source=trigger_source,
            has_gps_fix=data.get('has_gps_fix', False),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            location_source=location_source,
            timestamp=data.get('timestamp'),
        )

        out_serializer = SOSEventSerializer(sos_event)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)


class ActiveSOSView(APIView):
    """
    GET /api/tracker/sos/active/
    Returns active SOS alerts for urgent dashboard notifications.
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        queryset = SOSEvent.objects.filter(status=SOSStatus.ACTIVE).select_related('asset')
        if device_id:
            queryset = queryset.filter(asset__device_id=device_id)

        serializer = SOSEventSerializer(queryset.order_by('-timestamp'), many=True)
        return Response(serializer.data)


class SOSHistoryView(APIView):
    """
    GET /api/tracker/sos/history/
    Queries all SOS event history.
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        try:
            limit = min(int(request.query_params.get('limit', 50)), 500)
        except ValueError:
            limit = 50

        queryset = SOSEvent.objects.select_related('asset').all()
        if device_id:
            queryset = queryset.filter(asset__device_id=device_id)

        records = queryset.order_by('-timestamp')[:limit]
        serializer = SOSEventSerializer(records, many=True)
        return Response(serializer.data)


class SOSResolveView(APIView):
    """
    POST /api/tracker/sos/<id>/resolve/
    Resolves an active SOS alert. Requires administrative access.
    """
    def post(self, request, pk):
        # Administrative Access Control
        authorized, err_msg = verify_dashboard_admin(request)
        if not authorized:
            return Response(
                {"error": "Forbidden", "details": err_msg},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = SOSResolveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            sos_event = resolve_sos_event(
                sos_event_id=pk,
                resolved_by=serializer.validated_data.get('resolved_by', 'Operator'),
                notes=serializer.validated_data.get('notes'),
            )
            # Instruct ESP32 node to clear its hardware latch on next poll
            sos_event.asset.pending_sos_clear = True
            sos_event.asset.config_version += 1
            sos_event.asset.save(update_fields=['pending_sos_clear', 'config_version'])
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_404_NOT_FOUND
            )

        out_serializer = SOSEventSerializer(sos_event)
        return Response(out_serializer.data, status=status.HTTP_200_OK)


# ------------------------------------------------------------------------------
# Operator / Administrative Authentication Endpoints
# ------------------------------------------------------------------------------

class AdminLoginView(APIView):
    """
    POST /api/tracker/auth/login/
    Authenticates administrative operator via username and password.
    Establishes Django HTTP session with secure session cookie.
    Enforces standard Django credentials; no predictable shared keys accepted.
    """
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return Response({
                "error": "Validation failed",
                "details": "Both username and password are required for operator authentication."
            }, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            request.session['is_dashboard_admin'] = True
            request.session['admin_user'] = user.username
            return Response({
                "status": "authenticated",
                "user": user.username,
                "method": "django_user"
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Authentication failed",
                "details": "Invalid username or password."
            }, status=status.HTTP_401_UNAUTHORIZED)


class AdminLogoutView(APIView):
    """
    POST /api/tracker/auth/logout/
    Logs out the administrative operator and clears the session.
    """
    def post(self, request):
        logout(request)
        request.session.flush()
        return Response({"status": "logged_out"}, status=status.HTTP_200_OK)


class AdminAuthStatusView(APIView):
    """
    GET /api/tracker/auth/status/
    Returns whether the current session is administratively authorized.
    """
    def get(self, request):
        is_user_auth = bool(request.user and request.user.is_authenticated)
        is_session_auth = bool(request.session.get('is_dashboard_admin'))
        is_auth = is_user_auth or is_session_auth

        user_label = None
        if is_user_auth:
            user_label = request.user.username
        elif is_session_auth:
            user_label = request.session.get('admin_user', 'Operator')

        return Response({
            "is_authenticated": is_auth,
            "user": user_label
        }, status=status.HTTP_200_OK)


# ------------------------------------------------------------------------------
# Hardware Device & Synchronized Configuration Endpoints
# ------------------------------------------------------------------------------

class DeviceConfigView(APIView):
    """
    GET /api/tracker/device/config/
    Polled by the ESP32 tracking node over Wi-Fi (every 2-3 seconds).
    Requires 'X-Device-Token' hardware authentication.
    Returns:
      - config_version: Monotonically increasing version counter.
      - geofence: Active geofence boundary (center_lat, center_lon, radius_meters, is_active).
      - target_location: Staged software coordinates (if pending), marked DISPATCHED.
      - clear_sos_command: True if operator resolved SOS and node should clear local latch.
    """
    def get(self, request):
        device_id = request.query_params.get('device_id')
        asset = get_target_asset(device_id)
        if not asset:
            return Response(
                {"error": f"Asset with device_id '{device_id}' does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        authorized, err_msg = verify_device_credentials(request, asset)
        if not authorized:
            return Response(
                {"error": "Unauthorized hardware device", "details": err_msg},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Heartbeat: record that device contacted backend
        asset.last_seen_at = timezone.now()
        asset.save(update_fields=['last_seen_at'])

        # Geofence configuration
        geofence_data = None
        if hasattr(asset, 'geofence_settings') and asset.geofence_settings.is_active:
            gf = asset.geofence_settings
            geofence_data = {
                "center_latitude": float(gf.center_latitude),
                "center_longitude": float(gf.center_longitude),
                "radius_meters": float(gf.radius_meters),
                "is_active": gf.is_active,
            }

        # Check for software target coordinates with reliable retry & recovery
        MAX_DISPATCH_RETRIES = 5
        DISPATCH_RETRY_INTERVAL_SECONDS = 10.0
        now = timezone.now()
        target_to_send = None

        # 1. Recovery check: Look for unconsumed targets previously marked DISPATCHED
        unconsumed_dispatched = asset.software_location_targets.filter(
            status=TargetStatus.DISPATCHED
        ).order_by('created_at', 'id').first()

        if unconsumed_dispatched:
            elapsed_sec = (now - unconsumed_dispatched.dispatched_at).total_seconds() if unconsumed_dispatched.dispatched_at else 999.0
            if unconsumed_dispatched.dispatch_count >= MAX_DISPATCH_RETRIES and elapsed_sec >= DISPATCH_RETRY_INTERVAL_SECONDS:
                # Exceeded retry limit: surface failure clearly and advance queue
                unconsumed_dispatched.mark_failed(
                    f"Target dispatch retry limit reached ({unconsumed_dispatched.dispatch_count} attempts) without device telemetry confirmation."
                )
            else:
                # Recover/re-dispatch: re-issue target to ESP32 (e.g. after Wi-Fi loss or reboot)
                unconsumed_dispatched.mark_dispatched()
                target_to_send = unconsumed_dispatched

        # 2. If no active dispatched target, pick the oldest PENDING target (FIFO queue)
        if not target_to_send:
            pending_target = asset.software_location_targets.filter(
                status=TargetStatus.PENDING
            ).order_by('created_at', 'id').first()

            if pending_target:
                pending_target.mark_dispatched()
                target_to_send = pending_target

        target_data = {"has_target": False}
        if target_to_send:
            target_data = {
                "has_target": True,
                "target_id": target_to_send.id,
                "latitude": float(target_to_send.latitude),
                "longitude": float(target_to_send.longitude),
                "sequence_number": target_to_send.sequence_number,
                "dispatch_count": target_to_send.dispatch_count,
            }

        return Response({
            "device_id": asset.device_id,
            "config_version": asset.config_version,
            "operating_mode": asset.current_mode,
            "geofence": geofence_data,
            "target_location": target_data,
            "clear_sos_command": asset.pending_sos_clear,
        }, status=status.HTTP_200_OK)


class DeviceTelemetryView(APIView):
    """
    POST /api/tracker/device/telemetry/
    Ingests live telemetry, calculated geofence distance, and physical GPIO output states
    from the physical ESP32 node.
    Requires 'X-Device-Token' hardware authentication.
    """
    def post(self, request):
        serializer = DeviceTelemetrySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data['device_id']
        asset = Asset.objects.filter(device_id=device_id).first()
        if not asset:
            return Response(
                {"error": f"Asset with device_id '{device_id}' does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        authorized, err_msg = verify_device_credentials(request, asset)
        if not authorized:
            return Response(
                {"error": "Unauthorized hardware device", "details": err_msg},
                status=status.HTTP_401_UNAUTHORIZED
            )

        now = timezone.now()
        timestamp = data.get('timestamp') or now

        # Update heartbeats
        asset.last_seen_at = now
        asset.last_telemetry_at = now

        # Server-side authoritative geofence evaluation
        lat_float = float(data['latitude'])
        lon_float = float(data['longitude'])
        geofence_result = evaluate_geofence(asset, lat_float, lon_float)

        esp32_inside = data.get('esp32_is_inside')
        server_inside = geofence_result['is_inside']
        boundary_mismatch = (
            esp32_inside is not None and
            server_inside is not None and
            esp32_inside != server_inside
        )

        # Decimal format for exact database storage
        lat_dec = Decimal(str(round(lat_float, 6)))
        lon_dec = Decimal(str(round(lon_float, 6)))

        # Update Asset live state
        asset.last_known_latitude = lat_dec
        asset.last_known_longitude = lon_dec
        asset.last_known_source = data['source_type']
        asset.last_known_timestamp = timestamp
        asset.is_inside_geofence = server_inside

        asset.esp32_latitude = lat_dec
        asset.esp32_longitude = lon_dec
        asset.esp32_distance_meters = data.get('esp32_distance_meters')
        asset.esp32_is_inside = esp32_inside
        asset.boundary_mismatch = boundary_mismatch
        asset.led_green_state = data.get('led_green_state', True)
        asset.led_red_state = data.get('led_red_state', False)
        asset.buzzer_state = data.get('buzzer_state', False)
        asset.esp32_sos_active = data.get('sos_active', False)

        if data.get('config_version') is not None:
            asset.last_processed_config_version = data['config_version']

        asset.save()

        # Check target_id for duplicate delivery prevention and consumption
        target_id = data.get('target_id')
        if target_id:
            target = SoftwareLocationTarget.objects.filter(id=target_id, asset=asset).first()
            if target and target.status == TargetStatus.CONSUMED:
                # DUPLICATE TARGET DELIVERY:
                # Duplicate target delivery must not create duplicate location records!
                existing_record = target.location_record
                return Response({
                    "status": "duplicate_ignored",
                    "message": f"Target #{target_id} has already been acknowledged and consumed.",
                    "record_id": existing_record.id if existing_record else None,
                    "device_id": asset.device_id,
                    "server_distance_meters": existing_record.distance_from_center_meters if existing_record else None,
                    "esp32_distance_meters": asset.esp32_distance_meters,
                    "server_is_inside": existing_record.is_inside_geofence if existing_record else asset.is_inside_geofence,
                    "esp32_is_inside": asset.esp32_is_inside,
                    "geofence_transition": "NONE",
                    "boundary_mismatch": asset.boundary_mismatch,
                    "config_version_acknowledged": asset.last_processed_config_version,
                    "config_version_current": asset.config_version,
                    "is_sync_pending": (asset.config_version != asset.last_processed_config_version),
                }, status=status.HTTP_200_OK)

        # Create LocationRecord (only for new/non-duplicate telemetry)
        record = LocationRecord.objects.create(
            asset=asset,
            latitude=lat_dec,
            longitude=lon_dec,
            satellites_count=data.get('satellites_count', 0),
            source_type=data['source_type'],
            distance_from_center_meters=geofence_result['distance_meters'],
            is_inside_geofence=server_inside,
            geofence_transition=geofence_result['transition'],
            timestamp=timestamp,
            esp32_distance_meters=data.get('esp32_distance_meters'),
            esp32_is_inside=esp32_inside,
            boundary_mismatch=boundary_mismatch,
            led_red_state=data.get('led_red_state'),
            led_green_state=data.get('led_green_state'),
            buzzer_state=data.get('buzzer_state'),
            sos_active=data.get('sos_active'),
            config_version=data.get('config_version'),
        )

        # Mark SoftwareLocationTarget as CONSUMED if target_id provided
        if target_id:
            target = SoftwareLocationTarget.objects.filter(id=target_id, asset=asset).first()
            if target and target.status in [TargetStatus.PENDING, TargetStatus.DISPATCHED]:
                target.mark_consumed(location_record=record)

        return Response({
            "status": "success",
            "record_id": record.id,
            "device_id": asset.device_id,
            "server_distance_meters": record.distance_from_center_meters,
            "esp32_distance_meters": record.esp32_distance_meters,
            "server_is_inside": record.is_inside_geofence,
            "esp32_is_inside": record.esp32_is_inside,
            "geofence_transition": record.geofence_transition,
            "boundary_mismatch": record.boundary_mismatch,
            "config_version_acknowledged": asset.last_processed_config_version,
            "config_version_current": asset.config_version,
            "is_sync_pending": (asset.config_version != asset.last_processed_config_version),
        }, status=status.HTTP_201_CREATED)


class SoftwareTargetStageView(APIView):
    """
    POST /api/tracker/device/target/
    Called by Dashboard Operator to stage new target coordinates for ESP32 retrieval.
    Requires administrative authorization.
    Increments Asset.config_version.
    """
    def post(self, request):
        authorized, err_msg = verify_dashboard_admin(request)
        if not authorized:
            return Response(
                {"error": "Forbidden", "details": err_msg},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = SoftwareTargetStageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data['device_id']
        asset = get_target_asset(device_id)
        if not asset:
            return Response(
                {"error": f"Asset '{device_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Sequence number for FIFO queue
        seq = (asset.software_location_targets.count() + 1)
        staged_by = request.user.username if request.user and request.user.is_authenticated else "Operator"

        target = SoftwareLocationTarget.objects.create(
            asset=asset,
            latitude=data['latitude'],
            longitude=data['longitude'],
            sequence_number=seq,
            staged_by=staged_by,
            status=TargetStatus.PENDING,
        )

        # Increment config version so ESP32 knows a new target is ready
        asset.config_version += 1
        asset.save(update_fields=['config_version'])

        return Response({
            "status": "staged",
            "target": SoftwareLocationTargetSerializer(target).data,
            "config_version": asset.config_version,
        }, status=status.HTTP_201_CREATED)


class DeviceClearSosAckView(APIView):
    """
    POST /api/tracker/device/clear-sos-ack/
    Called by ESP32 to confirm that it has cleared its physical hardware SOS latch.
    Requires 'X-Device-Token' hardware authentication.
    """
    def post(self, request):
        serializer = DeviceClearSosAckSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        device_id = data['device_id']
        asset = Asset.objects.filter(device_id=device_id).first()
        if not asset:
            return Response(
                {"error": f"Asset '{device_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        authorized, err_msg = verify_device_credentials(request, asset)
        if not authorized:
            return Response(
                {"error": "Unauthorized hardware device", "details": err_msg},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if data.get('clear_sos_ack'):
            asset.pending_sos_clear = False
            asset.esp32_sos_active = False
            asset.save(update_fields=['pending_sos_clear', 'esp32_sos_active'])

        return Response({
            "status": "acknowledged",
            "device_id": asset.device_id,
            "pending_sos_clear": asset.pending_sos_clear,
        }, status=status.HTTP_200_OK)




# ------------------------------------------------------------------------------
# Dashboard Template View
# ------------------------------------------------------------------------------

def dashboard_view(request):
    """
    Renders the interactive Leaflet.js and Bootstrap 5 IoT dashboard.
    """
    default_id = getattr(settings, 'DEFAULT_ASSET_ID', 'ASSET-ESP32-01')
    asset = get_target_asset(default_id)
    return render(request, 'tracker/dashboard.html', {
        'asset': asset,
        'default_asset_id': asset.device_id if asset else default_id,
    })
