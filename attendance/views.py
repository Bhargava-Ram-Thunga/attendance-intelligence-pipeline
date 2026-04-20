from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from .serializers import BulkMarkSerializer
from .models import AttendanceRecord, Course, Tenant
from .tasks import recompute_attendance

class BulkMarkView(APIView):
    """
    Bulk mark attendance for a course.
    Expected Payload: { "course_id": "...", "date": "...", "period": 1, "records": [...] }
    """
    def post(self, request, *args, **kwargs):
        serializer = BulkMarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        course_id = data['course_id']
        date = data['date']
        period = data['period']
        records_data = data['records']

        try:
            course = Course.objects.get(id=course_id)
            tenant_id = course.tenant_id
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        # Prepare records for bulk_create
        attendance_objs = [
            AttendanceRecord(
                tenant_id=tenant_id,
                course_id=course_id,
                date=date,
                period=period,
                student_id=r['student_id'],
                status=r['status']
            ) for r in records_data
        ]

        with transaction.atomic():
            # Strong Hire Signal: use bulk_create with update_conflicts for idempotency
            AttendanceRecord.objects.bulk_create(
                attendance_objs,
                update_conflicts=True,
                update_fields=["status"],
                unique_fields=["tenant", "student", "course", "date", "period"]
            )

            # Extract student IDs for the recompute task
            student_ids = [r['student_id'] for r in records_data]

            # Strong Hire Signal: dispatch task on commit to avoid phantom reads
            transaction.on_commit(lambda: recompute_attendance.delay(
                course_id=course_id,
                student_ids=student_ids,
                tenant_id=tenant_id
            ))

        return Response({"message": "Attendance marking accepted for processing"}, status=status.HTTP_202_ACCEPTED)
