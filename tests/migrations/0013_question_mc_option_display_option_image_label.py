from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tests", "0012_examrun_published_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="mc_option_display",
            field=models.CharField(
                choices=[("TEXT", "Text options"), ("IMAGE", "Image options")],
                db_index=True,
                default="TEXT",
                help_text="For MULTIPLE_CHOICE: all options are text or all are images.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="questionoption",
            name="label",
            field=models.TextField(blank=True, default="", help_text="Optional LaTeX caption under image option."),
        ),
        migrations.AddField(
            model_name="questionoption",
            name="image",
            field=models.ImageField(blank=True, null=True, upload_to="question_option_images/%Y/%m/"),
        ),
        migrations.AlterField(
            model_name="questionoption",
            name="text",
            field=models.TextField(blank=True, default=""),
        ),
    ]
