"""内网 W3 登录：打开登录页、凭据随请求发送、以及"不许说假话"。

用户原话：**"我的内置 agent 是在内网使用，需要登录华为的 W3 账号，你需要增加一个使用内置
agent 的时候调用浏览器登陆 W3 账号的选项"**；被问到"登录后程序要拿什么才能调通"时答
**"我不确定，你先按最稳的做"**。所以他那边有两种可能：网关按登录态放行（登录完就通），
或者网关认某个 cookie（必须随请求带上）。这份用例把**两条路**都钉住：

1. 打开登录页这件事本身（勾选项、按钮、没填地址时绝不开一个猜出来的页面）；
2. 凭据真的出现在请求头里（用 `httpx.MockTransport` 看发出去的请求），以及**没启用时
   一个字节都不许带**（否则用户把后端换回公网 API 时，内网 cookie 会被送到第三方站点）；
3. 界面上说的话与真实行为一致（状态行、401/403 的补充提示、以及"这次启动只自动开一次"）。

关于"没地址"：用户现在**没有**登录地址（要进内网才知道），所以"地址留空"是这一版的
**正常状态**，不是异常 —— 那种情况下要做的是把话说明白，不是乱开浏览器。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tu_shell_agent.agent_backends.api_client import ModelApiClient, sanitize_headers
from tu_shell_agent.ui import w3_login
from tu_shell_agent.ui.settings import AppSettings


@pytest.fixture(scope="module", autouse=True)
def _themed(qapp):
    """需要 QApplication（打开浏览器那条路走 Qt）；主题不必装，这里不测外观。"""
    yield


def _settings(**kwargs) -> SimpleNamespace:
    """与 AppSettings 同名的四个字段（w3_login 只按属性名取值，不要求真是 AppSettings）。"""
    base = dict(
        w3_login_enabled=False,
        w3_login_url="",
        w3_credential_header="",
        w3_credential="",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── 地址：只认 http/https，空/非法一律不开 ────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://w3.example.com/", "https://w3.example.com/"),
        ("  http://w3.example.com/login  ", "http://w3.example.com/login"),
        ("", ""),
        ("   ", ""),
        # 这些都不该被交给"打开"那一步：登录页只可能是 http/https
        ("w3.example.com", ""),
        ("file:///etc/passwd", ""),
        ("javascript:alert(1)", ""),
    ],
)
def test_login_url_only_accepts_http_and_https(text, expected):
    assert w3_login.normalize_login_url(text) == expected


def test_open_login_page_refuses_empty_and_invalid(monkeypatch):
    """没地址/地址非法时**不许**调用系统的打开动作（也不许猜一个地址）。"""
    opened: list[str] = []
    monkeypatch.setattr(
        w3_login.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )

    assert w3_login.open_login_page("") is False
    assert w3_login.open_login_page("file:///etc/passwd") is False
    assert opened == []


def test_open_login_page_opens_the_configured_url(monkeypatch):
    """有合法地址就交给系统浏览器（这正是用户要的"调用浏览器登录"）。"""
    opened: list[str] = []
    monkeypatch.setattr(
        w3_login.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True
    )

    assert w3_login.open_login_page("https://w3.example.com/") is True
    assert opened == ["https://w3.example.com/"]


# ── 该不该自动打开 ────────────────────────────────────────────────────────


def test_auto_open_requires_option_and_url():
    assert w3_login.should_auto_open(_settings(), already_opened=False) is False
    assert (
        w3_login.should_auto_open(
            _settings(w3_login_enabled=True), already_opened=False
        )
        is False
    ), "没填地址时不该自动打开（会开出一个猜出来的页面）"
    assert (
        w3_login.should_auto_open(
            _settings(w3_login_enabled=True, w3_login_url="https://w3.example.com/"),
            already_opened=False,
        )
        is True
    )


def test_auto_open_happens_only_once_per_session():
    """每次启动只自动打开一次：点一次「开始」弹一次浏览器的话，用户会直接关掉这个功能。"""
    settings = _settings(w3_login_enabled=True, w3_login_url="https://w3.example.com/")

    assert w3_login.should_auto_open(settings, already_opened=True) is False


# ── 凭据：只有勾选项打开时才随请求发送 ────────────────────────────────────


def test_no_credential_header_when_disabled():
    """**关键安全行为**：选项没勾就不发 —— 即使设置里存着凭据。

    不然用户在内网粘了 cookie、之后把后端换回公网 API，那个 cookie 会被送到第三方站点上。
    """
    settings = _settings(w3_credential="W3SSO=secret")

    assert w3_login.extra_headers(settings) == {}


def test_no_credential_header_when_value_is_empty():
    settings = _settings(w3_login_enabled=True, w3_credential="   ")

    assert w3_login.extra_headers(settings) == {}


def test_credential_header_defaults_to_cookie_and_honours_a_custom_name():
    default = _settings(w3_login_enabled=True, w3_credential="W3SSO=abc")
    custom = _settings(
        w3_login_enabled=True, w3_credential="abc", w3_credential_header="X-Auth-Token"
    )

    assert w3_login.extra_headers(default) == {"Cookie": "W3SSO=abc"}
    assert w3_login.extra_headers(custom) == {"X-Auth-Token": "abc"}


def test_header_injection_is_dropped():
    """头值里夹着换行就是**请求头注入**，必须丢掉（用户是手粘文本，什么都可能粘进来）。

    两头的空白是另一回事：那是复制粘贴常见的副产品，去掉就好（不是攻击，也不影响语义）。
    """
    assert sanitize_headers({"Cookie": "a=b\r\nX-Evil: 1"}) == {}
    assert sanitize_headers({"Cookie": "a=b\nX-Evil: 1"}) == {}
    # 头名非法（手滑打成 "Cookie x"）同样丢掉；空名/空值也丢掉
    assert sanitize_headers({"Cookie x": "v"}) == {}
    assert sanitize_headers({"": "v"}) == {}
    assert sanitize_headers({"Cookie": "   "}) == {}
    # 正常的留下，两头空白顺手去掉
    assert sanitize_headers({"Cookie": "  a=b  "}) == {"Cookie": "a=b"}


# ── 凭据真的到了请求上（MockTransport 看实际发出的请求） ──────────────────


def _client_capturing(seen: list[httpx.Request], **kwargs) -> ModelApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(200, text="data: [DONE]\n\n")

    return ModelApiClient(
        base_url="https://api.example.com/v1",
        api_key="k",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_credential_is_sent_on_completion_requests():
    """启用后，凭据必须出现在**真正发出去**的请求头里（不是只存在设置对象里）。"""
    seen: list[httpx.Request] = []
    client = _client_capturing(
        seen, extra_headers=w3_login.extra_headers(_settings(
            w3_login_enabled=True, w3_credential="W3SSO=abc"
        ))
    )
    try:
        client.stream_chat(model="m", messages=[{"role": "user", "content": "hi"}])
    finally:
        client.close()

    assert seen, "没有发出任何请求"
    assert seen[0].headers.get("Cookie") == "W3SSO=abc"


def test_credential_is_sent_on_model_list_requests():
    """「检测可用模型」走的是另一个端点：它同样要过网关，否则会出现"检测通过、运行被拒"。"""
    seen: list[httpx.Request] = []
    client = _client_capturing(
        seen, extra_headers={"Cookie": "W3SSO=abc"}
    )
    try:
        client.list_models()
    finally:
        client.close()

    assert seen
    assert all(request.headers.get("Cookie") == "W3SSO=abc" for request in seen)


def test_no_credential_header_on_the_wire_when_not_configured():
    """没配置时请求里**不许**出现 Cookie 头（别把内网凭据发给公网 API）。"""
    seen: list[httpx.Request] = []
    client = _client_capturing(seen)
    try:
        client.stream_chat(model="m", messages=[{"role": "user", "content": "hi"}])
    finally:
        client.close()

    assert seen
    assert "cookie" not in {name.lower() for name in seen[0].headers}


# ── 401/403 的那句话 ──────────────────────────────────────────────────────


def test_auth_hint_only_when_enabled():
    assert w3_login.auth_hint(_settings()) == ""


def test_auth_hint_gives_both_ways_out():
    """内网被拒时，两条路的说法都要给：先去登录；若还不行就是缺凭据/凭据过期。"""
    without = w3_login.auth_hint(_settings(w3_login_enabled=True))
    with_value = w3_login.auth_hint(
        _settings(w3_login_enabled=True, w3_credential="W3SSO=abc")
    )

    assert "登录 W3" in without and "Cookie" in without
    assert "过期" in with_value


def test_401_error_message_carries_the_hint_only_when_given():
    """公网用户不该看到"去登录 W3"这种与他无关的指引。"""
    response = httpx.Response(401, json={"error": {"message": "unauthorized"}})
    from tu_shell_agent.agent_backends.api_client import _error_message

    plain = _error_message(response, "https://api.example.com/v1/chat/completions", "m")
    hinted = _error_message(
        response,
        "https://api.example.com/v1/chat/completions",
        "m",
        auth_hint="内网 W3 认证：先登录。",
    )

    assert "W3" not in plain
    assert "内网 W3 认证" in hinted


def test_successful_response_is_not_touched_by_the_hint():
    """403 之外的状态码不该被硬塞进内网说明（否则每次报错都在误导人）。"""
    from tu_shell_agent.agent_backends.api_client import _error_message

    response = httpx.Response(404, json={"error": {"message": "no such model"}})
    message = _error_message(response, "https://api.example.com/v1/chat/completions", "m",
                             auth_hint="内网 W3 认证：先登录。")

    assert "内网 W3 认证" not in message


# ── 状态行：说的话必须与真实行为一致 ─────────────────────────────────────


def test_status_says_nothing_is_sent_while_disabled():
    text = w3_login.status_text(_settings(w3_credential="W3SSO=abc"))

    assert "不会" in text and "凭据" in text


def test_status_tells_where_to_fill_the_url_when_missing():
    """用户现在就没有地址 —— 这句提示必须告诉他去哪填，而不是假装已经处理好了。"""
    text = w3_login.status_text(_settings(w3_login_enabled=True))

    assert "W3 登录地址" in text and "设置" in text


def test_status_reports_the_credential_without_echoing_it():
    text = w3_login.status_text(
        _settings(w3_login_enabled=True, w3_login_url="https://w3.example.com/",
                  w3_credential="W3SSO=topsecret")
    )

    assert "Cookie" in text and "已设置" in text
    assert "topsecret" not in text, "状态行把凭据原文回显出来了"


def test_status_explains_the_no_credential_case():
    """没填凭据时不许说"已登录"：我们并不知道内网网关认哪种方式。"""
    text = w3_login.status_text(
        _settings(w3_login_enabled=True, w3_login_url="https://w3.example.com/")
    )

    assert "未设置凭据" in text
    assert "已登录" not in text


# ── 设置：四个字段能存能读 ────────────────────────────────────────────────


def test_w3_fields_round_trip_through_the_settings_file(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    assert settings.w3_login_enabled is False
    assert settings.w3_login_url == ""
    settings.w3_login_enabled = True
    settings.w3_login_url = "https://w3.example.com/"
    settings.w3_credential_header = "X-Auth-Token"
    settings.w3_credential = "abc"
    settings.save()

    again = AppSettings.load(path)

    assert again.w3_login_enabled is True
    assert again.w3_login_url == "https://w3.example.com/"
    assert again.w3_credential_header == "X-Auth-Token"
    assert again.w3_credential == "abc"


# ── 控制器：什么时候真的去开浏览器 ────────────────────────────────────────


def _window(qtbot, tmp_path, **settings_kwargs):
    """测试用窗口：设置指向 tmp_path（不碰用户的真实设置文件，见 test_run_controller）。"""
    from tu_shell_agent.ui.main_window import MainWindow

    settings = AppSettings(
        run_root=str(tmp_path / "runs"),
        templates_dir=str(tmp_path / "templates"),
        **settings_kwargs,
    )
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    return window


def _controller(qtbot, tmp_path, monkeypatch, opened: list[str], **settings_kwargs):
    """造控制器，并把"打开浏览器"换成一个只记录不真的开浏览器的假动作。

    `_offer_w3_login()` 走的是 `w3_login.open_login_page`，所以在这里替掉它 ——
    比替 `QDesktopServices` 更贴近调用点，也不会因为 Qt 平台插件而假失败。
    """
    from tu_shell_agent.ui.run_controller import RunController

    monkeypatch.setattr(
        w3_login, "open_login_page", lambda url: (opened.append(url), True)[1]
    )
    return RunController(window=_window(qtbot, tmp_path, **settings_kwargs))


def test_controller_opens_the_login_page_once_per_session(qtbot, tmp_path, monkeypatch):
    """勾上选项 + 有地址：第一次用内置 agent 时打开浏览器，同一次启动不再重复打开。"""
    opened: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, opened,
        agent_backend="builtin",
        w3_login_enabled=True,
        w3_login_url="https://w3.example.com/",
    )

    controller._offer_w3_login()
    controller._offer_w3_login()
    controller._offer_w3_login()

    assert opened == ["https://w3.example.com/"]
    assert "W3" in controller.window.status_label.text()


def test_controller_says_where_to_fill_when_the_url_is_missing(qtbot, tmp_path, monkeypatch):
    """用户现在就没有地址：不许开页面，但必须说清楚去哪填（否则他只知道"点了没反应"）。"""
    opened: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, opened, agent_backend="builtin", w3_login_enabled=True
    )

    controller._offer_w3_login()

    assert opened == [], "没有地址时居然打开了浏览器"
    assert "W3 登录地址" in controller.window.status_label.text()


def test_controller_does_nothing_when_the_option_is_off(qtbot, tmp_path, monkeypatch):
    opened: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, opened,
        agent_backend="builtin",
        w3_login_url="https://w3.example.com/",
    )

    controller._offer_w3_login()

    assert opened == []


def test_controller_skips_w3_for_command_line_backends(qtbot, tmp_path, monkeypatch):
    """命令行后端（opencode / claude / codeagent）与 W3 无关，不该去开浏览器。"""
    opened: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, opened,
        agent_backend="opencode",
        w3_login_enabled=True,
        w3_login_url="https://w3.example.com/",
    )

    controller._offer_w3_login()

    assert opened == []


def test_new_controller_offers_again(qtbot, tmp_path, monkeypatch):
    """"每次启动只自动打开一次"是**每次启动**：重开程序后还要再问一次（会话可能过期）。"""
    opened: list[str] = []
    for _ in range(2):
        controller = _controller(
            qtbot, tmp_path, monkeypatch, opened,
            agent_backend="builtin",
            w3_login_enabled=True,
            w3_login_url="https://w3.example.com/",
        )
        controller._offer_w3_login()

    assert opened == ["https://w3.example.com/", "https://w3.example.com/"]


# ── 内置 agent 的配置：带上（或不带）凭据 ─────────────────────────────────


def test_api_config_carries_the_credential_only_when_enabled(qtbot, tmp_path, monkeypatch):
    enabled: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, enabled,
        agent_backend="builtin",
        w3_login_enabled=True,
        w3_credential="W3SSO=secret",
    )
    disabled: list[str] = []
    other = _controller(
        qtbot, tmp_path, monkeypatch, disabled,
        agent_backend="builtin",
        w3_credential="W3SSO=secret",
    )

    assert controller._api_config()["extra_headers"] == {"Cookie": "W3SSO=secret"}
    assert controller._api_config()["auth_hint"] != ""
    assert other._api_config()["extra_headers"] == {}
    assert other._api_config()["auth_hint"] == ""


def test_turning_the_option_off_after_pasting_keeps_settings_intact(
    qtbot, tmp_path, monkeypatch
):
    """取消勾选只是"不再发送"，不该把用户粘进来的凭据擦掉（他可能只是临时换后端）。"""
    opened: list[str] = []
    controller = _controller(
        qtbot, tmp_path, monkeypatch, opened,
        agent_backend="builtin",
        w3_login_enabled=False,
        w3_credential="W3SSO=secret",
    )

    assert controller._api_config()["extra_headers"] == {}
    assert controller.settings.w3_credential == "W3SSO=secret"


# ── 设置页：置灰、状态行、按钮 ────────────────────────────────────────────


def _page(qtbot, settings: AppSettings):
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(settings)
    return page


def test_settings_page_rows_follow_the_builtin_backend(qtbot):
    """这几行与 API 地址/key 一样：只有内置 agent 用得上，别的后端下置灰。"""
    settings = AppSettings(agent_backend="builtin")
    page = _page(qtbot, settings)

    assert page.w3_login_url_edit.isEnabled() is True
    assert page.w3_login_button.isEnabled() is True

    settings.agent_backend = "opencode"
    page.set_settings(settings)
    page._refresh_backend_hint()

    assert page.w3_login_url_edit.isEnabled() is False
    assert page.w3_login_check.isEnabled() is False


def test_settings_page_button_refuses_without_a_url(qtbot, monkeypatch):
    """没填地址点按钮：不开页面，并把"去哪填"写在状态行上。"""
    opened: list[str] = []
    monkeypatch.setattr(
        w3_login, "open_login_page", lambda url: (opened.append(url), True)[1]
    )
    page = _page(qtbot, AppSettings(agent_backend="builtin", w3_login_enabled=True))

    assert page.open_w3_login() is False
    assert opened == []
    assert "W3 登录地址" in page.w3_status_label.text()


def test_settings_page_button_opens_and_reports(qtbot, monkeypatch):
    """填了地址就打开，并在状态行上如实说"已打开、登录完回来"。"""
    opened: list[str] = []
    monkeypatch.setattr(
        w3_login, "open_login_page", lambda url: (opened.append(url), True)[1]
    )
    page = _page(
        qtbot,
        AppSettings(
            agent_backend="builtin",
            w3_login_enabled=True,
            w3_login_url="https://w3.example.com/",
        ),
    )

    assert page.open_w3_login() is True
    assert opened == ["https://w3.example.com/"]
    assert "已在浏览器打开" in page.w3_status_label.text()


def test_settings_page_status_follows_unsaved_edits(qtbot):
    """状态行读的是**当前控件**：刚填完地址还没保存，也不该说"还没填地址"。"""
    page = _page(qtbot, AppSettings(agent_backend="builtin"))

    assert "未启用" in page.w3_status_label.text()           # 还没启用
    assert "凭据" in page.w3_status_label.text()             # 说清"不会发送任何凭据"

    page.w3_login_check.setChecked(True)
    assert "W3 登录地址" in page.w3_status_label.text()      # 启用了但地址空：告诉他去哪填

    page.w3_login_url_edit.setText("https://w3.example.com/")
    text = page.w3_status_label.text()
    assert "https://w3.example.com/" in text
    assert "未设置凭据" in text


def test_settings_page_does_not_echo_the_credential(qtbot):
    """凭据是密码框，状态行里也不许出现它的原文（截图/旁人瞥一眼都不该泄露）。"""
    page = _page(
        qtbot,
        AppSettings(
            agent_backend="builtin",
            w3_login_enabled=True,
            w3_login_url="https://w3.example.com/",
            w3_credential="W3SSO=topsecret",
        ),
    )

    assert page.w3_credential_edit.echoMode().name == "Password"
    assert "topsecret" not in page.w3_status_label.text()


def test_settings_page_collects_the_w3_fields(qtbot, tmp_path):
    """设置页保存时要把这四个值写回设置（不然用户填了却等于没填）。"""
    settings = AppSettings(agent_backend="builtin")
    page = _page(qtbot, settings)
    page.w3_login_check.setChecked(True)
    page.w3_login_url_edit.setText("https://w3.example.com/")
    page.w3_credential_header_edit.setText("X-Auth-Token")
    page.w3_credential_edit.setText("abc")

    collected = page.collect()

    assert collected.w3_login_enabled is True
    assert collected.w3_login_url == "https://w3.example.com/"
    assert collected.w3_credential_header == "X-Auth-Token"
    assert collected.w3_credential == "abc"


# ── 凭据不许落进运行目录 ──────────────────────────────────────────────────


def test_credential_never_lands_in_the_run_directory(qtbot, tmp_path):
    """跑一次真实（假后端）的运行，然后翻整个运行目录：凭据不许出现在任何文件里。

    运行目录会写 `meta.json`（含本次运行的 config 快照）、plan.md、脚本、日志 ——
    用户会把运行目录打包发给别人排查问题，会话 cookie 漏在里面等于把内网凭据送出去。
    """
    import json

    from tu_shell_agent.types import DetectionReport, ExecuteResult, GeneratedScript
    from tu_shell_agent.ui.run_controller import RunController

    class _FakeOpencode:
        def start(self, run_dir, agent_name, model):
            return "ses_w3"

        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            return GeneratedScript(
                script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n",
                notes="n",
                assumptions=(),
            )

        def abort(self, session_id):
            pass

        def dispose(self):
            pass

    class _FakeToolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            return [], 0, '{"comments": []}'

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            return ExecuteResult(0, None, False, False, 1, "ok\n", "")

    secret = "W3SSO=topsecret-cookie"
    window = _window(
        qtbot, tmp_path,
        agent_backend="builtin",
        w3_login_enabled=True,
        w3_login_url="https://w3.example.com/",
        w3_credential=secret,
    )
    controller = RunController(
        opencode=_FakeOpencode(), toolchain=_FakeToolchain(), window=window,
        run_root=str(tmp_path / "runs"),
    )
    plan = tmp_path / "plan.md"
    plan.write_text("打印 ok", encoding="utf-8")
    window.left_pane.set_plan(str(plan))

    with qtbot.waitSignal(controller.finished, timeout=20_000):
        controller.start()

    run_dir = Path(controller._run_dir)
    assert run_dir.is_dir()
    leaked = [
        str(path.relative_to(run_dir))
        for path in run_dir.rglob("*")
        if path.is_file() and secret in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == [], f"凭据出现在运行目录里：{leaked}"
    # 顺带确认这次运行真的把配置写进了 meta.json（否则上面那条是"空跑"）
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("config"), "meta.json 里没有 config，这条用例就白测了"

