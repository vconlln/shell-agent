"""内网 W3 登录：只管三件事 —— 打开登录页、判断该不该自动打开、以及在界面上如实说明。

**为什么要单独一个模块**：这个功能横跨三处（设置页的几行控件、控制器的"开始运行/发对话"
两个入口、以及报错时要补的那句话），但真正的逻辑只有"打开登录页 + 该不该打开 + 该说什么"
这三件。放在一个模块里，三处调用同一份判断，才不会出现"设置页说已登录、运行时不认"这种漂移。

**它不碰网络请求**。凭据怎么随请求发出去是传输层的事（`agent_backends/api_client.py`），
本模块只负责从设置里把用户填的东西读出来、并且**不判断网关到底认哪种方式** ——
我们不知道内网网关是按登录态放行还是认 cookie，所以这里的立场是：
把两条路都留好，并把"当前会发送什么"如实写在界面上，让用户一试就知道缺哪一环。

**凭据是敏感的**：它和 API key 一样存在设置文件里（这是现状，本模块不改变），
值不走日志、不进运行目录的 `meta.json`（有专门的用例守着这一条）。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

# 凭据默认放在这个请求头里：W3 这类网关最常见的就是 SSO cookie
DEFAULT_CREDENTIAL_HEADER = "Cookie"

# 用户还没填登录地址时说的话（他没有地址、要到内网才知道，所以这句话必须告诉他去哪填）
MISSING_URL_HINT = (
    "还没填 W3 登录地址：到内网打开登录页后，把那个地址填进"
    "「设置 → 后端 agent → W3 登录地址」，再点「打开浏览器登录 W3」。"
)

# 勾上选项、但没有凭据时的那句说明（不吓人，也不假装能自动搞定）
NO_CREDENTIAL_NOTE = (
    "未设置凭据：如果内网网关是按登录态放行的，浏览器登录完就能用；"
    "如果它认 cookie，登录后把浏览器里的 Cookie 粘到「登录凭据」那一格。"
)


def normalize_login_url(text: str) -> str:
    """登录地址：只接受 http/https，其余（含空串）返回空串。

    为什么必须挑协议：这个值会直接交给 `QDesktopServices.openUrl()` 去拉起浏览器。
    让 `file://`、`javascript:` 这类东西从设置里跑到"打开"这一步没有任何好处，
    而 `http/https` 正是"登录页"的唯一可能形态。
    """
    value = str(text or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return ""
    return value


def open_login_page(url: str) -> bool:
    """用系统浏览器打开登录页；地址为空/非法返回 False（**不猜、不乱开页面**）。

    返回布尔值而不是 None：调用方（设置页的按钮、控制器的自动打开）都要据此决定
    是"已打开浏览器"还是"先把地址填上"，两句话不一样。
    """
    target = normalize_login_url(url)
    if not target:
        return False
    return bool(QDesktopServices.openUrl(QUrl(target)))


def credential_header(settings: Any) -> str:
    """凭据的请求头名；留空用默认值（用户只需要改"不是 Cookie"的情况）。"""
    name = str(getattr(settings, "w3_credential_header", "") or "").strip()
    return name or DEFAULT_CREDENTIAL_HEADER


def credential_value(settings: Any) -> str:
    """凭据的值（用户从浏览器复制的 Cookie / token）；没填返回空串。"""
    return str(getattr(settings, "w3_credential", "") or "").strip()


def login_enabled(settings: Any) -> bool:
    """是否启用了"内网 W3 登录"这一套（选项本身）。"""
    return bool(getattr(settings, "w3_login_enabled", False))


def extra_headers(settings: Any) -> dict[str, str]:
    """要随内置 agent 的请求一起发出去的额外头。

    **两个条件都满足才发**：选项勾上 + 凭据非空。只要凭据非空就发是危险的 ——
    用户把凭据粘进去、后来把后端换回公网 API，那个 cookie 就会被送到第三方站点上去。
    所以"发不发"由这一个开关统一决定，界面上的状态行也照着它说。
    """
    if not login_enabled(settings):
        return {}
    value = credential_value(settings)
    if not value:
        return {}
    return {credential_header(settings): value}


def auth_hint(settings: Any) -> str:
    """401/403 时补在报错后面的那句话（只在启用了这个功能时给，免得干扰公网用户）。

    内网网关拒绝请求时最常见的原因就是"没登录"或"没带凭据"，而这两件事的解法完全不同。
    这句话同时给出两条路，用户按提示走一遍就知道自己属于哪一种。
    """
    if not login_enabled(settings):
        return ""
    parts = ["内网 W3 认证：如果网关要求先登录，点「设置 → 后端 agent → 打开浏览器登录 W3」"]
    if credential_value(settings):
        parts.append(
            f"已经会随请求发送 {credential_header(settings)}："
            "若仍被拒绝，说明它过期了，重新登录后换一份。"
        )
    else:
        parts.append("若网关还要求凭据，把浏览器里的 Cookie 粘到「登录凭据」那一格。")
    return "；".join(parts) + "。"


def should_auto_open(settings: Any, already_opened: bool) -> bool:
    """该不该**自动**打开浏览器：勾了选项、有地址、且这次启动还没自动开过。

    "每次启动只自动打开一次"是刻意的：用户点一次「开始」就弹一次浏览器，用两次就会
    把这个功能关掉。登录状态在一次会话里不会自己失效，需要再来一次时设置页有手动按钮。
    """
    if already_opened or not login_enabled(settings):
        return False
    return bool(normalize_login_url(str(getattr(settings, "w3_login_url", "") or "")))


def status_text(settings: Any) -> str:
    """设置页那行状态：把"现在会发生什么"如实写出来（不写"已登录"，我们并不知道）。"""
    if not login_enabled(settings):
        return "未启用：内置 agent 不会打开登录页，也不会随请求发送任何凭据。"
    url = normalize_login_url(str(getattr(settings, "w3_login_url", "") or ""))
    if not url:
        return "已启用，但" + MISSING_URL_HINT
    value = credential_value(settings)
    if not value:
        return f"登录页：{url}。" + NO_CREDENTIAL_NOTE
    return (
        f"登录页：{url}。每次内置 agent 请求都会带上 "
        f"{credential_header(settings)}：…（已设置，值不回显）。"
    )
