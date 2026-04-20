from django.db import models

class Tenant(models.Model):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Tenants"

    def __str__(self):
        return self.name

class TenantModel(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        db_index=False, # Composite indexes handle the lookups
        related_name="%(class)s_set"
    )

    class Meta:
        abstract = True

class Program(TenantModel):
    name = models.CharField(max_length=255)

    class Meta:
        indexes = [
            # Query: List programs for a specific tenant by name
            models.Index(fields=['tenant', 'name'], name='idx_program_tenant_name'),
        ]

class Course(TenantModel):
    name = models.CharField(max_length=255)
    program = models.ForeignKey(Program, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            # Query: List courses for a tenant within a specific program
            models.Index(fields=['tenant', 'program', 'name'], name='idx_course_tenant_prog_name'),
        ]

class Student(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField() # Representing User system link
    program = models.ForeignKey(Program, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            # Query: List all students for a tenant in a program
            models.Index(fields=['tenant', 'program'], name='idx_student_tenant_prog'),
        ]
        unique_together = ('tenant', 'user_id')

class Parent(TenantModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="parents")
    user_id = models.UUIDField()

    class Meta:
        indexes = [
            # Query: Find parent of a specific student within a tenant
            models.Index(fields=['tenant', 'student'], name='idx_parent_tenant_std'),
        ]

class Counselor(TenantModel):
    user_id = models.UUIDField()

    class Meta:
        indexes = [
            # Query: Find counselor by user_id within a tenant
            models.Index(fields=['tenant', 'user_id'], name='idx_counselor_tenant_user'),
        ]

class StudentCounselorAssignment(TenantModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    counselor = models.ForeignKey(Counselor, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('tenant', 'student', 'counselor', 'course')
        indexes = [
            # Query: Find all counselors for a student in a specific course
            models.Index(fields=['tenant', 'student', 'course'], name='idx_sca_tenant_std_crs'),
        ]

class AttendanceRecord(TenantModel):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LATE = "LATE", "Late"

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    date = models.DateField()
    period = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices)

    class Meta:
        unique_together = ('tenant', 'student', 'course', 'date', 'period')
        indexes = [
            # Query: Faculty bulk-mark lookup for students on a specific date
            models.Index(fields=['tenant', 'student', 'date'], name='idx_att_rec_tenant_std_date'),
            # Query: Aggregate attendance for a student in a specific course
            models.Index(fields=['tenant', 'student', 'course'], name='idx_att_rec_tenant_std_crs'),
        ]

class AttendancePercentage(TenantModel):
    class RiskStatus(models.TextChoices):
        SAFE = "SAFE", "Safe"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    risk_status = models.CharField(max_length=10, choices=RiskStatus.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'student', 'course')
        indexes = [
            # Query: Target for select_for_update during recomputation
            models.Index(fields=['tenant', 'student', 'course'], name='idx_att_pct_tenant_std_crs'),
        ]
