"""
Business Logic & Service Functions for Smart Asset Location Tracker.
Contains the Haversine formula, coordinate validation, geofence boundary engine,
and SOS alert processing logic.
"""

import math
from decimal import Decimal
from django.utils import timezone
from .models import (
    Asset,
    GeofenceSettings,
    LocationRecord,
    LocationSource,
    GeofenceTransition,
    SOSEvent,
    SOSStatus,
    SOSTriggerSource,
)

# Standard Mean Earth Radius in meters
EARTH_RADIUS_METERS = 6371000.0


def validate_coordinates(latitude, longitude):
    """
    Validates latitude and longitude values.
    Latitude must be between -90.0 and +90.0.
    Longitude must be between -180.0 and +180.0.
    
    Returns:
        tuple: (is_valid: bool, error_message: str | None, lat_float: float | None, lon_float: float | None)
    """
    if latitude is None or longitude is None:
        return False, "Coordinates cannot be null.", None, None

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (ValueError, TypeError):
        return False, "Coordinates must be valid numeric decimal numbers.", None, None

    if math.isnan(lat) or math.isnan(lon) or math.isinf(lat) or math.isinf(lon):
        return False, "Coordinates must be finite real numbers.", None, None

    if not (-90.0 <= lat <= 90.0):
        return False, f"Latitude {lat} is out of valid range [-90.0, 90.0].", None, None

    if not (-180.0 <= lon <= 180.0):
        return False, f"Longitude {lon} is out of valid range [-180.0, 180.0].", None, None

    return True, None, lat, lon


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculates great-circle distance between two geographic coordinates on Earth
    using the Haversine formula.

    Returns:
        float: Distance in meters (rounded to 2 decimal places).
    """
    lat1_rad = math.radians(float(lat1))
    lon1_rad = math.radians(float(lon1))
    lat2_rad = math.radians(float(lat2))
    lon2_rad = math.radians(float(lon2))

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (math.sin(dlat / 2.0) ** 2) + (
        math.cos(lat1_rad) * math.cos(lat2_rad) * (math.sin(dlon / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    distance = EARTH_RADIUS_METERS * c

    return round(distance, 2)


def evaluate_geofence(asset, latitude, longitude):
    """
    Evaluates current coordinates against active geofence boundary for the given asset.
    Detects ENTRY and EXIT transitions without emitting duplicates.
    
    Returns:
        dict: {
            "distance_meters": float | None,
            "is_inside": bool | None,
            "transition": GeofenceTransition
        }
    """
    try:
        geofence = asset.geofence_settings
    except GeofenceSettings.DoesNotExist:
        return {
            "distance_meters": None,
            "is_inside": None,
            "transition": GeofenceTransition.NONE,
        }

    if not geofence.is_active:
        return {
            "distance_meters": None,
            "is_inside": None,
            "transition": GeofenceTransition.NONE,
        }

    distance = haversine_distance(
        latitude,
        longitude,
        geofence.center_latitude,
        geofence.center_longitude,
    )
    is_inside = distance <= geofence.radius_meters

    # State transition detection
    previous_state = asset.is_inside_geofence

    if previous_state is None:
        # Initial reading: establish baseline state without false ENTRY/EXIT alert
        transition = GeofenceTransition.NONE
    elif not previous_state and is_inside:
        transition = GeofenceTransition.ENTRY
    elif previous_state and not is_inside:
        transition = GeofenceTransition.EXIT
    else:
        # Same state: prevent duplicate alerts
        transition = GeofenceTransition.NONE

    # Update asset's cached boundary state
    asset.is_inside_geofence = is_inside
    asset.save(update_fields=['is_inside_geofence'])

    return {
        "distance_meters": distance,
        "is_inside": is_inside,
        "transition": transition,
    }


def record_location(
    asset,
    latitude,
    longitude,
    source_type,
    altitude=None,
    speed=None,
    satellites_count=0,
    timestamp=None,
):
    """
    Validates, processes, and stores a location update for an asset.
    Strictly verifies source types (REAL_GPS vs DYNAMIC_SIMULATION vs STATIC_SIMULATION).
    """
    if source_type not in LocationSource.values:
        raise ValueError(f"Invalid source_type: '{source_type}'. Must be one of {LocationSource.values}.")

    is_valid, error_msg, lat_float, lon_float = validate_coordinates(latitude, longitude)
    if not is_valid:
        raise ValueError(error_msg)

    if timestamp is None:
        timestamp = timezone.now()

    # Calculate distance and geofence status
    geofence_result = evaluate_geofence(asset, lat_float, lon_float)

    # Decimal format for exact database storage
    lat_dec = Decimal(str(round(lat_float, 6)))
    lon_dec = Decimal(str(round(lon_float, 6)))

    record = LocationRecord.objects.create(
        asset=asset,
        latitude=lat_dec,
        longitude=lon_dec,
        altitude=altitude,
        speed=speed,
        satellites_count=satellites_count,
        source_type=source_type,
        distance_from_center_meters=geofence_result["distance_meters"],
        is_inside_geofence=geofence_result["is_inside"],
        geofence_transition=geofence_result["transition"],
        timestamp=timestamp,
    )

    # Update asset's denormalized latest state
    asset.last_known_latitude = lat_dec
    asset.last_known_longitude = lon_dec
    asset.last_known_source = source_type
    asset.last_known_timestamp = timestamp
    asset.current_mode = source_type
    asset.save(update_fields=[
        'last_known_latitude',
        'last_known_longitude',
        'last_known_source',
        'last_known_timestamp',
        'current_mode',
    ])

    return record


def process_sos_event(
    asset,
    trigger_source=SOSTriggerSource.HARDWARE_BUTTON,
    has_gps_fix=False,
    latitude=None,
    longitude=None,
    location_source=None,
    timestamp=None,
    max_gps_age_seconds=15,
):
    """
    Processes an emergency SOS alert.
    Enforces rule: Do not generate SOS location coordinates from stale or missing GPS records.
    """
    if timestamp is None:
        timestamp = timezone.now()

    final_lat = None
    final_lon = None
    final_source = None
    final_has_fix = False

    # Only accept coordinates if has_gps_fix is explicitly True AND fresh
    if has_gps_fix and latitude is not None and longitude is not None:
        is_valid, _, lat_float, lon_float = validate_coordinates(latitude, longitude)
        if is_valid:
            # Check for stale GPS timestamp if provided
            age_seconds = (timezone.now() - timestamp).total_seconds()
            if age_seconds <= max_gps_age_seconds:
                final_has_fix = True
                final_lat = Decimal(str(round(lat_float, 6)))
                final_lon = Decimal(str(round(lon_float, 6)))
                final_source = location_source if location_source in LocationSource.values else LocationSource.REAL_GPS

    sos_event = SOSEvent.objects.create(
        asset=asset,
        timestamp=timestamp,
        status=SOSStatus.ACTIVE,
        trigger_source=trigger_source,
        has_gps_fix=final_has_fix,
        latitude=final_lat,
        longitude=final_lon,
        location_source=final_source,
    )

    return sos_event


def resolve_sos_event(sos_event_id, resolved_by="Operator", notes=None):
    """
    Resolves an active SOS alert.
    """
    try:
        sos_event = SOSEvent.objects.get(id=sos_event_id)
    except SOSEvent.DoesNotExist:
        raise ValueError(f"SOSEvent with id {sos_event_id} does not exist.")

    sos_event.status = SOSStatus.RESOLVED
    sos_event.resolved_at = timezone.now()
    sos_event.resolved_by = resolved_by
    if notes:
        sos_event.notes = notes
    sos_event.save(update_fields=['status', 'resolved_at', 'resolved_by', 'notes'])

    return sos_event

