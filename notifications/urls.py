from django.urls import path
from .views import MarkNotificationReadView

urlpatterns = [
    path('mark-read/<<uuiduuid:notification_id>/', MarkNotificationReadView.as_view(), name='mark-read'),
]
