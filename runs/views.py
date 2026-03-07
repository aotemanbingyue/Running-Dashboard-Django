import json
import os
from io import StringIO

from django.contrib import messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.shortcuts import redirect, render

from .models import RunActivity


def dashboard_view(request):
    """
    看板主页视图。
    取最近 30 条跑步记录（按日期升序），将日期和距离序列化为 JSON
    字符串传入模板，供 ECharts 直接使用。
    """
    # 按日期升序取最近 30 条，让折线图从左到右表示时间流逝
    recent_runs = RunActivity.objects.order_by("-date")[:30]
    recent_runs = list(reversed(recent_runs))  # 反转为升序

    # X 轴：显示 "MM-DD" 格式，保持简洁
    dates = [run.date.strftime("%m-%d") for run in recent_runs]
    # Y 轴：距离，保留两位小数
    distances = [round(run.distance_km, 2) for run in recent_runs]
    # 额外数据：移动时间（分钟），用于 Tooltip 展示
    durations = [round(run.moving_time_min, 1) for run in recent_runs]
    # 额外数据：平均心率
    heart_rates = [run.average_heart_rate or 0 for run in recent_runs]

    # 汇总统计卡片数据
    total_runs = RunActivity.objects.count()
    total_km = sum(distances) if distances else 0
    avg_distance = round(total_km / len(distances), 2) if distances else 0
    best_distance = max(distances) if distances else 0

    context = {
        "runs": recent_runs,
        "total_runs": total_runs,
        "total_km": round(total_km, 2),
        "avg_distance": avg_distance,
        "best_distance": best_distance,
        # 序列化为 JSON 字符串，在模板中直接注入 JS 变量
        "dates_json": json.dumps(dates, ensure_ascii=False),
        "distances_json": json.dumps(distances),
        "durations_json": json.dumps(durations),
        "heart_rates_json": json.dumps(heart_rates),
    }
    return render(request, "runs/dashboard.html", context)


def sync_strava_view(request):
    """
    看板页触发的「更新数据」接口。
    仅接受 POST，使用 .env 中的 STRAVA_ACCESS_TOKEN 调用 sync_strava 逻辑，
    同步最近 30 条跑步数据后重定向回看板并展示结果消息。
    """
    if request.method != "POST":
        return redirect("runs:dashboard")

    token = os.getenv("STRAVA_ACCESS_TOKEN", "").strip()
    if not token:
        messages.warning(
            request,
            "尚未配置 STRAVA_ACCESS_TOKEN，无法在网页端同步。请先在终端运行 "
            "python manage.py sync_strava 完成授权，并将返回的 Token 写入 .env 后再使用「更新数据」按钮。",
        )
        return redirect("runs:dashboard")

    out = StringIO()
    err = StringIO()
    try:
        call_command(
            "sync_strava",
            "--access-token",
            token,
            "--count",
            "30",
            stdout=out,
            stderr=err,
        )
        summary = (out.getvalue() + " " + err.getvalue()).strip()
        if "同步完成" in summary or "新增" in summary or "更新" in summary:
            messages.success(request, "Strava 数据已更新，看板已刷新。")
        else:
            messages.success(request, "同步已执行完成。")
    except CommandError as e:
        messages.error(request, f"同步失败：{str(e)}")
    except Exception as e:
        messages.error(
            request,
            f"同步失败：{str(e)}。请检查网络或 .env 中的 Token 是否过期，必要时重新运行 python manage.py sync_strava 获取新 Token。",
        )

    return redirect("runs:dashboard")
