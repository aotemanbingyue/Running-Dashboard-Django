from django.db import models


class RunActivity(models.Model):
    """
    存储从 Strava 同步的单次跑步活动数据。
    以 strava_id 作为业务唯一键，防止重复写入同一条活动。
    """

    strava_id = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="Strava 活动 ID",
        help_text="来自 Strava API 的活动唯一标识符",
    )
    name = models.CharField(
        max_length=255,
        verbose_name="活动名称",
    )
    date = models.DateTimeField(
        verbose_name="运动日期",
    )
    distance_km = models.FloatField(
        verbose_name="距离（公里）",
    )
    moving_time_min = models.FloatField(
        verbose_name="移动时间（分钟）",
    )
    average_heart_rate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="平均心率（bpm）",
        help_text="未佩戴心率设备时为空",
    )
    # 记录数据写入时间，方便排查同步问题
    synced_at = models.DateTimeField(auto_now=True, verbose_name="最后同步时间")

    class Meta:
        verbose_name = "跑步活动"
        verbose_name_plural = "跑步活动"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"[{self.date.strftime('%Y-%m-%d')}] {self.name} ({self.distance_km:.2f} km)"
