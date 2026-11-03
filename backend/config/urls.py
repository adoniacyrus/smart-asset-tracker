"""
URL configuration for Smart Asset Location Tracker.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from tracker import views as tracker_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/tracker/', include('tracker.urls')),
    path('', tracker_views.dashboard_view, name='dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')
