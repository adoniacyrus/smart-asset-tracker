from django.contrib import admin
from .models import Asset, GeofenceSettings, LocationRecord, SOSEvent


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('device_id', 'name', 'current_mode', 'is_inside_geofence', 'is_active', 'last_known_timestamp')
    list_filter = ('current_mode', 'is_active', 'is_inside_geofence')
    search_fields = ('device_id', 'name')


@admin.register(GeofenceSettings)
class GeofenceSettingsAdmin(admin.ModelAdmin):
    list_display = ('asset', 'center_latitude', 'center_longitude', 'radius_meters', 'is_active')
    list_filter = ('is_active',)


@admin.register(LocationRecord)
class LocationRecordAdmin(admin.ModelAdmin):
    list_display = ('asset', 'source_type', 'latitude', 'longitude', 'distance_from_center_meters', 'geofence_transition', 'timestamp')
    list_filter = ('source_type', 'geofence_transition')
    search_fields = ('asset__device_id',)


@admin.register(SOSEvent)
class SOSEventAdmin(admin.ModelAdmin):
    list_display = ('asset', 'status', 'trigger_source', 'has_gps_fix', 'latitude', 'longitude', 'timestamp')
    list_filter = ('status', 'trigger_source', 'has_gps_fix')
    search_fields = ('asset__device_id',)
