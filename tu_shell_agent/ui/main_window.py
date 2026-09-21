"""主窗口：三区 + 底栏 + 两个独立页。只做布局与装配，业务在 run_controller。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .panes.center import CenterPane
from .panes.left import LeftPane
from .panes.right import RightPane
from .panes.templates import TemplatesPane
from .pages.history import HistoryPage
from .pages.selfcheck import SelfCheckPage
from .chat import ChatPanel
from .widgets.collapsible import CollapsibleSection
from .pages.settings_page import SettingsPage
from . import acrylic as acrylic_module
from . import backdrop as backdrop_module
from .settings import AppSettings, default_settings_path, default_templates_dir
from .theme import apply_theme
from ..template_store.store import TemplateStore


def _titled(widget: QWidget, title: str) -> QWidget:
    """给一个控件加一行栏头，返回包好的容器（栏头文字是次级色小标题）。"""
    container = QWidget()
    # 每栏是一张"卡片"：大圆角 + 极淡边框。圆角要看得出来就得有边框，而栏与栏之间的
    # 分隔感也由它提供 —— 分割条平时是透明的（只在悬停时显色）。
    container.setObjectName("paneCard")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    header = QLabel(title)
    header.setObjectName("paneHeader")
    layout.addWidget(header)
    layout.addWidget(widget, 1)
    return container


def _card(widget: QWidget) -> QWidget:
    """给控件套一张卡片（大圆角 + 极淡边框），**不加栏头** —— 栏头由内部控件自己提供。"""
    container = QWidget()
    container.setObjectName("paneCard")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(widget, 1)
    return container


def available_screen_size() -> tuple[int, int]:
    """主屏**可用区**的逻辑像素（扣掉任务栏/停靠区）；拿不到时给一个保守值。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return (1280, 720)
    rect = screen.availableGeometry()
    return (rect.width(), rect.height())


def window_minimum_for(available: tuple[int, int]) -> tuple[int, int]:
    """按屏幕可用区分摊窗口最小尺寸（纯函数，便于用例钉住规则）。

    取可用区的九成（高度取八成半），并保留一个下限（720x520）—— 比这更小的窗口已经
    没法用了，宁可让用户去放大窗口，也不要把界面压成重叠。
    """
    width = max(720, min(960, int(available[0] * 0.9)))
    height = max(520, min(780, int(available[1] * 0.85)))
    return (width, height)


class MainWindow(QMainWindow):
    """三区（方案/脚本/校验）+ 底栏（历史 + 两个独立页）+ 按钮条。

    `wire_controller=False` 是给控制器自己用的：RunController 在没有传入 window 时会建一个
    主窗口，此时若窗口再建一个控制器，就会出现两个控制器同时接管同一批按钮与同一个历史列表
    （回放会被处理两遍）。生产入口 `ui/app.py` 走默认值，窗口自己装配控制器。
    """

    def __init__(self, *, wire_controller: bool = True, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("tu-shell-agent — 方案 → shell 脚本 → 执行 → 校验")
        self.resize(1440, 900)

        self.settings = settings if settings is not None else AppSettings.load(default_settings_path())
        # 亚克力（自绘模糊）的壁纸图层：apply_appearance 里按设置准备，paintEvent 里画
        self._acrylic_image = None
        self._acrylic_wallpaper = ""
        # 壁纸是会变的（本机桌面每 900 秒轮换一次）。低频自检，变了就重新模糊 ——
        # 不做监听是因为壁纸的"当前值"写在别家的状态文件里，轮询最省事也最不容易出错。
        self._acrylic_timer = QTimer(self)
        self._acrylic_timer.setInterval(60_000)
        self._acrylic_timer.timeout.connect(self._poll_acrylic_wallpaper)
        # 滚动条一动就整窗重绘：半透明窗口只重绘一条带时，旧像素会留下来形成重影
        # （用户报的"半透明又成这种重影的了"）。整窗重绘 + paintEvent 里的擦除，
        # 才能保证那块区域回到"全新绘制"的样子。
        self._wire_scroll_repaints()
        # 系统模糊不可用时自绘层会接管（见 apply_appearance）
        self._acrylic_fallback = False
        self._effective_backdrop = "off"

        self.left_pane = LeftPane()
        self.left_pane.setObjectName("leftPane")
        # 模板库的目录来自设置页；没设过就用应用数据目录下的 templates/。
        self.templates_pane = TemplatesPane(
            store=TemplateStore(str(self.settings.templates_dir or default_templates_dir()))
        )
        self.templates_pane.setObjectName("templatesPane")
        # 启动就要有列表：面板自己不会在构造时读盘，不调这一下用户看到的是空面板
        # （会以为"模板丢了"，而模板其实好好躺在磁盘上）。
        self.templates_pane.reload()
        self.center_pane = CenterPane()
        self.center_pane.setObjectName("centerPane")
        self.right_pane = RightPane()
        self.right_pane.setObjectName("rightPane")

        # 每栏顶部一行小标题（Codex 的分区感来自"小号、次级色、字距略宽"的栏头）。
        # 用包装控件而不是往各 pane 里塞标签：pane 的布局归 pane 自己管，
        # 而且骨架测试是按 objectName 找 pane 的，包一层不影响 findChild。

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")   # 测试契约
        self.splitter.addWidget(_titled(self.left_pane, "方案与运行参数"))
        self.splitter.addWidget(_titled(self.center_pane, "脚本与轮次"))
        self.splitter.addWidget(_titled(self.right_pane, "校验与输出"))
        self.splitter.setSizes([360, 620, 460])

        # 底栏左边是历史运行（列表 + 回放），右边是两个独立页。
        # 列表控件本身在 HistoryPage 里，objectName 仍是 historyList（界面骨架测试的契约）。
        # 历史与两个独立页的标题由 _titled 提供，HistoryPage 内部的"历史运行"标签就不需要了
        self.history_page = HistoryPage(run_root=self.settings.run_root)

        self.selfcheck_page = SelfCheckPage()
        self.settings_page = SettingsPage()
        self.settings_page.set_settings(self.settings)
        self.chat_panel = ChatPanel()

        # 工具区：低频面板都收进这一行的页签（用户裁定的排布）。
        # 主区三栏因此各自只干一件事：方案与参数 / 脚本与轮次 / 校验与输出。
        self.tool_tabs = QTabWidget()
        self.tool_tabs.setObjectName("toolTabs")
        self.tool_tabs.addTab(self.history_page, "历史运行")
        self.tool_tabs.addTab(self.templates_pane, "模板库")
        self.tool_tabs.addTab(self.chat_panel, "模型对话")
        self.tool_tabs.addTab(self.selfcheck_page, "环境自检")
        self.tool_tabs.addTab(self.settings_page, "设置")

        self.start_button = QPushButton("开始")
        # 主操作用白底黑字（Codex 的主按钮就这样），其余按钮是"白 5% 叠加 + 1px 边框"
        self.start_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton("取消")
        self.continue_button = QPushButton("继续修复")
        self.verify_button = QPushButton("改后重跑")
        self.open_dir_button = QPushButton("打开运行目录")
        bottom = QHBoxLayout()
        for button in (self.start_button, self.cancel_button, self.continue_button,
                       self.verify_button, self.open_dir_button):
            bottom.addWidget(button)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        bottom.addWidget(self.status_label, 1)


        # 上下两层（三栏区 / 历史与设置区）放进竖向分割器：原来是一个 QVBoxLayout 的
        # addWidget(..., 1) + addLayout(..., 1)，比例固定 1:1，用户**根本没法调** ——
        # 这就是"不能调节竖向的长度"。现在拖中间那条就能改，而且尺寸会记进设置。
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setObjectName("verticalSplitter")
        self.vertical_splitter.addWidget(self.splitter)
        # 工具区默认**收起**，只留一行标题：用户反馈"工具区有点太占用空间了，
        # 软件的主要作用是写 shell 脚本"。点它的标题、或点某个页签、或需要看提议时
        # （见 focus_tool_tab）会自动展开，展开的高度记在分割器尺寸里（跟其它尺寸一起持久化）。
        self.tool_section = CollapsibleSection("工具区", self.tool_tabs)
        self.tool_section.setObjectName("toolSection")
        self.vertical_splitter.addWidget(_card(self.tool_section))
        self.vertical_splitter.setSizes([900, 40])
        self.tool_section.header.mousePressEvent = self._on_tool_header_clicked  # type: ignore[method-assign]
        self.tool_tabs.currentChanged.connect(lambda _index: self.expand_tool_area())

        root_layout = QVBoxLayout()
        root_layout.addWidget(self.vertical_splitter, 1)
        root_layout.addLayout(bottom)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        # 设置里存的值要体现在界面上，否则用户会以为设置没生效——设置页那四个运行参数
        # （轮次/两个超时/阻断级别）此前根本没人读，是四个死值。
        for splitter in self.findChildren(QSplitter):
            splitter.splitterMoved.connect(lambda *_args: self._save_layout())
        # 折叠状态也记进设置（默认展开，用户收起哪块就记哪块）
        self._apply_settings_to_inputs()
        # 右栏那句"会不会阻断"必须跟着**生效**的级别走：引擎读的是左栏那个下拉框，
        # 用户一改就该立刻反映，不能等到下次运行。
        self.left_pane.blocking_combo.currentTextChanged.connect(
            self._on_blocking_level_changed
        )
        self.settings_page.saved.connect(self._on_settings_saved)
        # 外观控件改动 → 立即应用（只改内存，不落盘；落盘仍由"保存"负责）。
        # 没有这一步，用户改完缩放要先去点"保存"才看得到效果，体感就是"改了不管用"。
        self.settings_page.appearance_changed.connect(self._preview_appearance)

        # 把手宽度写进代码而不是只靠 QSS：样式表没加载时（或换主题时）它会退回 Qt 默认的
        # 4px，而 4px 抓不住 —— 用户"不能调节竖向的长度"就是这么来的。命中目标不能依赖样式。
        # 用 findChildren 而不是列举我已知的那几个：中栏内部还有一个 splitter
        # （页签 / 轮次时间线），漏掉它那条把手就只有 4px —— 测试里就是这么抓到的。
        for splitter in self.findChildren(QSplitter):
            splitter.setHandleWidth(8)

        # 放开最小高度：各栏内部控件的 minimumSizeHint 加起来有 400~500px，会**锁死**分割器
        # 的比例（拖了也没反应，看起来像"不能调"）。这里给每块一个能接受的小下限，
        # 让用户真的能把某一块压小；压小了内部靠滚动条看。
        for widget, minimum in (
            (self.left_pane, 220), (self.templates_pane, 220),
            (self.center_pane, 240), (self.right_pane, 260),
            (self.tool_tabs, 300),
        ):
            widget.setMinimumHeight(minimum)
        # 各栏还要有最小**宽度**：只设高度的话，横向把窗口压窄时三栏会一路缩到贴边
        # （内容被裁掉/看不见）。这三个数是"每栏还能看清内容"的下限。
        self.left_pane.setMinimumWidth(240)
        self.templates_pane.setMinimumWidth(240)
        self.center_pane.setMinimumWidth(300)
        self.right_pane.setMinimumWidth(260)
        self.tool_tabs.setMinimumWidth(300)

        # 窗口本身的最小尺寸 = 三栏最小宽 + 两条 8px 把手 + 边距；高度 = 左列两块 + 下方 + 按钮条。
        # 低于这个尺寸这个界面本来就不可用，与其让 Qt 把内容裁掉，不如让窗口管理器直接不许缩到那么小
        # （平铺窗口管理器也会读 min-size 提示）。
        # 高度的下限按"工具区里最高的那个页签装得下"来定：实测对话面板 minimumSizeHint
        # 305px、模板面板 465px、历史页 452px —— 给 300 会让对话面板溢出被裁掉（症状是
        # 输入框盖在记录区上、内容看不全）。所以工具区最小 300、窗口相应留到 780。
        # 窗口最小尺寸按**屏幕可用区**收敛，而不是写死。
        # 用户反馈（Windows 150% 缩放）：左侧文字出框、对话面板与输入框重叠、右栏被压缩。
        # 根因就是写死的 960x780：1080p 在 150% 缩放下只有 720 逻辑像素高，窗口的最小高度
        # 比屏幕还高，窗口管理器照给，布局只能违反最小尺寸 —— 于是重叠与出框。
        min_width, min_height = window_minimum_for(available_screen_size())
        self.setMinimumSize(min_width, min_height)

        self._layout_restored = False
        self.controller = None
        if wire_controller:
            self._wire_controller()

    def expand_tool_area(self, *, minimum: int = 300) -> bool:
        """展开工具区（已展开则不动），返回是否发生了变化。

        默认收起是为了把高度让给脚本视图；一旦用户真的要在这里看东西（点页签、
        或对话里出现需要他决定的提议），就必须自动展开 —— 否则"东西在那儿但他看不见"。
        """
        changed = False
        if self.tool_section.is_collapsed():
            self.tool_section.set_collapsed(False)
            changed = True
        if changed:
            self._set_tool_area_height(minimum)
            self._persist_tool_area(collapsed=False)
        return changed

    def _set_tool_area_height(self, height: int) -> None:
        """把工具区设成指定高度。**必须等布局更新之后**再调（见 showEvent 的同一坑）。"""
        def apply() -> None:
            sizes = self.vertical_splitter.sizes()
            if len(sizes) != 2:
                return
            total = sum(sizes)
            tool = max(40, min(height, max(40, total - 240)))
            self.vertical_splitter.setSizes([max(1, total - tool), tool])

        QTimer.singleShot(0, apply)

    def _persist_tool_area(self, *, collapsed: bool) -> None:
        if getattr(self.settings, "tool_area_collapsed", True) == collapsed:
            return
        try:
            self.settings.tool_area_collapsed = collapsed
            self.settings.save()
        except (OSError, ValueError) as error:
            self.set_status(f"工具区状态未能保存：{error}")

    def focus_tool_tab(self, page: QWidget) -> None:
        """切到某个工具页并确保工具区展开（提议出现、点"把脚本放进中栏"这类流程用它）。"""
        index = self.tool_tabs.indexOf(page)
        if index >= 0:
            self.tool_tabs.setCurrentIndex(index)
        self.expand_tool_area()

    def _on_tool_header_clicked(self, event) -> None:
        """点"工具区"标题：收起/展开切换（收起是默认，所以要能给用户收回去）。"""
        if self.tool_section.is_collapsed():
            self.expand_tool_area()
            return
        self.tool_section.set_collapsed(True)
        self._set_tool_area_height(40)
        self._persist_tool_area(collapsed=True)

    def _wire_scroll_repaints(self) -> None:
        """任何滚动条动一下就让整窗重绘一次（半透明窗口防重影，见 paintEvent 的说明）。"""
        from PySide6.QtWidgets import QAbstractScrollArea

        for area in self.findChildren(QAbstractScrollArea):
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                if bar is not None:
                    bar.valueChanged.connect(lambda _value: self.update())

    # ── 外观（缩放 / 字体 / 背景效果）────────────────────────────────
    def apply_appearance(self) -> None:
        """把外观设置装到应用与窗口上；保存设置后也会调它（热更新，不用重启）。

        - 缩放/字体/配色走 `apply_theme`（全局样式表 + 调色板 + 基础字号）；
        - 半透明需要窗口属性（`WA_TranslucentBackground`）；
        - 亚克力模糊优先问平台要（Windows 的 DWM）；平台给不了就**改用界面自绘模糊**
          （同一套观感，只是模糊由界面自己算），两者都拿不到才退化为半透明。
        """
        mode = str(getattr(self.settings, "backdrop", "off") or "off")
        # 只有三种背景效果：不透明 / 半透明 / 亚克力模糊。后两者的底色都由**界面自绘**
        # （半透明 = 壁纸不模糊 + 淡色调；亚克力 = 壁纸模糊 + 浓色调），窗口本身始终不透明。
        # 为什么不问系统要：Windows 上 WA_TranslucentBackground 必须配无边框窗口才生效，
        # DWM 亚克力又要求窗口没有重定向位图（Qt 的普通窗口有）—— 两条路在真机上都不成立，
        # 于是两端改成同一条自绘路径，行为完全一致。
        effective = mode
        if mode != "off" and acrylic_module.find_wallpaper(
            str(getattr(self.settings, "acrylic_wallpaper", "") or "")
        ) is None:
            effective = "off"          # 没有壁纸就画不出"透明"，退回纯色而不是留一块假的
        self._effective_backdrop = effective

        app = QApplication.instance()
        if app is not None:
            apply_theme(
                app,
                scale=float(getattr(self.settings, "ui_scale", 1.0) or 1.0),
                ui_font=str(getattr(self.settings, "ui_font", "") or ""),
                mono_font=str(getattr(self.settings, "mono_font", "") or ""),
                backdrop=effective,
            )
        backdrop_module.set_mode(effective)
        backdrop_module.apply_to(self)
        for widget in QApplication.topLevelWidgets():
            if widget is not self:
                backdrop_module.apply_to(widget)

        # 字体/缩放变了，表单标签列要按新字体重算宽度（QSS padding 不计入 sizeHint）
        from .theme import fit_form_labels

        fit_form_labels(self)
        layer_ready = self._refresh_acrylic()
        if mode != "off" and not layer_ready:
            self.set_status("未找到壁纸图片，已使用纯色背景。请在设置的「背景壁纸」中指定。")
        if effective in ("translucent", "acrylic"):
            self._acrylic_timer.start()
        else:
            self._acrylic_timer.stop()

    def _window_background_color(self):
        """重绘前用来重填受损区域的**不透明**底色。

        窗口现在始终不透明，所以这里必须给不透明色：给带 alpha 的窗口底色会在重填处
        挖出"窟窿"（Qt 画好的底色被替换成了半透明像素）。有自绘底色的模式用它的色调色，
        纯色模式用主题底色。
        """
        from PySide6.QtGui import QColor

        from .theme import backdrop_colors

        mode = str(getattr(self, "_effective_backdrop", "off") or "off")
        if mode != "off":
            tint = (
                acrylic_module.TINT
                if mode == "acrylic"
                else acrylic_module.TRANSLUCENT_TINT
            )
            return QColor(*tint)          # tint 自带 alpha，这里要的是它压出来的实色
        value = backdrop_colors("off")["bg"]
        return QColor(*value) if isinstance(value, tuple) else QColor(str(value))

    def _refresh_acrylic(self) -> bool:
        """按当前模式准备/清掉"自绘底色层"；返回是否真的铺上了。

        - 半透明：壁纸**不模糊** + 淡色调（看起来就是透过一块玻璃看桌面）；
        - 亚克力：壁纸模糊（强度来自设置）+ 浓色调；
        - 不透明：不铺。
        """
        mode = getattr(self, "_effective_backdrop", None) or str(
            getattr(self.settings, "backdrop", "off") or "off"
        )
        if mode not in ("translucent", "acrylic"):
            self._acrylic_image = None
            return False
        wallpaper = acrylic_module.find_wallpaper(
            str(getattr(self.settings, "acrylic_wallpaper", "") or "")
        )
        self._acrylic_wallpaper = wallpaper or ""
        blur = (
            int(getattr(self.settings, "acrylic_blur", acrylic_module.DEFAULT_BLUR) or 0)
            if mode == "acrylic"
            else 0
        )
        self._acrylic_image = acrylic_module.backdrop_image(
            wallpaper,
            max(1, self.width()),
            max(1, self.height()),
            blur,
            tint=(
                acrylic_module.TINT if mode == "acrylic" else acrylic_module.TRANSLUCENT_TINT
            ),
        )
        self.update()
        return self._acrylic_image is not None

    def _poll_acrylic_wallpaper(self) -> None:
        """壁纸换了吗？换了就重新模糊（桌面轮换壁纸时模糊层不该停在旧图上）。"""
        mode = getattr(self, "_effective_backdrop", None) or str(
            getattr(self.settings, "backdrop", "off") or "off"
        )
        if mode == "off":
            self._acrylic_timer.stop()
            return
        found = acrylic_module.find_wallpaper(
            str(getattr(self.settings, "acrylic_wallpaper", "") or "")
        ) or ""
        if found != self._acrylic_wallpaper:
            self._refresh_acrylic()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """亚克力模式：**先**铺自己模糊好的壁纸，再让 QSS 的半透明深色底压上去。

        顺序不能反：壁纸在最底层、深色底在它上面，才是毛玻璃的观感；反过来壁纸会把
        底色整块盖掉（那就是一张普通背景图，不是亚克力了）。

        第一件事是**擦掉这次要重绘的区域**：半透明窗口的底色带 alpha，正常绘制是混合而不是
        覆盖，滚动/重排后只重绘一块时会留下上一次的像素 —— 那就是用户看到的"重影"。
        """
        backdrop_module.erase_damage(self, event, self._window_background_color())
        image = getattr(self, "_acrylic_image", None)
        if image is not None:
            painter = QPainter(self)
            painter.drawImage(self.rect(), image)
            painter.end()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """窗口尺寸变了要按新尺寸重新生成模糊层（生成结果按尺寸分桶缓存，不会每像素重算）。"""
        super().resizeEvent(event)
        if getattr(self, "_acrylic_image", None) is not None:
            self._refresh_acrylic()

    def _preview_appearance(self) -> None:
        """即时预览：把设置页当前的控件值收进内存再应用一次外观（**不写文件**）。"""
        self.settings_page.collect()
        self.apply_appearance()

    def _appearance_hint(self) -> str:
        """给设置页/状态栏用的一句话说明（测试与用户都看这句）。"""
        if self.settings.backdrop != "off" and getattr(self, "_acrylic_image", None) is None:
            return "未找到壁纸图片，已使用纯色背景"
        return ""

    # ── 布局记忆 ────────────────────────────────────────────────────
    def _splitter_state(self) -> dict[str, list[int]]:
        """四个分割器的尺寸。存**尺寸**而不是 saveState() 的字节：尺寸是可读的 JSON，
        换 Qt 版本也不会失效，出问题时用户能自己看一眼。"""
        return {
            "main": self.splitter.sizes(),
            "vertical": self.vertical_splitter.sizes(),
            "center": self.center_pane.splitter.sizes(),
        }

    def _save_layout(self) -> None:
        """拖动后立刻写盘（失败只记状态栏，不打断用户）。"""
        try:
            self.settings.layout = json.dumps(self._splitter_state(), ensure_ascii=False)
            self.settings.save()
        except (OSError, ValueError) as error:
            self.set_status(f"布局未能保存：{error}")

    def _apply_tool_area_state(self) -> None:
        """按设置里的"收起/展开"落地工具区高度（默认收起 = 只留标题那一条）。"""
        if getattr(self.settings, "tool_area_collapsed", True):
            self.tool_section.set_collapsed(True)
            self._set_tool_area_height(40)
        else:
            self.tool_section.set_collapsed(False)
            self._set_tool_area_height(300)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """首次显示时还原布局尺寸。

        必须在 showEvent 里做，不能在 `__init__` 里：控件没显示之前几何尺寸还没定下来，
        那时 `setSizes()` 会被最小高度夹成一个平均分布 —— 表现为"设置里存了、开窗却没还原"
        （实测：构造期设 [397,649]，显示后变成 [238,238]）。
        """
        super().showEvent(event)
        if not self._layout_restored:
            self._layout_restored = True
            self._restore_layout()
            # 工具区状态在**还原布局之后**落地：它是"收起/展开"的开关，
            # 优先于上次拖出来的分割器尺寸（否则用户收起过、下次开窗又变回一大块）
            self._apply_tool_area_state()

    def _restore_layout(self) -> None:
        """按上次拖出来的尺寸还原；没存过或存坏了就用默认比例。"""
        raw = getattr(self.settings, "layout", "") or ""
        if not raw.strip():
            return
        try:
            saved = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(saved, dict):
            return
        for key, splitter in (
            ("main", self.splitter),
            ("vertical", self.vertical_splitter),
            ("center", self.center_pane.splitter),
        ):
            sizes = saved.get(key)
            if isinstance(sizes, list) and len(sizes) == splitter.count():
                splitter.setSizes([int(value) for value in sizes])

    # ── 接线 ──────────────────────────────────────────────────────
    def _wire_controller(self) -> None:
        """装配控制器。按钮接线在控制器里做：它才是"我这个控件归谁管"的主人，
        而且测试直接构造 RunController 时也要能拿到同一套接线。"""
        from .run_controller import RunController

        RunController(
            window=self,
            settings=self.settings,
            run_root=self.settings.run_root,
            # 生产语义：非 trusted 模板的每一次执行都要过人工闸门（规格 §9）。
            # auto_confirm=False + confirm_answer=None → 控制器弹真实对话框；
            # trusted 模板由引擎自己跳过确认，根本不会走到这里。
            auto_confirm=False,
            confirm_answer=None,
        )

    def _apply_settings_to_inputs(self) -> None:
        """把设置里的值铺到界面上（设置是"默认值"，左栏仍是本次运行可改的地方）。"""
        pane = self.left_pane
        # 运行根只在左栏为空时预填：别覆盖用户刚敲进去的
        if self.settings.run_root and not pane.run_root_edit.text().strip():
            pane.run_root_edit.setText(self.settings.run_root)
        pane.blocking_combo.setCurrentText(self.settings.blocking_level)
        pane.max_rounds_spin.setValue(self.settings.max_rounds)
        pane.generate_timeout_spin.setValue(self.settings.generate_timeout_ms)
        pane.execute_timeout_spin.setValue(self.settings.execute_timeout_ms)

    def _on_blocking_level_changed(self, level: str) -> None:
        self.right_pane.blocking_level = level

    def _on_settings_saved(self, _path: str) -> None:
        """设置保存后重新装载：只影响**之后**的运行，不打断正在跑的。"""
        self.settings = self.settings_page.collect()
        self.history_page.run_root = self.settings.run_root
        self.history_page.reload()
        for splitter in self.findChildren(QSplitter):
            splitter.splitterMoved.connect(lambda *_args: self._save_layout())
        # 折叠状态也记进设置（默认展开，用户收起哪块就记哪块）
        self._apply_settings_to_inputs()
        if self.settings.templates_dir:
            # 模板目录改了就得换库：不换的话设置页显示"已保存"，模板面板还指着旧目录
            self.templates_pane.set_store(TemplateStore(str(self.settings.templates_dir)))
        self.set_status(f"设置已保存：{_path}")

    # ── 供控制器调用 ──────────────────────────────────────────────
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_running(self, running: bool) -> None:
        """运行期间只留"取消"可点：重复点"开始"会在同一个运行目录上再起一个 worker。"""
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.continue_button.setEnabled(not running)
        self.verify_button.setEnabled(not running)
        self.open_dir_button.setEnabled(not running)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 关闭时先把 worker 收掉：QThread 还在跑就被析构会让进程崩在退出路径上。
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.shutdown()
        super().closeEvent(event)
