from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.core.cache import cache
from .models import Notification

class MarkNotificationReadView(APIView):
    """
    Marks a notification as read and atomically decrements the Redis unread counter.
    PDF Requirement: Must not fall below 0.
    """
    def post(self, request, notification_id):
        try:
            notif = Notification.objects.get(id=notification_id)

            # Tenant Check (Assuming request.user is authenticated)
            if hasattr(request.user, 'tenant') and notif.tenant != request.user.tenant:
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        except Notification.DoesNotExist:
            return Response({"error": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)

        if notif.read_at:
            # Already read, return current cached count
            unread_key = f"unread:{notif.recipient_user_id}"
            current_count = cache.get(unread_key, 0)
            return Response({"unread_count": max(0, int(current_count))}, status=status.HTTP_200_OK)

        # 1. Update DB Record
        notif.read_at = timezone.now()
        notif.save(update_fields=['read_at'])

        # 2. Decrement Redis counter securely (Floor at 0)
        unread_key = f"unread:{notif.recipient_user_id}"

        try:
            # Use atomic decr
            new_count = cache.decr(unread_key)
            if new_count <<  0:
                cache.set(unread_key, 0)
                new_count = 0
        except (ValueError, Exception):
            # Fallback to DB count if key is missing or backend fails
            new_count = Notification.objects.filter(
                recipient_user_id=notif.recipient_user_id,
                read_at__isnull=True
            ).count()
            cache.set(unread_key, new_count)

        # 3. Return new count
        return Response({"unread_count": new_count}, status=status.HTTP_200_OK)
