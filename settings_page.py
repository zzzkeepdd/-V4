# -*- coding: utf-8 -*-
"""
用户设置页 —— PyQt6 控件。

功能：
- 最大杠杆（1~20x）QSpinBox
- 单笔风险比例（0.5~3%）QDoubleSpinBox
- 单日亏损上限（1~10%）QDoubleSpinBox
- 策略冷却期（6~48h）QSpinBox
- 模拟/实盘切换 QComboBox
- 参数保存到 config/user_config.json，启动时自动加载
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ===== 路径常量 =====
try:
    from exchange_interface import USER_CONFIG_FILE, load_config, save_config
except ImportError:
    def _get_root() -> Path:
        return Path(__file__).resolve().parent

    USER_CONFIG_FILE = _get_root() / "config" / "user_config.json"

    def load_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def save_config(path: Path, new_vals: Dict[str, Any]) -> None:
        try:
            existing: Dict[str, Any] = {}
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(loaded, dict):
                    existing = loaded
            existing.update(new_vals or {})
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"保存配置失败：{path}，错误：{exc}")
            raise


# ===== 涩谷风配色 =====
_COLOR_BG_FRAME = "#16213e"       # 卡片背景
_COLOR_BORDER = "#2a2a4a"         # 边框
_COLOR_TEXT = "#eaeaea"           # 主文字
_COLOR_MUTED = "#a0a0b0"          # 次要文字
_COLOR_ACCENT = "#e94560"         # 强调色
_COLOR_BLUE = "#48dbfb"           # 天空蓝
_COLOR_GREEN = "#2ed573"          # 盈亏绿
_COLOR_INPUT_BG = "#0f3460"       # 输入框背景
_COLOR_BUTTON_BG = "#e94560"      # 按钮背景
_COLOR_BUTTON_HOVER = "#ff6b6b"   # 按钮悬停
_COLOR_SAVED = "#2ed573"          # 保存成功提示


# ===== 参数定义 =====
_PARAM_DEFS = {
    "max_leverage": {
        "label": "📈 最大杠杆",
        "default": 20,
        "min": 1,
        "max": 20,
        "step": 1,
        "unit": "x",
        "tip": "单笔交易允许的最大杠杆倍数（风控硬限制）",
        "type": "spin",
    },
    "risk_per_trade_pct": {
        "label": "🎯 单笔风险比例",
        "default": 1.0,
        "min": 0.5,
        "max": 3.0,
        "step": 0.5,
        "unit": "%",
        "tip": "每笔交易最多亏损本金的百分比",
        "type": "double",
    },
    "daily_loss_limit_pct": {
        "label": "🛑 单日亏损上限",
        "default": 5.0,
        "min": 1.0,
        "max": 10.0,
        "step": 1.0,
        "unit": "%",
        "tip": "当日累计亏损达到此比例则熔断所有新开仓",
        "type": "double",
    },
    "strategy_cooldown_hours": {
        "label": "⏳ 策略冷却期",
        "default": 12,
        "min": 6,
        "max": 48,
        "step": 6,
        "unit": "小时",
        "tip": "同向策略切换后需等待此时间才能再次切换",
        "type": "spin",
    },
}


class SettingsPage(QFrame):
    """用户设置页。

    包含风险参数、冷却期、模拟/实盘切换等所有用户可调参数。
    参数保存到 config/user_config.json，启动时自动加载。

    Signals:
        settings_saved(dict): 参数保存后发出，携带完整参数字典
        exchange_mode_changed(str): 模拟/实盘切换时发出（含提示）
    """

    settings_saved = pyqtSignal(dict)
    exchange_mode_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        """初始化设置页。

        Args:
            parent: 父级控件
        """
        super().__init__(parent)
        self._config_path: Path = USER_CONFIG_FILE
        self._widgets: Dict[str, Any] = {}
        self._saved_config: Dict[str, Any] = {}
        self._previous_mode: str = "模拟"

        self._setup_ui()
        self._apply_styles()
        self.load_settings()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建完整的设置页 UI。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 用 QScrollArea 包裹（参数多时可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # ---- 区块1：风险参数 ----
        risk_group = self._create_section("🛡️ 风险参数", "控制交易风险的核心参数，这些值会被风控模块硬编码引用")
        risk_layout = QFormLayout()
        risk_layout.setContentsMargins(16, 8, 16, 8)
        risk_layout.setSpacing(12)
        risk_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # 创建各参数控件
        for key, defn in _PARAM_DEFS.items():
            row = self._create_param_row(key, defn)
            risk_layout.addRow(row)

        risk_group_layout = risk_group.layout() or QVBoxLayout()
        risk_group_layout.addLayout(risk_layout)
        content_layout.addWidget(risk_group)

        # ---- 区块2：交易所模式 ----
        mode_group = self._create_section("🔀 交易所模式", "切换模拟盘/实盘（实盘需单独配置 API 凭证，切换后需重启）")
        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(16, 8, 16, 8)
        mode_layout.setSpacing(12)

        mode_label = QLabel("当前模式：")
        mode_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 11pt;")
        mode_layout.addWidget(mode_label)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["模拟", "实盘"])
        self._mode_combo.setFixedWidth(120)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)

        self._mode_status = QLabel("🟢 模拟盘运行中")
        self._mode_status.setStyleSheet(f"color: {_COLOR_GREEN}; font-size: 10pt; padding-left: 8px;")
        mode_layout.addWidget(self._mode_status)

        mode_layout.addStretch()
        mode_group.layout().addLayout(mode_layout)
        content_layout.addWidget(mode_group)

        # ---- 区块3：API 凭证提示 ----
        api_group = self._create_section("🔑 API 凭证", "交易所 API 密钥配置（加密存储）")
        api_layout = QVBoxLayout()
        api_layout.setContentsMargins(16, 8, 16, 8)
        api_layout.setSpacing(6)

        api_note = QLabel(
            "API 密钥存储在 config/api_config.json（加密格式）。\n"
            "启动时自动加载，连接失败时请检查配置文件是否存在。"
        )
        api_note.setWordWrap(True)
        api_note.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 10pt;")
        api_layout.addWidget(api_note)

        api_path_label = QLabel(f"📁 {USER_CONFIG_FILE.parent / 'api_config.json'}")
        api_path_label.setStyleSheet(f"color: {_COLOR_BORDER}; font-size: 9pt; font-family: Consolas;")
        api_layout.addWidget(api_path_label)

        api_group.layout().addLayout(api_layout)
        content_layout.addWidget(api_group)

        # ---- 保存按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(16, 4, 16, 4)

        self._save_btn = QPushButton("💾 保存设置")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setMinimumWidth(140)
        self._save_btn.clicked.connect(self.save_settings)
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_COLOR_BUTTON_BG};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 11pt;
                font-weight: bold;
                padding: 6px 20px;
            }}
            QPushButton:hover {{
                background-color: {_COLOR_BUTTON_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {_COLOR_ACCENT};
            }}
        """)
        btn_layout.addWidget(self._save_btn)

        self._save_status = QLabel("")
        self._save_status.setStyleSheet(f"color: {_COLOR_SAVED}; font-size: 10pt; padding-left: 12px;")
        btn_layout.addWidget(self._save_status)

        btn_layout.addStretch()
        content_layout.addLayout(btn_layout)

        content_layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _create_section(self, title: str, tip: str) -> QGroupBox:
        """创建带标题和提示的分组框。

        Args:
            title: 区块标题
            tip: 区块提示文字

        Returns:
            QGroupBox 实例
        """
        group = QGroupBox()
        group.setTitle("")

        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 标题行
        header = QHBoxLayout()
        title_label = QLabel(title)
        title_font = QFont("Microsoft YaHei", 11, QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setStyleSheet(f"color: {_COLOR_TEXT};")
        header.addWidget(title_label)

        tip_label = QLabel(f"  —  {tip}")
        tip_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 9pt;")
        header.addWidget(tip_label)
        header.addStretch()
        layout.addLayout(header)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {_COLOR_BORDER}; max-height: 1px;")
        layout.addWidget(sep)

        return group

    def _create_param_row(self, key: str, defn: Dict[str, Any]) -> QWidget:
        """为单个参数创建控件行。

        Args:
            key: 参数字典键名
            defn: 参数定义 dict

        Returns:
            包含 label + input + unit + tip 的 QWidget
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(8)

        # 参数名标签
        label = QLabel(defn["label"])
        label.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 11pt;")
        label.setMinimumWidth(120)
        row_layout.addWidget(label)

        # 输入控件
        if defn["type"] == "spin":
            widget = QSpinBox()
            widget.setRange(defn["min"], defn["max"])
            widget.setSingleStep(defn["step"])
            widget.setValue(defn["default"])
            widget.setFixedWidth(80)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        elif defn["type"] == "double":
            widget = QDoubleSpinBox()
            widget.setRange(defn["min"], defn["max"])
            widget.setSingleStep(defn["step"])
            widget.setDecimals(1)
            widget.setValue(defn["default"])
            widget.setFixedWidth(80)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setSuffix(" %")  # 后缀百分号
        else:
            widget = QSpinBox()
            widget.setRange(defn["min"], defn["max"])
            widget.setValue(defn["default"])

        self._widgets[key] = widget
        row_layout.addWidget(widget)

        # 单位标签（double 类型已有后缀，不再额外显示 %）
        if defn["type"] != "double":
            unit_label = QLabel(defn.get("unit", ""))
            unit_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 10pt;")
            row_layout.addWidget(unit_label)

        # 提示（hover 可见的 tooltip 用简略文本）
        tip_label = QLabel("💡")
        tip_label.setToolTip(defn.get("tip", ""))
        tip_label.setStyleSheet(f"color: {_COLOR_BLUE}; font-size: 11pt;")
        row_layout.addWidget(tip_label)

        row_layout.addStretch()
        return row

    # ------------------------------------------------------------------
    #  样式
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        """应用涩谷风全局样式。"""
        self.setStyleSheet(f"SettingsPage {{ background: transparent; }}")
        # 输入控件统一样式通过父级设置
        self.setStyleSheet(f"""
            QGroupBox {{
                background-color: {_COLOR_BG_FRAME};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 10px;
                padding: 8px;
            }}
            QSpinBox, QDoubleSpinBox {{
                background-color: {_COLOR_INPUT_BG};
                color: {_COLOR_TEXT};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11pt;
            }}
            QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {_COLOR_BLUE};
            }}
            QComboBox {{
                background-color: {_COLOR_INPUT_BG};
                color: {_COLOR_TEXT};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11pt;
                min-width: 100px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {_COLOR_INPUT_BG};
                color: {_COLOR_TEXT};
                selection-background-color: {_COLOR_ACCENT};
            }}
            QComboBox:focus {{
                border-color: {_COLOR_BLUE};
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
        """)

    # ------------------------------------------------------------------
    #  保存 / 加载
    # ------------------------------------------------------------------

    def load_settings(self) -> None:
        """从 user_config.json 加载所有已保存的设置。"""
        config = load_config(self._config_path)
        self._saved_config = dict(config)

        # 加载风险参数
        for key in _PARAM_DEFS:
            saved_val = config.get(key)
            if saved_val is not None:
                widget = self._widgets.get(key)
                if widget:
                    widget.setValue(saved_val)

        # 加载交易所模式
        mode = config.get("exchange_mode", "模拟")
        self._previous_mode = mode
        self._mode_combo.setCurrentText(mode)
        self._update_mode_status(mode)

    def save_settings(self) -> None:
        """保存当前所有设置到 user_config.json。

        合并模式保存，不覆盖其他模块写入的字段（如本金）。
        """
        new_vals: Dict[str, Any] = {}

        for key in _PARAM_DEFS:
            widget = self._widgets.get(key)
            if widget:
                new_vals[key] = widget.value()

        # 交易所模式
        mode = self._mode_combo.currentText()
        new_vals["exchange_mode"] = mode

        try:
            save_config(self._config_path, new_vals)
            self._saved_config = dict(load_config(self._config_path))
            self._save_status.setText("✅ 设置已保存")
            self._save_status.setStyleSheet(f"color: {_COLOR_SAVED}; font-size: 10pt; padding-left: 12px;")
            self.settings_saved.emit(new_vals)
        except Exception as exc:
            self._save_status.setText(f"❌ 保存失败：{exc}")
            self._save_status.setStyleSheet("color: #ff4757; font-size: 10pt; padding-left: 12px;")

    def get_all_settings(self) -> Dict[str, Any]:
        """获取当前所有设置值（不保存）。

        Returns:
            dict 所有参数字典
        """
        result: Dict[str, Any] = {}
        for key in _PARAM_DEFS:
            widget = self._widgets.get(key)
            if widget:
                result[key] = widget.value()
        result["exchange_mode"] = self._mode_combo.currentText()
        return result

    # ------------------------------------------------------------------
    #  模式切换
    # ------------------------------------------------------------------

    def _on_mode_changed(self, new_mode: str) -> None:
        """交易所模式切换回调 —— 弹窗提示需重启。

        Args:
            new_mode: '模拟' | '实盘'
        """
        if new_mode == self._previous_mode:
            return

        # 弹窗提示
        msg = QMessageBox(self)
        msg.setWindowTitle("模式切换")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(f"即将切换到「{new_mode}」模式")
        msg.setInformativeText(
            f"交易所模式变更后需要重启应用才能生效。\n\n"
            f"切换到实盘前请确保：\n"
            f"  • API 凭证已正确配置（config/api_config.json）\n"
            f"  • 网络代理（如有需自行配置）\n"
            f"  • 已充分了解实盘风险\n\n"
            f"当前模式仍为「{self._previous_mode}」，重启后切换为「{new_mode}」。"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setStyleSheet(f"""
            QMessageBox {{
                background-color: {_COLOR_BG_FRAME};
                color: {_COLOR_TEXT};
            }}
            QPushButton {{
                background-color: {_COLOR_BUTTON_BG};
                color: white;
                border-radius: 6px;
                padding: 6px 16px;
            }}
        """)
        msg.exec()

        # 记住新选择（下次启动生效）
        self._previous_mode = new_mode
        self._update_mode_status(new_mode)
        self.exchange_mode_changed.emit(new_mode)

    def _update_mode_status(self, mode: str) -> None:
        """更新模式状态指示器。

        Args:
            mode: '模拟' | '实盘'
        """
        if mode == "实盘":
            self._mode_status.setText("🔴 实盘模式（重启生效）")
            self._mode_status.setStyleSheet("color: #ff4757; font-size: 10pt; padding-left: 8px;")
        else:
            self._mode_status.setText("🟢 模拟盘运行中")
            self._mode_status.setStyleSheet(f"color: {_COLOR_GREEN}; font-size: 10pt; padding-left: 8px;")


# ============================================================================
#  自检入口
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = QMainWindow()
    window.setWindowTitle("用户设置 — 自检")
    window.setStyleSheet("background-color: #1a1a2e;")

    settings = SettingsPage()
    settings.setFixedWidth(520)
    window.setCentralWidget(settings)
    window.resize(550, 520)
    window.show()

    # 打印设置（用于验证）
    def on_saved(data: dict) -> None:
        print(f"设置已保存: {json.dumps(data, ensure_ascii=False, indent=2)}")

    settings.settings_saved.connect(on_saved)

    sys.exit(app.exec())
