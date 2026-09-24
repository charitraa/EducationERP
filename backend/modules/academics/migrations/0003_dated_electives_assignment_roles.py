from django.db import migrations, models
from django.db.models import F, Q


def fill_started_on(apps, schema_editor):
    """Existing choices started when their enrollment did."""
    StudentElective = apps.get_model("academics", "StudentElective")
    for choice in StudentElective.objects.select_related("enrollment").iterator():
        choice.started_on = choice.enrollment.started_on
        choice.save(update_fields=["started_on"])


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0002_teachingassignment_periods_per_week_studentelective"),
        ("students", "0002_enrollment_section_alter_enrollment_status"),
    ]

    operations = [
        # Electives: dated, so dropping a subject keeps its history.
        migrations.AddField(
            model_name="studentelective",
            name="started_on",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="studentelective",
            name="ended_on",
            field=models.DateField(blank=True, null=True, help_text="Set when the subject is dropped."),
        ),
        migrations.RunPython(fill_started_on, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="studentelective",
            name="started_on",
            field=models.DateField(),
        ),
        migrations.RemoveConstraint(model_name="studentelective", name="uniq_student_elective"),
        migrations.AlterModelOptions(
            name="studentelective",
            options={"ordering": ["enrollment", "subject__name", "started_on"]},
        ),
        migrations.AddConstraint(
            model_name="studentelective",
            constraint=models.UniqueConstraint(
                condition=Q(ended_on__isnull=True), fields=("enrollment", "subject"),
                name="uniq_open_student_elective",
            ),
        ),
        migrations.AddConstraint(
            model_name="studentelective",
            constraint=models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gt=F("started_on")),
                name="student_elective_dates_ordered",
            ),
        ),
        # Teaching assignments: several teachers per subject, and retired ones.
        migrations.AddField(
            model_name="teachingassignment",
            name="role",
            field=models.CharField(
                choices=[("lecture", "Lecture / theory"), ("practical", "Practical / lab"),
                         ("tutorial", "Tutorial"), ("co_teaching", "Co-teaching")],
                default="lecture", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="teachingassignment",
            name="is_active",
            field=models.BooleanField(
                db_index=True, default=True,
                help_text="False once the teacher no longer teaches it (e.g. after a hand-over). "
                          "Kept as a record; can't get new lessons.",
            ),
        ),
        migrations.RemoveConstraint(model_name="teachingassignment", name="uniq_teaching_assignment"),
        migrations.AddConstraint(
            model_name="teachingassignment",
            constraint=models.UniqueConstraint(
                fields=("section", "subject", "teacher", "role"), name="uniq_teaching_assignment_role"
            ),
        ),
    ]
