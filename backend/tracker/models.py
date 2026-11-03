import secrets
from django.db import models
from django.utils import timezone


def generate_device_token():
    """
    Generates a cryptographically secure random token (32 bytes url-safe base64)
    for hardware ESP32 authentication. Prevents predictable default credentials.
    """
    return secrets.token_urlsafe(32)


class LocationSource(models.TextChoices):
    REAL_GPS = 'REAL_GPS', 'Real GPS'
    SOFTWARE_SIMULATION = 'SOFTWARE_SIMULATION', 'Software Simulation'
    DYNAMIC_SIMULATION = 'DYNAMIC_SIMULATION', 'Dynamic Simulation'
    STATIC_SIMULATION = 'STATIC_SIMULATION', 'Static Simulation'


class TargetStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Dispatch'
    DISPATCHED = 'DISPATCHED', 'Dispatched to ESP32'
    CONSUMED = 'CONSUMED', 'Consumed & Ingested'
    CANCELLED = 'CANCELLED', 'Cancelled by Operator'
    FAILED = 'FAILED', 'Failed Ingestion'


class GeofenceTransition(models.TextChoices):
    NONE = 'NONE', 'No Change'
    ENTRY = 'ENTRY', 'Entered Geofence'
    EXIT = 'EXIT', 'Exited Geofence'


class SOSStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active Alert'
    ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged'
    RESOLVED = 'RESOLVED', 'Resolved'


class SOSTriggerSource(models.TextChoices):
    HARDWARE_BUTTON = 'HARDWARE_BUTTON', 'Physical ESP32 Button'
    DASHBOARD_SIMULATION = 'DASHBOARD_SIMULATION', 'Dashboard Simulation'


class Asset(models.Model):
    """
    Represents an IoT asset / tracking node (e.g. ESP32 device).
    Maintains the current operating mode and latest known state for quick lookups.
    """
    device_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique hardware identifier, e.g., ASSET-ESP32-01"
    )
    name = models.CharField(max_length=100, default="Primary Asset")
    description = models.TextField(blank=True, null=True)

    current_mode = models.CharField(
        max_length=25,
        choices=LocationSource.choices,
        default=LocationSource.REAL_GPS
    )
    device_token = models.CharField(
        max_length=64,
        default=generate_device_token,
        help_text="Secret cryptographically random token for hardware ESP32 authentication",
        db_index=True
    )
    is_active = models.BooleanField(default=True)

    # Cached latest location state (denormalized for high performance)
    last_known_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_known_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_known_source = models.CharField(max_length=25, choices=LocationSource.choices, null=True, blank=True)
    last_known_timestamp = models.DateTimeField(null=True, blank=True)

    # Stateful tracker for geofence entry/exit detection (prevents duplicate transitions)
    is_inside_geofence = models.BooleanField(
        null=True,
        blank=True,
        help_text="Current state relative to active geofence (None = initial/uncalculated)"
    )

    # Configuration Versioning (increments whenever geofence or target coordinates change)
    config_version = models.PositiveIntegerField(
        default=1,
        help_text="Monotonically increasing version counter for geofence and coordinate configuration"
    )
    last_processed_config_version = models.PositiveIntegerField(
        default=0,
        help_text="Last configuration version successfully processed by the ESP32"
    )

    # Live Hardware Heartbeat & State Tracking
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last authenticated HTTP contact from the physical ESP32"
    )
    last_telemetry_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last telemetry report from the physical ESP32"
    )

    # ESP32-Reported Coordinates & Onboard Calculations
    esp32_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    esp32_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    esp32_distance_meters = models.FloatField(
        null=True,
        blank=True,
        help_text="Distance calculated onboard by ESP32 using Haversine formula"
    )
    esp32_is_inside = models.BooleanField(
        null=True,
        blank=True,
        help_text="Boundary state determined onboard by ESP32"
    )
    boundary_mismatch = models.BooleanField(
        default=False,
        help_text="True if ESP32 and Django geofence boundary calculations disagree"
    )

    # ESP32 Physical Output States
    led_red_state = models.BooleanField(default=False, help_text="Physical red LED state (GPIO26)")
    led_green_state = models.BooleanField(default=True, help_text="Physical green LED state (GPIO27)")
    buzzer_state = models.BooleanField(default=False, help_text="Physical buzzer driver state (GPIO25)")
    esp32_sos_active = models.BooleanField(default=False, help_text="Physical hardware SOS active latch")

    # Remote SOS Clear Protocol
    pending_sos_clear = models.BooleanField(
        default=False,
        help_text="Set to True when operator resolves SOS, instructing ESP32 to clear latch"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tracker_asset'
        ordering = ['device_id']

    def __str__(self):
        return f"{self.name} ({self.device_id}) - Mode: {self.current_mode}"


class GeofenceSettings(models.Model):
    """
    Defines the dynamic circular geofence boundary configuration for an asset.
    """
    asset = models.OneToOneField(
        Asset,
        on_delete=models.CASCADE,
        related_name='geofence_settings'
    )
    center_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    center_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_meters = models.FloatField(
        default=500.0,
        help_text="Geofence boundary radius in meters"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tracker_geofence_settings'

    def __str__(self):
        return f"Geofence for {self.asset.device_id}: ({self.center_latitude}, {self.center_longitude}) - R: {self.radius_meters}m"


class LocationRecord(models.Model):
    """
    Persistent time-series log of all location updates.
    Strictly distinguishes Real GPS from simulated records.
    """
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='location_records'
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    altitude = models.FloatField(null=True, blank=True, help_text="Altitude in meters")
    speed = models.FloatField(null=True, blank=True, help_text="Ground speed in km/h")
    satellites_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Satellites in view (0 for simulated or no-lock)"
    )

    source_type = models.CharField(
        max_length=25,
        choices=LocationSource.choices,
        db_index=True
    )

    distance_from_center_meters = models.FloatField(
        null=True,
        blank=True,
        help_text="Calculated Haversine distance from geofence center"
    )
    is_inside_geofence = models.BooleanField(null=True, blank=True)
    geofence_transition = models.CharField(
        max_length=20,
        choices=GeofenceTransition.choices,
        default=GeofenceTransition.NONE,
        db_index=True
    )

    timestamp = models.DateTimeField(db_index=True, help_text="Device/telemetry timestamp")
    created_at = models.DateTimeField(auto_now_add=True, help_text="Server ingestion timestamp")

    # ESP32 onboard reported data and physical GPIO state at time of record
    esp32_distance_meters = models.FloatField(null=True, blank=True)
    esp32_is_inside = models.BooleanField(null=True, blank=True)
    boundary_mismatch = models.BooleanField(default=False)
    led_red_state = models.BooleanField(null=True, blank=True)
    led_green_state = models.BooleanField(null=True, blank=True)
    buzzer_state = models.BooleanField(null=True, blank=True)
    sos_active = models.BooleanField(null=True, blank=True)
    config_version = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'tracker_location_record'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['asset', '-timestamp']),
            models.Index(fields=['source_type']),
            models.Index(fields=['geofence_transition']),
        ]

    def __str__(self):
        return f"[{self.source_type}] {self.asset.device_id} @ ({self.latitude}, {self.longitude}) - {self.timestamp}"


class SOSEvent(models.Model):
    """
    Emergency SOS event records triggered from the physical ESP32 button or dashboard.
    Enforces strict distinction between fresh GPS vs no satellite fix.
    """
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='sos_events'
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=SOSStatus.choices,
        default=SOSStatus.ACTIVE,
        db_index=True
    )
    trigger_source = models.CharField(
        max_length=30,
        choices=SOSTriggerSource.choices,
        default=SOSTriggerSource.HARDWARE_BUTTON
    )

    # Real GPS lock flag
    has_gps_fix = models.BooleanField(
        default=False,
        help_text="True only when valid, fresh coordinates were acquired at the moment of SOS"
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_source = models.CharField(
        max_length=25,
        choices=LocationSource.choices,
        null=True,
        blank=True
    )

    # Resolution lifecycle
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=100, null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'tracker_sos_event'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['asset', '-timestamp']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        fix_info = f"({self.latitude}, {self.longitude})" if self.has_gps_fix else "NO_GPS_FIX"
        return f"SOS [{self.status}] {self.asset.device_id} @ {self.timestamp} - {fix_info}"


class SoftwareLocationTarget(models.Model):
    """
    Stages operator-selected coordinates intended for retrieval and processing
    by the ESP32 tracking node over Wi-Fi.
    Enforces target acknowledgement and consumption tracking to guarantee no
    staged target is silently lost if network or upload failure occurs.
    Supports multiple pending targets safely with deterministic FIFO ordering.
    """
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='software_location_targets',
        help_text="Target hardware device/asset intended to receive these coordinates."
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text="Target latitude for software simulation."
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text="Target longitude for software simulation."
    )
    status = models.CharField(
        max_length=20,
        choices=TargetStatus.choices,
        default=TargetStatus.PENDING,
        db_index=True,
        help_text="Current lifecycle state of the software coordinate command."
    )
    sequence_number = models.PositiveIntegerField(
        default=1,
        help_text="Monotonically increasing sequence order for deterministic multi-target execution."
    )
    staged_by = models.CharField(
        max_length=100,
        default='Operator',
        help_text="Operator username or component that staged the coordinates."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when coordinates were staged."
    )
    dispatched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the ESP32 node retrieved this target."
    )
    dispatch_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this target was requested by/dispatched to the ESP32 (for retry resilience)."
    )
    consumed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the ESP32 successfully uploaded the resulting telemetry back to Django."
    )
    location_record = models.ForeignKey(
        'LocationRecord',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='originating_software_target',
        help_text="Reference to the LocationRecord ingested as a result of this target."
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Failure or retry diagnostics if an ingestion issue occurred."
    )

    class Meta:
        db_table = 'tracker_software_location_target'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['asset', 'status', 'created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['asset', 'sequence_number']),
        ]

    def __str__(self):
        return f"SoftwareTarget #{self.id} for {self.asset.device_id} [{self.status}] @ ({self.latitude}, {self.longitude})"

    def mark_dispatched(self):
        """Records that the target was retrieved by the ESP32."""
        self.status = TargetStatus.DISPATCHED
        self.dispatch_count += 1
        self.dispatched_at = timezone.now()
        self.save(update_fields=['status', 'dispatch_count', 'dispatched_at'])

    def mark_consumed(self, location_record=None):
        """Records that the ESP32 successfully ingested telemetry corresponding to this target."""
        self.status = TargetStatus.CONSUMED
        self.consumed_at = timezone.now()
        if location_record:
            self.location_record = location_record
            self.save(update_fields=['status', 'consumed_at', 'location_record'])
        else:
            self.save(update_fields=['status', 'consumed_at'])

    def mark_failed(self, error_msg):
        """Records a failure diagnostic for this target."""
        self.status = TargetStatus.FAILED
        self.error_message = error_msg
        self.save(update_fields=['status', 'error_message'])

