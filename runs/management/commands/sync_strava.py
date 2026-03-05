"""
runs/management/commands/sync_strava.py

Django Management Command：同步 Strava 跑步数据到本地数据库。

用法：
    # 首次运行（需要完整 OAuth 授权流程）
    python manage.py sync_strava

    # 已有 access_token 时，直接传入跳过授权步骤
    python manage.py sync_strava --access-token <your_token>

    # 指定同步条数（默认 5）
    python manage.py sync_strava --count 10
"""

import os
import sys
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from runs.models import RunActivity

# ── Strava 常量 ──────────────────────────────────────
BASE_URL = "https://www.strava.com"
REDIRECT_URI = "http://localhost"


# ── 工具函数（与 strava_sync.py 保持一致，但错误使用 raise 而非 sys.exit）─────


def _build_auth_url(client_id: str) -> str:
    """构造 Strava OAuth 2.0 授权链接"""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
    }
    return f"{BASE_URL}/oauth/authorize?{urlencode(params)}"


def _extract_code(redirected_url: str) -> str:
    """从重定向 URL 中解析 authorization code"""
    parsed = urlparse(redirected_url)
    code_list = parse_qs(parsed.query).get("code")
    if not code_list:
        raise CommandError(
            "URL 中未找到 'code' 参数，请确认粘贴了完整的重定向 URL。"
        )
    return code_list[0]


def _exchange_token(client_id: str, client_secret: str, code: str) -> dict:
    """用 authorization code 换取 access_token"""
    try:
        resp = requests.post(
            f"{BASE_URL}/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise CommandError("网络连接失败，请检查你的网络或代理设置。")
    except requests.exceptions.Timeout:
        raise CommandError("请求超时，Strava 服务器响应过慢，请稍后重试。")
    except requests.exceptions.HTTPError:
        raise CommandError(
            f"换取令牌失败（HTTP {resp.status_code}）：{resp.text}"
        )

    token_data = resp.json()
    if "access_token" not in token_data:
        raise CommandError(f"授权失败，Strava 返回：{token_data}")
    return token_data


def _fetch_runs(access_token: str, count: int) -> list:
    """拉取最近 count 条跑步活动的原始 JSON 列表"""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": max(count * 3, 30), "page": 1},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise CommandError("获取活动数据时网络连接失败。")
    except requests.exceptions.Timeout:
        raise CommandError("获取活动数据超时，请稍后重试。")
    except requests.exceptions.HTTPError:
        raise CommandError(
            f"获取活动数据失败（HTTP {resp.status_code}）：{resp.text}"
        )

    all_activities = resp.json()
    runs = [a for a in all_activities if a.get("type") == "Run"]
    return runs[:count]


def _save_runs(runs: list, stdout) -> tuple[int, int]:
    """
    将活动列表写入数据库，返回 (created_count, updated_count)。
    以 strava_id 为唯一键，使用 update_or_create 幂等写入。
    """
    created_total = 0
    updated_total = 0

    for run in runs:
        strava_id = str(run["id"])
        date = parse_datetime(run.get("start_date_local", "")) or parse_datetime(
            run.get("start_date", "")
        )

        defaults = {
            "name": run.get("name", "未命名"),
            "date": date,
            "distance_km": round(run.get("distance", 0) / 1000, 3),
            "moving_time_min": round(run.get("moving_time", 0) / 60, 2),
            "average_heart_rate": run.get("average_heartrate"),
        }

        _, created = RunActivity.objects.update_or_create(
            strava_id=strava_id,
            defaults=defaults,
        )

        status_label = "新增" if created else "更新"
        stdout.write(
            f"  [{status_label}] {defaults['name']}  "
            f"{defaults['date'].strftime('%Y-%m-%d') if defaults['date'] else '?'}  "
            f"{defaults['distance_km']:.2f} km"
        )

        if created:
            created_total += 1
        else:
            updated_total += 1

    return created_total, updated_total


# ── Django Command 主体 ───────────────────────────────


class Command(BaseCommand):
    help = "从 Strava API 同步最近的跑步数据并存入数据库"

    def add_arguments(self, parser):
        parser.add_argument(
            "--access-token",
            type=str,
            default="",
            help="直接传入已有的 access_token，跳过 OAuth 授权交互步骤",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=5,
            help="要同步的跑步记录条数（默认 5）",
        )

    def handle(self, *args, **options):
        # ── 读取环境变量 ──────────────────────────────
        client_id = os.getenv("STRAVA_CLIENT_ID", "")
        client_secret = os.getenv("STRAVA_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            raise CommandError(
                "未找到 STRAVA_CLIENT_ID 或 STRAVA_CLIENT_SECRET，"
                "请在项目根目录的 .env 文件中配置。"
            )

        access_token: str = options["access_token"] or os.getenv(
            "STRAVA_ACCESS_TOKEN", ""
        )
        count: int = options["count"]

        # ── 若没有现成 Token，执行 OAuth 交互流程 ─────
        if not access_token:
            self.stdout.write("\n" + "=" * 55)
            self.stdout.write("  Strava 数据同步（Django Command）")
            self.stdout.write("=" * 55)
            self.stdout.write(
                "\n第一步：请在浏览器中打开以下链接，完成 Strava 授权：\n"
            )
            self.stdout.write(f"  {_build_auth_url(client_id)}\n")
            self.stdout.write(
                "授权成功后，将浏览器地址栏中以 http://localhost/... 开头的完整 URL 粘贴回来。\n"
            )

            redirected_url = input("> ").strip()
            if not redirected_url:
                raise CommandError("未输入 URL，已取消。")

            code = _extract_code(redirected_url)
            self.stdout.write(f"\n✅ 已提取授权码：{code[:8]}...（已截断）")

            self.stdout.write("正在换取访问令牌...")
            token_data = _exchange_token(client_id, client_secret, code)
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            athlete_name = token_data.get("athlete", {}).get("firstname", "用户")

            self.stdout.write(self.style.SUCCESS(f"✅ 授权成功！欢迎，{athlete_name}！"))
            self.stdout.write(
                "\n💡 将以下两行写入 .env，下次运行无需重复授权：\n"
                f"   STRAVA_ACCESS_TOKEN={access_token}\n"
                f"   STRAVA_REFRESH_TOKEN={refresh_token}\n"
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ 已使用现有 access_token（{access_token[:12]}...）"
                )
            )

        # ── 拉取数据 ──────────────────────────────────
        self.stdout.write(f"\n正在从 Strava 获取最近 {count} 条跑步记录...")
        runs = _fetch_runs(access_token, count)

        if not runs:
            self.stdout.write(self.style.WARNING("⚠️  未找到任何跑步记录（Run 类型）。"))
            return

        # ── 存库 ─────────────────────────────────────
        self.stdout.write(f"\n正在写入数据库（共 {len(runs)} 条）：\n")
        created, updated = _save_runs(runs, self.stdout)

        self.stdout.write("\n" + "=" * 55)
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ 同步完成！新增 {created} 条，更新 {updated} 条。"
            )
        )
        self.stdout.write("=" * 55 + "\n")
