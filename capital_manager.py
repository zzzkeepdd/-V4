# -*- coding: utf-8 -*-
"""
本金管理窗口 —— PyQt6 控件。

功能：
- 显示交易所真实余额（USDT）
- 用户输入自定义本金（滑块 + 输入框联动）
- 硬编码防护：本金 ≤ 真实余额
- 保存到 config/user_config.json
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# ===== 路径常量 =====
try:
    from exchange_interface import USER_CONFIG_FILE, load_config, save_config
except ImportError:
    # 兜底：独立运行时
    def _get_root() -> Path:
        return Path(__file__).resolve().parent

    USER_CONFIG_FILE = _get_root() / "config" / "user_config.json"

    def load_config(path: Path) -> Dict[str, Any]:
        """安全加载 JSON 配置。"""
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def save_config(path: Path, new_vals: Dict[str, Any]) -> None:
        """合并保存 JSON 配置。"""
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
_COLOR_BG = "#16213e"          # 卡片背景
_COLOR_BORDER = "#2a2a4a"      # 边框
_COLOR_TEXT = "#eaeaea"        # 主文字
_COLOR_MUTED = "#a0a0b0"       # 次要文字
_COLOR_ACCENT = "#e94560"      # 强调色（珊瑚红）
_COLOR_BLUE = "#48dbfb"        # 天空蓝
_COLOR_GREEN = "#2ed573"       # 盈亏绿
_COLOR_SLIDER_TRACK = "#0f3460"
_COLOR_SLIDER_HANDLE = "#e94560"


class CapitalManager(QFrame):
    """本金管理控件。

    包含：
    - 当前交易所真实余额显示（只读）
    - 自定义本金滑块（1 ~ 100%）+ 输入框联动
    - 保存按钮

    Signals:
        capital_changed(float): 本金变更时发出，携带新的本金值
    """

    capital_changed = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None):
        """初始化本金管理控件。

        Args:
            parent: 父级控件
        """
        super().__init__(parent)
        self._real_balance: float = 0.0      # 交易所真实余额
        self._custom_capital: float = 10000.0  # 用户自定义本金
        self._config_path: Path = USER_CONFIG_FILE
        self._updating: bool = False  # 防止无限循环更新

        self._setup_ui()
        self._apply_styles()
        self._load_saved_capital()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建 UI 布局。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # ---- 标题行 ----
        title_font = QFont("Microsoft YaHei", 12, QFont.Weight.Bold)
        self._title_label = QLabel("💰 本金管理")
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet(f"color: {_COLOR_TEXT};")
        layout.addWidget(self._title_label)

        # ---- 交易所余额显示 ----
        bal_layout = QHBoxLayout()
        bal_layout.setSpacing(8)

        bal_label = QLabel("交易所余额：")
        bal_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 11pt;")
        bal_layout.addWidget(bal_label)

        self._balance_value = QLabel("—")
        self._balance_value.setStyleSheet(
            f"color: {_COLOR_GREEN}; font-size: 12pt; font-weight: bold;"
        )
        self._balance_value.setMinimumWidth(120)
        bal_layout.addWidget(self._balance_value)

        bal_layout.addStretch()
        layout.addLayout(bal_layout)

        # ---- 分隔线 ----
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {_COLOR_BORDER}; max-height: 1px;")
        layout.addWidget(sep)

        # ---- 自定义本金滑块 + 输入框 ----
        cap_row = QHBoxLayout()
        cap_row.setSpacing(10)

        cap_label = QLabel("自定义本金：")
        cap_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 11pt;")
        cap_row.addWidget(cap_label)

        # 滑块（0-100%，内部转为实际金额）
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(1, 100)  # 最低 1%
        self._slider.setValue(100)     # 默认 100%
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(10)
        self._slider.valueChanged.connect(self._on_slider_changed)
        cap_row.addWidget(self._slider, stretch=1)

        # 金额输入框
        self._capital_input = QLineEdit()
        self._capital_input.setPlaceholderText("输入金额...")
        self._capital_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._capital_input.setFixedWidth(130)
        self._capital_input.textChanged.connect(self._on_input_changed)
        self._capital_input.editingFinished.connect(self._on_input_done)
        cap_row.addWidget(self._capital_input)

        # USDT 单位标签
        unit_label = QLabel("USDT")
        unit_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 10pt;")
        cap_row.addWidget(unit_label)

        layout.addLayout(cap_row)

        # ---- 比例快捷按钮行 ----
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)

        quick_label = QLabel("快捷比例：")
        quick_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 10pt;")
        quick_row.addWidget(quick_label)

        for pct, label in [(25, "25%"), (50, "50%"), (75, "75%"), (100, "100%")]:
            from PyQt6.QtWidgets import QPushButton
            btn = QPushButton(label)
            btn.setFixedWidth(55)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, p=pct: self._set_percentage(p))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_COLOR_SLIDER_TRACK};
                    color: {_COLOR_TEXT};
                    border: 1px solid {_COLOR_BORDER};
                    border-radius: 4px;
                    font-size: 9pt;
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    background-color: {_COLOR_ACCENT};
                }}
            """)
            quick_row.addWidget(btn)

        quick_row.addStretch()
        layout.addLayout(quick_row)

        # ---- 警告提示区 ----
        self._warning_label = QLabel("")
        self._warning_label.setStyleSheet(
            f"color: #ff4757; font-size: 9pt; padding: 4px;"
        )
        self._warning_label.setWordWrap(True)
        self._warning_label.setVisible(False)
        layout.addWidget(self._warning_label)

        # ---- 当前生效本金 ----
        active_row = QHBoxLayout()
        active_row.setSpacing(8)

        active_label = QLabel("📊 当前生效本金：")
        active_label.setStyleSheet(f"color: {_COLOR_MUTED}; font-size: 10pt;")
        active_row.addWidget(active_label)

        self._active_capital_label = QLabel(f"{self._custom_capital:,.0f} USDT")
        self._active_capital_label.setStyleSheet(
            f"color: {_COLOR_BLUE}; font-size: 11pt; font-weight: bold;"
        )
        active_row.addWidget(self._active_capital_label)

        active_row.addStretch()
        layout.addLayout(active_row)

        layout.addStretch()

    # ------------------------------------------------------------------
    #  样式
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        """应用涩谷风样式。"""
        self.setStyleSheet(f"""
            CapitalManager {{
                background-color: {_COLOR_BG};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 12px;
            }}
            QSlider::groove:horizontal {{
                background: {_COLOR_SLIDER_TRACK};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {_COLOR_SLIDER_HANDLE};
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }}
            QSlider::sub-page:horizontal {{
                background: {_COLOR_ACCENT};
                border-radius: 3px;
            }}
            QLineEdit {{
                background-color: {_COLOR_SLIDER_TRACK};
                color: {_COLOR_TEXT};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11pt;
            }}
            QLineEdit:focus {{
                border-color: {_COLOR_BLUE};
            }}
        """)

    # ------------------------------------------------------------------
    #  核心逻辑
    # ------------------------------------------------------------------

    def set_real_balance(self, balance_usdt: float) -> None:
        """设置交易所真实余额（外部调用）。

        Args:
            balance_usdt: 交易所 USDT 余额
        """
        self._real_balance = max(0.0, balance_usdt)
        self._balance_value.setText(f"{self._real_balance:,.2f} USDT")

        # 如果真实余额为 0，禁用控件
        has_balance = self._real_balance > 0
        self._slider.setEnabled(has_balance)
        self._capital_input.setEnabled(has_balance)
        if not has_balance:
            self._warning_label.setText("⚠️ 交易所余额为 0，无法设置本金")
            self._warning_label.setVisible(True)
            return

        # 确保当前本金不超过新的真实余额
        if self._custom_capital > self._real_balance:
            self._custom_capital = self._real_balance
            self._update_ui_from_capital()

    def get_custom_capital(self) -> float:
        """获取当前用户自定义本金。

        Returns:
            float 本金金额
        """
        return self._custom_capital

    def _set_percentage(self, percentage: int) -> None:
        """快捷设置本金比例。

        Args:
            percentage: 百分比 (1-100)
        """
        if self._real_balance <= 0:
            return
        self._slider.setValue(percentage)

    def _on_slider_changed(self, value: int) -> None:
        """滑块值变更回调 —— 同步更新输入框。

        Args:
            value: 滑块值 (1-100)
        """
        if self._updating:
            return
        self._updating = True
        try:
            new_capital = self._real_balance * value / 100.0
            self._custom_capital = round(new_capital, 2)
            self._capital_input.setText(f"{new_capital:,.2f}")
            self._update_active_display()
        finally:
            self._updating = False

    def _on_input_changed(self, text: str) -> None:
        """输入框文本变更回调 —— 实时校验，但不立刻触发滑块更新。"""
        pass  # 实时显示不做校验，编辑完成时统一处理

    def _on_input_done(self) -> None:
        """输入框编辑完成回调 —— 校验并更新。

        硬编码防护：本金 ≤ 真实余额。
        """
        if self._updating:
            return
        self._updating = True
        try:
            text = self._capital_input.text().strip().replace(",", "")
            if not text:
                self._restore_previous_input()
                return

            try:
                value = float(text)
            except ValueError:
                self._warning_label.setText("⚠️ 请输入有效数字")
                self._warning_label.setVisible(True)
                self._restore_previous_input()
                return

            # 硬编码防护：本金不能超过真实余额
            if value > self._real_balance:
                self._warning_label.setText(
                    f"⚠️ 本金不能超过交易所余额（{self._real_balance:,.2f} USDT），已自动修正"
                )
                self._warning_label.setVisible(True)
                value = self._real_balance
            elif value < 0:
                self._warning_label.setText("⚠️ 本金不能为负数，已设置为 0")
                self._warning_label.setVisible(True)
                value = 0.0
            else:
                self._warning_label.setVisible(False)

            self._custom_capital = round(value, 2)

            # 同步滑块
            if self._real_balance > 0:
                pct = int(self._custom_capital / self._real_balance * 100)
                self._slider.setValue(max(1, min(100, pct)))

            self._update_active_display()
            self._save_capital()
            self.capital_changed.emit(self._custom_capital)
        finally:
            self._updating = False

    def _restore_previous_input(self) -> None:
        """恢复输入框到当前本金值。"""
        self._capital_input.setText(f"{self._custom_capital:,.2f}")

    def _update_active_display(self) -> None:
        """更新当前生效本金标签。"""
        self._active_capital_label.setText(f"{self._custom_capital:,.0f} USDT")
        self._capital_input.setText(f"{self._custom_capital:,.2f}")

    def _update_ui_from_capital(self) -> None:
        """从 _custom_capital 同步更新所有 UI 元素。"""
        self._capital_input.setText(f"{self._custom_capital:,.2f}")
        if self._real_balance > 0:
            pct = int(self._custom_capital / self._real_balance * 100)
            self._slider.setValue(max(1, min(100, pct)))
        self._update_active_display()

    # ------------------------------------------------------------------
    #  持久化
    # ------------------------------------------------------------------

    def _load_saved_capital(self) -> None:
        """从 user_config.json 加载已保存的本金。"""
        config = load_config(self._config_path)
        saved = config.get("custom_capital", None)
        if saved is not None:
            self._custom_capital = float(saved)
        # 如果还没连交易所，先在输入框显示已保存值
        self._capital_input.setText(f"{self._custom_capital:,.2f}")
        self._update_active_display()

    def _save_capital(self) -> None:
        """保存本金到 user_config.json。"""
        try:
            save_config(self._config_path, {"custom_capital": self._custom_capital})
        except Exception as exc:
            print(f"⚠️ 本金保存失败：{exc}")


# ============================================================================
#  自检入口
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = QMainWindow()
    window.setWindowTitle("本金管理 — 自检")
    window.setStyleSheet("background-color: #1a1a2e;")

    central = QWidget()
    layout = QVBoxLayout(central)

    mgr = CapitalManager()
    mgr.set_real_balance(10000.0)  # 模拟 10000 USDT 余额
    mgr.setFixedWidth(400)
    layout.addWidget(mgr)
    layout.addStretch()

    window.setCentralWidget(central)
    window.resize(450, 320)
    window.show()

    sys.exit(app.exec())
