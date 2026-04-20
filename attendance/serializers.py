from rest_framework import serializers
from attendance.models import AttendanceRecord, Course, Student, Tenant

class BulkMarkSerializer(serializers.Serializer):
    course_id = serializers.UUIDField()
    date = serializers.DateField()
    period = serializers.IntegerField()
    records = serializers.ListField(
        child=serializers.DictField(),
        max_length=120
    )

    def validate(self, attrs):
        # Validate that the course belongs to the user's tenant
        # In a real scenario, request.user.tenant would be used.
        # For now, we assume the tenant is passed or inferred.

        # Validate records format
        for record in attrs['records']:
            if 'student_id' not in record or 'status' not in record:
                raise serializers.ValidationError("Each record must contain student_id and status")

        return attrs
