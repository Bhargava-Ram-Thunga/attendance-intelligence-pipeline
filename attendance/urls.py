from django.urls import path
from .views import BulkMarkView

urlpatterns = [
    path('bulk-mark/', BulkMarkView.as_view(), name='bulk-mark'),
]
