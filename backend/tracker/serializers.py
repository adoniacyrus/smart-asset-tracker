"""
Django REST Framework Serializers for Smart Asset Location Tracker.
Handles serialization, deserialization, and strict validation for location telemetry,
geofencing, and SOS events.
"""

from decimal import Decimal
from rest_framework import serializers
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
from .services import validate_coordinates


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializes Asset state for dashboard and API consumption.
    Protects device_token from being exposed to the client.
    """
    device_token = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Asset
        fields = [
            'id',
            'device_id',
            'name',
            'description',
            'current_mode',
            'is_active',
            'last_known_latitude',
            'last_known_longitude',
            'last_known_source',
            'last_known_timestamp',
            'is_inside_geofence',
            'device_token',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class GeofenceSettingsSerializer(serializers.ModelSerializer):
    """
    Serializes and validates circular geofence boundary configurations.
    """
    device_id = serializers.CharField(source='asset.device_id', read_only=True)

    class Meta:
        model = GeofenceSettings
        fields = [
            'id',
            'device_id',
            'center_latitude',
            'center_longitude',
            'radius_meters',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'device_id', 'created_at', 'updated_at']

    def validate_center_latitude(self, value):
        val = float(value)
        if not (-90.0 <= val <= 90.0):
            raise serializers.ValidationError(f"Center latitude {val} must be between -90.0 and 90.0.")
        return value

    def validate_center_longitude(self, value):
        val = float(value)
        if not (-180.0 <= val <= 180.0):
            raise serializers.ValidationError(f"Center longitude {val} must be between -180.0 and 180.0.")
        return value

    def validate_radius_meters(self, value):
        if value < 10.0 or value > 100000.0:
            raise serializers.ValidationError(
                f"Radius {value}m is invalid. Must be between 10m and 100,000m (100km)."
            )
        return value


class LocationRecordSerializer(serializers.ModelSerializer):
    """
    Serializes persistent time-series location telemetry.
    """
    device_id = serializers.CharField(source='asset.device_id', read_only=True)

    class Meta:
        model = LocationRecord
        fields = [
            'id',
            'device_id',
            'latitude',
            'longitude',
            'altitude',
            'speed',
            'satellites_count',
            'source_type',
            'distance_from_center_meters',
            'is_inside_geofence',
            'geofence_transition',
            'timestamp',
            'created_at',
        ]
        read_only_fields = ['id', 'device_id', 'created_at']


class SOSEventSerializer(serializers.ModelSerializer):
    """
    Serializes emergency SOS alert events.
    """
    device_id = serializers.CharField(source='asset.device_id', read_only=True)

    class Meta:
        model = SOSEvent
        fields = [
            'id',
            'device_id',
            'timestamp',
            'status',
            'trigger_source',
            'has_gps_fix',
            'latitude',
            'longitude',
            'location_source',
            'resolved_at',
            'resolved_by',
            'notes',
        ]
        read_only_fields = ['id', 'device_id']


# ------------------------------------------------------------------------------
# Input Payload Validation Serializers
# ------------------------------------------------------------------------------

class LocationIngestSerializer(serializers.Serializer):
    """
    Validates incoming telemetry ingestion payloads from ESP32 or simulation modes.
    """
    device_id = serializers.CharField(max_length=64, required=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    source_type = serializers.ChoiceField(choices=LocationSource.choices, required=True)
    altitude = serializers.FloatField(required=False, allow_null=True)
    speed = serializers.FloatField(required=False, allow_null=True)
    satellites_count = serializers.IntegerField(required=False, default=0, min_value=0)
    timestamp = serializers.DateTimeField(required=False, default=None)

    def validate(self, data):
        lat = data.get('latitude')
        lon = data.get('longitude')
        is_valid, err_msg, _, _ = validate_coordinates(lat, lon)
        if not is_valid:
            raise serializers.ValidationError({"coordinates": err_msg})
        return data


class SOSIngestSerializer(serializers.Serializer):
    """
    Validates incoming emergency SOS alert payloads.
    """
    device_id = serializers.CharField(max_length=64, required=True)
    trigger_source = serializers.ChoiceField(
        choices=SOSTriggerSource.choices,
        default=SOSTriggerSource.HARDWARE_BUTTON
    )
    has_gps_fix = serializers.BooleanField(default=False)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    location_source = serializers.ChoiceField(
        choices=LocationSource.choices,
        required=False,
        allow_null=True
    )
    timestamp = serializers.DateTimeField(required=False, default=None)

    def validate(self, data):
        has_fix = data.get('has_gps_fix', False)
        lat = data.get('latitude')
        lon = data.get('longitude')

        if has_fix:
            if lat is None or lon is None:
                raise serializers.ValidationError({
                    "has_gps_fix": "When has_gps_fix is True, both latitude and longitude are required."
                })
            is_valid, err_msg, _, _ = validate_coordinates(lat, lon)
            if not is_valid:
                raise serializers.ValidationError({"coordinates": err_msg})
        return data


class GeofenceUpdateSerializer(serializers.Serializer):
    """
    Validates dynamic geofence configuration update requests.
    """
    device_id = serializers.CharField(max_length=64, required=False)
    center_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    center_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    radius_meters = serializers.FloatField(required=True, min_value=10.0, max_value=100000.0)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate(self, data):
        lat = data.get('center_latitude')
        lon = data.get('center_longitude')
        is_valid, err_msg, _, _ = validate_coordinates(lat, lon)
        if not is_valid:
            raise serializers.ValidationError({"coordinates": err_msg})
        return data


class SOSResolveSerializer(serializers.Serializer):
    """
    Validates SOS alert resolution payload.
    """
    resolved_by = serializers.CharField(max_length=100, required=False, default="Operator")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class SoftwareLocationTargetSerializer(serializers.ModelSerializer):
    """
    Serializes SoftwareLocationTarget records for dashboard inspection.
    """
    device_id = serializers.CharField(source='asset.device_id', read_only=True)

    class Meta:
        model = SoftwareLocationTarget
        fields = [
            'id',
            'device_id',
            'latitude',
            'longitude',
            'status',
            'sequence_number',
            'staged_by',
            'created_at',
            'dispatched_at',
            'dispatch_count',
            'consumed_at',
            'error_message',
        ]
        read_only_fields = ['id', 'device_id', 'created_at', 'dispatched_at', 'consumed_at']


class SoftwareTargetStageSerializer(serializers.Serializer):
    """
    Validates operator coordinate input staged for ESP32 retrieval.
    """
    device_id = serializers.CharField(max_length=64, required=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)

    def validate(self, data):
        lat = data.get('latitude')
        lon = data.get('longitude')
        is_valid, err_msg, _, _ = validate_coordinates(lat, lon)
        if not is_valid:
            raise serializers.ValidationError({"coordinates": err_msg})
        return data


class DeviceTelemetrySerializer(serializers.Serializer):
    """
    Validates incoming telemetry and physical output reports from the physical ESP32.
    """
    device_id = serializers.CharField(max_length=64, required=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=True)
    source_type = serializers.ChoiceField(
        choices=LocationSource.choices,
        default=LocationSource.SOFTWARE_SIMULATION
    )
    satellites_count = serializers.IntegerField(required=False, default=0, min_value=0)
    esp32_distance_meters = serializers.FloatField(required=False, allow_null=True)
    esp32_is_inside = serializers.BooleanField(required=False, allow_null=True)
    led_green_state = serializers.BooleanField(required=False, default=True)
    led_red_state = serializers.BooleanField(required=False, default=False)
    buzzer_state = serializers.BooleanField(required=False, default=False)
    sos_active = serializers.BooleanField(required=False, default=False)
    config_version = serializers.IntegerField(required=False, allow_null=True)
    target_id = serializers.IntegerField(required=False, allow_null=True)
    timestamp = serializers.DateTimeField(required=False, default=None)

    def validate(self, data):
        lat = data.get('latitude')
        lon = data.get('longitude')
        is_valid, err_msg, _, _ = validate_coordinates(lat, lon)
        if not is_valid:
            raise serializers.ValidationError({"coordinates": err_msg})
        return data


class DeviceClearSosAckSerializer(serializers.Serializer):
    """
    Validates ESP32 acknowledgement that it executed the remote SOS clear command.
    """
    device_id = serializers.CharField(max_length=64, required=True)
    clear_sos_ack = serializers.BooleanField(required=True)


