"""
strava_sync.py
Strava OAuth 2.0 授权 + 跑步数据抓取脚本

运行前请确保：
1. 已安装依赖：pip install -r requirements.txt
2. 已在同级目录创建 .env 文件，填入 STRAVA_CLIENT_ID 和 STRAVA_CLIENT_SECRET
"""

import os
import sys
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# 1. 加载环境变量
# ──────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ 错误：未找到 STRAVA_CLIENT_ID 或 STRAVA_CLIENT_SECRET。")
    print("   请在脚本同级目录下创建 .env 文件，内容格式如下：")
    print("   STRAVA_CLIENT_ID=your_client_id")
    print("   STRAVA_CLIENT_SECRET=your_client_secret")
    sys.exit(1)

# Strava API 基础地址
BASE_URL = "https://www.strava.com"

# OAuth 回调地址（使用 Strava 开发者默认的本地测试地址）
REDIRECT_URI = "http://localhost"


# ──────────────────────────────────────────────
# 2. 生成授权 URL，引导用户在浏览器中完成授权
# ──────────────────────────────────────────────
def build_auth_url() -> str:
    """构造 Strava OAuth 2.0 授权链接"""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        # activity:read_all 权限可读取私密活动；如无需私密数据可改为 activity:read
        "scope": "activity:read_all",
        "approval_prompt": "auto",
    }
    return f"{BASE_URL}/oauth/authorize?{urlencode(params)}"


# ──────────────────────────────────────────────
# 3. 从用户粘贴的重定向 URL 中提取 authorization code
# ──────────────────────────────────────────────
def extract_code_from_redirect(redirected_url: str) -> str:
    """
    解析用户粘贴的完整重定向 URL，提取其中的 code 参数。
    示例 URL: http://localhost/?state=&code=xxxxxx&scope=...
    """
    try:
        parsed = urlparse(redirected_url)
        query_params = parse_qs(parsed.query)
        code_list = query_params.get("code")
        if not code_list:
            raise ValueError("URL 中未找到 'code' 参数，请确认粘贴了完整的重定向 URL。")
        return code_list[0]
    except Exception as e:
        print(f"❌ 解析重定向 URL 失败：{e}")
        sys.exit(1)


# ──────────────────────────────────────────────
# 4. 用 code 换取 access_token 和 refresh_token
# ──────────────────────────────────────────────
def exchange_token(code: str) -> dict:
    """
    向 Strava Token 端点发送 POST 请求，换取访问令牌。
    返回包含 access_token、refresh_token、expires_at 等字段的字典。
    """
    token_url = f"{BASE_URL}/oauth/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }

    try:
        response = requests.post(token_url, data=payload, timeout=15)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("❌ 网络连接失败，请检查你的网络或代理设置。")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("❌ 请求超时，Strava 服务器响应过慢，请稍后重试。")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ 换取令牌失败（HTTP {response.status_code}）：{response.text}")
        sys.exit(1)

    token_data = response.json()

    # 检查是否包含必要的 token 字段
    if "access_token" not in token_data:
        print(f"❌ 授权失败，Strava 返回：{token_data}")
        sys.exit(1)

    return token_data


# ──────────────────────────────────────────────
# 5. 获取最近 5 条跑步活动数据
# ──────────────────────────────────────────────
def fetch_recent_runs(access_token: str, count: int = 5) -> list:
    """
    调用 /athlete/activities 接口，拉取最近若干条活动。
    通过 per_page 参数控制拉取数量，再过滤出类型为 Run 的记录。
    """
    activities_url = f"{BASE_URL}/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    # 多拉一些以确保过滤后能凑够 count 条跑步记录
    params = {"per_page": 30, "page": 1}

    try:
        response = requests.get(activities_url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("❌ 获取活动数据时网络连接失败。")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("❌ 获取活动数据超时，请稍后重试。")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ 获取活动数据失败（HTTP {response.status_code}）：{response.text}")
        sys.exit(1)

    all_activities = response.json()

    # 仅保留跑步类型（Run），并截取前 count 条
    runs = [a for a in all_activities if a.get("type") == "Run"]
    return runs[:count]


# ──────────────────────────────────────────────
# 6. 格式化并打印跑步数据
# ──────────────────────────────────────────────
def print_run_summary(runs: list) -> None:
    """将原始活动列表格式化为可读的文本并打印到终端"""
    if not runs:
        print("⚠️  未找到任何跑步记录（Run 类型）。")
        return

    print("\n" + "=" * 55)
    print(f"  🏃 最近 {len(runs)} 条跑步记录")
    print("=" * 55)

    for idx, run in enumerate(runs, start=1):
        name = run.get("name", "未命名")
        # 日期格式：2024-05-01T07:30:00Z → 取前 10 位
        date = run.get("start_date_local", "")[:10]
        # 距离：单位为米，转换为公里，保留两位小数
        distance_km = run.get("distance", 0) / 1000
        # 移动时间：单位为秒，转换为分钟，保留一位小数
        moving_time_min = run.get("moving_time", 0) / 60
        # 平均心率：可能为 None（若未佩戴心率设备）
        avg_hr = run.get("average_heartrate")
        avg_hr_str = f"{avg_hr:.0f} bpm" if avg_hr else "无数据"

        print(f"\n  [{idx}] {name}")
        print(f"      📅 日期      : {date}")
        print(f"      📏 距离      : {distance_km:.2f} km")
        print(f"      ⏱️  移动时间  : {moving_time_min:.1f} 分钟")
        print(f"      💓 平均心率  : {avg_hr_str}")

    print("\n" + "=" * 55)


# ──────────────────────────────────────────────
# 主流程入口
# ──────────────────────────────────────────────
def main():
    # Step 1：生成并打印授权 URL
    auth_url = build_auth_url()
    print("\n" + "=" * 55)
    print("  Strava 数据同步脚本")
    print("=" * 55)
    print("\n第一步：请在浏览器中打开以下链接，完成 Strava 授权：\n")
    print(f"  {auth_url}\n")
    print("授权成功后，浏览器会跳转到一个以 http://localhost/... 开头")
    print("的页面（页面可能显示无法访问，这是正常现象）。\n")

    # Step 2：引导用户粘贴重定向 URL
    redirected_url = input("第二步：请将浏览器地址栏中的完整 URL 粘贴到这里，然后按回车：\n> ").strip()

    if not redirected_url:
        print("❌ 未输入 URL，程序退出。")
        sys.exit(1)

    # Step 3：提取 code
    code = extract_code_from_redirect(redirected_url)
    print(f"\n✅ 已提取授权码：{code[:8]}... （已截断显示）")

    # Step 4：换取 Token
    print("\n正在向 Strava 换取访问令牌...")
    token_data = exchange_token(code)
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    athlete_name = token_data.get("athlete", {}).get("firstname", "用户")

    print(f"✅ 授权成功！欢迎，{athlete_name}！")
    print(f"   access_token  : {access_token[:12]}...（已截断）")
    print(f"   refresh_token : {refresh_token[:12]}...（已截断）")
    print("\n💡 提示：将以下 Token 保存到 .env 文件中，下次可直接使用，无需重复授权：")
    print(f"   STRAVA_ACCESS_TOKEN={access_token}")
    print(f"   STRAVA_REFRESH_TOKEN={refresh_token}")

    # Step 5：获取并打印跑步数据
    print("\n正在获取最近 5 条跑步记录...")
    runs = fetch_recent_runs(access_token, count=5)
    print_run_summary(runs)


if __name__ == "__main__":
    main()
