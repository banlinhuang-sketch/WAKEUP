"""PySide6 图形界面，负责配置编辑、任务控制和实时展示。"""

from __future__ import annotations

from pathlib import Path
import traceback

from .audio import (
    AudioDependencyError,
    AudioValidationError,
    create_audio_backend,
    format_output_device_label,
    normalize_output_device_selection,
)
from .config import default_config, load_config, save_config
from .dut import LogSourceError, list_adb_devices, list_serial_port_names
from .engine import EngineCallbacks, TestEngine
from .matching import parse_rules_text, rules_to_text
from .models import AppConfig, AudioDeviceConfig, DutConfig, ScenarioConfig, TimingConfig


try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover - depends on host environment
    raise RuntimeError("PySide6 is required for GUI mode.") from exc


class EngineWorker(QtCore.QObject):
    """运行在后台线程中的工作对象。"""
    status = QtCore.Signal(str)
    log_event = QtCore.Signal(object)
    trial_result = QtCore.Signal(object, object)
    progress = QtCore.Signal(object)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        config: AppConfig,
        mode: str,
        dry_run: bool = False,
        preview_asset: str = "",
        preview_device: str = "",
        preview_gain_db: float = 0.0,
    ):
        """记录本次后台任务所需参数。"""
        super().__init__()
        self._config = config
        self._mode = mode
        self._dry_run = dry_run
        self._preview_asset = preview_asset
        self._preview_device = preview_device
        self._preview_gain_db = preview_gain_db
        self._engine: TestEngine | None = None

    @QtCore.Slot()
    def run(self) -> None:
        """在线程内执行预检、试听或正式测试。"""
        try:
            self._engine = TestEngine(
                config=self._config,
                dry_run=self._dry_run,
                audio_backend=create_audio_backend(self._dry_run),
            )
            if self._mode == "run":
                summary = self._engine.run(
                    callbacks=EngineCallbacks(
                        on_status=self.status.emit,
                        on_log_event=self.log_event.emit,
                        on_trial_result=self.trial_result.emit,
                        on_progress=self.progress.emit,
                    )
                )
                self.done.emit({"mode": "run", "summary": summary})
                return
            if self._mode == "precheck":
                messages = self._engine.precheck()
                for message in messages:
                    self.status.emit(message)
                self.done.emit({"mode": "precheck", "messages": messages})
                return
            if self._mode == "preview":
                self.status.emit(f"试听: {self._preview_asset}")
                self._engine.preview_asset(self._preview_asset, self._preview_device, self._preview_gain_db)
                self.done.emit({"mode": "preview", "asset": self._preview_asset})
                return
            raise ValueError(f"Unsupported worker mode: {self._mode}")
        except Exception as exc:
            if isinstance(exc, (AudioValidationError, AudioDependencyError, LogSourceError, ValueError, FileNotFoundError)):
                message = str(exc)
            else:
                message = f"{exc}\n\n{traceback.format_exc()}"
            self.failed.emit(message)

    @QtCore.Slot()
    def request_stop(self) -> None:
        """接收来自主线程的停止请求。"""
        if self._engine is not None:
            self._engine.request_stop()


class MainWindow(QtWidgets.QMainWindow):
    """主操作台窗口。"""
    stop_requested = QtCore.Signal()

    def __init__(self, project_root: Path, initial_config: AppConfig | None = None, dry_run: bool = False):
        """初始化窗口、恢复配置并刷新设备列表。"""
        super().__init__()
        self.project_root = project_root
        self.persistence_path = self.project_root / "last_config.yaml"
        self._persistence_mtime_ns: int | None = None
        self._config_base_dir = self.project_root
        self.dry_run = dry_run
        self._task_thread: QtCore.QThread | None = None
        self._task_worker: EngineWorker | None = None

        self.setWindowTitle("智能眼镜语音唤醒率测试工具")
        self.resize(1480, 900)
        self._build_ui()

        self._config = initial_config or self._load_persisted_or_default()
        if self._config.base_dir:
            self._config_base_dir = Path(self._config.base_dir)
        self._load_config_into_ui(self._config)
        self._refresh_device_lists(show_errors=False)

    def _build_ui(self) -> None:
        """构建整套界面布局。"""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)

        # 左侧负责配置与操作，右侧负责日志、结果和统计。
        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        splitter.addWidget(left_panel)

        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 5)

        form_card = QtWidgets.QGroupBox("测试配置")
        form_layout = QtWidgets.QFormLayout(form_card)
        left_layout.addWidget(form_card)

        # 平台与设备配置区。
        self.platform_combo = QtWidgets.QComboBox()
        self.platform_combo.addItems(["rtos", "qualcomm"])
        self.platform_combo.currentTextChanged.connect(self._update_platform_visibility)
        form_layout.addRow("平台", self.platform_combo)

        audio_row = QtWidgets.QWidget()
        audio_layout = QtWidgets.QGridLayout(audio_row)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        self.mouth_device_combo = QtWidgets.QComboBox()
        self.mouth_device_combo.setEditable(True)
        self.mouth_device_combo.setToolTip("人工嘴建议优先选择有线输出，避免蓝牙延迟影响响应时延统计。")
        self.noise_device_combo = QtWidgets.QComboBox()
        self.noise_device_combo.setEditable(True)
        self.noise_device_combo.setToolTip(
            "蓝牙音箱可用于噪声播放，但请优先选择 Stereo/A2DP 输出，避开 Hands-Free/AG Audio。"
        )
        self.refresh_audio_button = QtWidgets.QPushButton("刷新音频/蓝牙设备")
        self.refresh_audio_button.setToolTip("蓝牙音箱重连、切换模式或新插入声卡后，请点击这里刷新设备列表。")
        self.refresh_audio_button.clicked.connect(lambda: self._refresh_audio_devices(show_errors=True))
        self.audio_hint_label = QtWidgets.QLabel(
            "提示：蓝牙音箱可用于噪声播放；请选择 Stereo/A2DP 输出，避开 Hands-Free/AG Audio。"
        )
        self.audio_hint_label.setWordWrap(True)
        audio_layout.addWidget(QtWidgets.QLabel("人工嘴"), 0, 0)
        audio_layout.addWidget(self.mouth_device_combo, 0, 1)
        audio_layout.addWidget(QtWidgets.QLabel("噪声音箱"), 1, 0)
        audio_layout.addWidget(self.noise_device_combo, 1, 1)
        audio_layout.addWidget(self.refresh_audio_button, 2, 0, 1, 2)
        audio_layout.addWidget(self.audio_hint_label, 3, 0, 1, 2)
        form_layout.addRow("输出设备", audio_row)

        self.allow_same_device_checkbox = QtWidgets.QCheckBox("允许人工嘴与噪声音箱使用同一设备")
        form_layout.addRow("", self.allow_same_device_checkbox)

        self.rtos_group = QtWidgets.QGroupBox("RTOS 串口")
        rtos_layout = QtWidgets.QGridLayout(self.rtos_group)
        # RTOS 和 Qualcomm 连接方式不同，因此分别维护独立配置区。
        self.serial_port_combo = QtWidgets.QComboBox()
        self.serial_port_combo.setEditable(True)
        self.refresh_serial_button = QtWidgets.QPushButton("刷新串口")
        self.refresh_serial_button.clicked.connect(lambda: self._refresh_serial_ports(show_errors=True))
        self.baudrate_spin = QtWidgets.QSpinBox()
        self.baudrate_spin.setRange(1200, 3000000)
        self.baudrate_spin.setValue(115200)
        rtos_layout.addWidget(QtWidgets.QLabel("串口"), 0, 0)
        rtos_layout.addWidget(self.serial_port_combo, 0, 1)
        rtos_layout.addWidget(self.refresh_serial_button, 0, 2)
        rtos_layout.addWidget(QtWidgets.QLabel("波特率"), 1, 0)
        rtos_layout.addWidget(self.baudrate_spin, 1, 1)
        form_layout.addRow(self.rtos_group)

        self.qualcomm_group = QtWidgets.QGroupBox("Qualcomm ADB")
        qualcomm_layout = QtWidgets.QGridLayout(self.qualcomm_group)
        self.adb_serial_combo = QtWidgets.QComboBox()
        self.adb_serial_combo.setEditable(True)
        self.refresh_adb_button = QtWidgets.QPushButton("刷新 ADB")
        self.refresh_adb_button.clicked.connect(lambda: self._refresh_adb_devices(show_errors=True))
        qualcomm_layout.addWidget(QtWidgets.QLabel("ADB 设备"), 0, 0)
        qualcomm_layout.addWidget(self.adb_serial_combo, 0, 1)
        qualcomm_layout.addWidget(self.refresh_adb_button, 0, 2)
        form_layout.addRow(self.qualcomm_group)

        self.rules_edit = QtWidgets.QPlainTextEdit()
        self.rules_edit.setPlaceholderText("每行一条规则，regex: 前缀表示正则")
        self.rules_edit.setFixedHeight(90)
        form_layout.addRow("匹配规则", self.rules_edit)

        # 时序参数决定预热、轮次节奏和成功窗口。
        timing_row = QtWidgets.QWidget()
        timing_layout = QtWidgets.QGridLayout(timing_row)
        timing_layout.setContentsMargins(0, 0, 0, 0)
        self.pre_noise_spin = QtWidgets.QSpinBox()
        self.pre_noise_spin.setRange(0, 60000)
        self.pre_noise_spin.setSuffix(" ms")
        self.trial_interval_spin = QtWidgets.QSpinBox()
        self.trial_interval_spin.setRange(0, 60000)
        self.trial_interval_spin.setSuffix(" ms")
        self.success_window_spin = QtWidgets.QSpinBox()
        self.success_window_spin.setRange(100, 60000)
        self.success_window_spin.setSuffix(" ms")
        timing_layout.addWidget(QtWidgets.QLabel("噪声预热"), 0, 0)
        timing_layout.addWidget(self.pre_noise_spin, 0, 1)
        timing_layout.addWidget(QtWidgets.QLabel("试次间隔"), 0, 2)
        timing_layout.addWidget(self.trial_interval_spin, 0, 3)
        timing_layout.addWidget(QtWidgets.QLabel("成功窗口"), 1, 0)
        timing_layout.addWidget(self.success_window_spin, 1, 1)
        form_layout.addRow("时序", timing_row)

        self.output_root_edit = QtWidgets.QLineEdit()
        self.output_root_browse = QtWidgets.QPushButton("选择目录")
        self.output_root_browse.clicked.connect(self._choose_output_root)
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_root_edit)
        output_layout.addWidget(self.output_root_browse)
        form_layout.addRow("输出目录", output_row)

        scenario_group = QtWidgets.QGroupBox("批量场景表")
        scenario_layout = QtWidgets.QVBoxLayout(scenario_group)
        # 场景表是批量回归的核心配置区域。
        self.scenario_table = QtWidgets.QTableWidget(0, 7)
        self.scenario_table.setHorizontalHeaderLabels(
            ["启用", "名称", "噪声文件", "噪声增益(dB)", "唤醒文件", "唤醒增益(dB)", "轮数"]
        )
        self.scenario_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.scenario_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Stretch)
        scenario_layout.addWidget(self.scenario_table)

        scenario_buttons = QtWidgets.QHBoxLayout()
        self.add_scenario_button = QtWidgets.QPushButton("添加场景")
        self.remove_scenario_button = QtWidgets.QPushButton("删除场景")
        self.browse_noise_button = QtWidgets.QPushButton("浏览噪声文件")
        self.browse_wakeup_button = QtWidgets.QPushButton("浏览唤醒文件")
        scenario_buttons.addWidget(self.add_scenario_button)
        scenario_buttons.addWidget(self.remove_scenario_button)
        scenario_buttons.addWidget(self.browse_noise_button)
        scenario_buttons.addWidget(self.browse_wakeup_button)
        scenario_buttons.addStretch(1)
        scenario_layout.addLayout(scenario_buttons)

        custom_trials_row = QtWidgets.QHBoxLayout()
        self.custom_trials_spin = QtWidgets.QSpinBox()
        self.custom_trials_spin.setRange(1, 10000)
        self.custom_trials_spin.setValue(10)
        self.custom_trials_spin.setSuffix(" 次")
        self.custom_trials_spin.setToolTip("快速设置场景试次，无需手动编辑表格中的“次数”列。")
        self.apply_selected_trials_button = QtWidgets.QPushButton("应用到选中场景")
        self.apply_all_trials_button = QtWidgets.QPushButton("应用到全部场景")
        custom_trials_row.addWidget(QtWidgets.QLabel("自定义次数"))
        custom_trials_row.addWidget(self.custom_trials_spin)
        custom_trials_row.addWidget(self.apply_selected_trials_button)
        custom_trials_row.addWidget(self.apply_all_trials_button)
        custom_trials_row.addStretch(1)
        scenario_layout.addLayout(custom_trials_row)
        left_layout.addWidget(scenario_group)

        self.add_scenario_button.clicked.connect(self._append_empty_scenario)
        self.remove_scenario_button.clicked.connect(self._remove_selected_scenario)
        self.browse_noise_button.clicked.connect(lambda: self._browse_scenario_file(column=2))
        self.browse_wakeup_button.clicked.connect(lambda: self._browse_scenario_file(column=4))
        self.apply_selected_trials_button.clicked.connect(self._apply_custom_trials_to_selected)
        self.apply_all_trials_button.clicked.connect(self._apply_custom_trials_to_all)
        self.scenario_table.itemSelectionChanged.connect(self._sync_custom_trials_from_selected_row)

        action_row = QtWidgets.QHBoxLayout()
        self.load_config_button = QtWidgets.QPushButton("加载配置")
        self.save_config_button = QtWidgets.QPushButton("保存配置")
        self.precheck_button = QtWidgets.QPushButton("预检")
        self.preview_wakeup_button = QtWidgets.QPushButton("试听唤醒词")
        self.preview_noise_button = QtWidgets.QPushButton("试听噪声")
        self.start_button = QtWidgets.QPushButton("开始")
        self.stop_button = QtWidgets.QPushButton("停止")
        self.stop_button.setEnabled(False)
        for button in [
            self.load_config_button,
            self.save_config_button,
            self.precheck_button,
            self.preview_wakeup_button,
            self.preview_noise_button,
            self.start_button,
            self.stop_button,
        ]:
            action_row.addWidget(button)
        left_layout.addLayout(action_row)
        left_layout.addStretch(1)

        # 右侧优先展示运行中的关键信息，方便测试同学观察。
        self.load_config_button.clicked.connect(self._load_config_from_file)
        self.save_config_button.clicked.connect(self._save_config_to_file)
        self.precheck_button.clicked.connect(lambda: self._start_worker("precheck"))
        self.preview_wakeup_button.clicked.connect(lambda: self._start_preview(preview_noise=False))
        self.preview_noise_button.clicked.connect(lambda: self._start_preview(preview_noise=True))
        self.start_button.clicked.connect(lambda: self._start_worker("run"))
        self.stop_button.clicked.connect(self._stop_current_task)

        summary_group = QtWidgets.QGroupBox("实时概览")
        summary_layout = QtWidgets.QGridLayout(summary_group)
        self.status_label = QtWidgets.QLabel("就绪")
        self.run_dir_label = QtWidgets.QLabel("-")
        self.success_rate_label = QtWidgets.QLabel("成功率: -")
        self.latency_label = QtWidgets.QLabel("时延: -")
        self.progress_bar = QtWidgets.QProgressBar()
        summary_layout.addWidget(QtWidgets.QLabel("状态"), 0, 0)
        summary_layout.addWidget(self.status_label, 0, 1)
        summary_layout.addWidget(QtWidgets.QLabel("报告目录"), 1, 0)
        summary_layout.addWidget(self.run_dir_label, 1, 1)
        summary_layout.addWidget(self.success_rate_label, 2, 0, 1, 2)
        summary_layout.addWidget(self.latency_label, 3, 0, 1, 2)
        summary_layout.addWidget(self.progress_bar, 4, 0, 1, 2)
        right_layout.addWidget(summary_group)

        log_group = QtWidgets.QGroupBox("实时日志")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output)
        right_layout.addWidget(log_group, 3)

        result_group = QtWidgets.QGroupBox("试次结果")
        result_layout = QtWidgets.QVBoxLayout(result_group)
        self.result_table = QtWidgets.QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(["场景", "试次", "状态", "时延(ms)", "原因"])
        result_header = self.result_table.horizontalHeader()
        for column in range(5):
            result_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Stretch)
        result_layout.addWidget(self.result_table)
        right_layout.addWidget(result_group, 2)

    def _load_persisted_or_default(self) -> AppConfig:
        """优先恢复上次配置，失败时回退到默认配置。"""
        if self.persistence_path.exists():
            try:
                config = load_config(self.persistence_path)
                self._config_base_dir = self.persistence_path.parent
                self._normalize_legacy_placeholder_paths(config)
                self._record_persistence_snapshot()
                return config
            except Exception:
                pass
        self._record_persistence_snapshot()
        return default_config(base_dir=self.project_root)

    def _record_persistence_snapshot(self) -> None:
        """记录 last_config.yaml 当前时间戳，供运行前检测外部修改。"""
        try:
            self._persistence_mtime_ns = self.persistence_path.stat().st_mtime_ns
        except FileNotFoundError:
            self._persistence_mtime_ns = None

    def _has_external_persistence_update(self) -> bool:
        """判断 last_config.yaml 是否在窗口打开期间被外部更新。"""
        try:
            current_mtime_ns = self.persistence_path.stat().st_mtime_ns
        except FileNotFoundError:
            return self._persistence_mtime_ns is not None
        return self._persistence_mtime_ns is not None and current_mtime_ns != self._persistence_mtime_ns

    def _maybe_reload_external_persistence(self) -> bool:
        """运行前发现 last_config.yaml 有外部更新时，提示是否重载。"""
        if not self._has_external_persistence_update():
            return True

        answer = QtWidgets.QMessageBox.question(
            self,
            "检测到配置已更新",
            (
                "检测到 last_config.yaml 已在外部被修改。\n\n"
                "选择“是”将重新加载磁盘中的最新配置；\n"
                "选择“否”则继续使用当前界面内容，并在运行时覆盖磁盘文件。"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return True

        try:
            config = load_config(self.persistence_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "重新加载配置失败", str(exc))
            return False

        self._config_base_dir = self.persistence_path.parent
        self._normalize_legacy_placeholder_paths(config)
        self._config = config
        self._load_config_into_ui(config)
        self._refresh_audio_devices(show_errors=False)
        self._record_persistence_snapshot()
        self.status_label.setText(f"已重新加载 {self.persistence_path}")
        return True

    def _normalize_legacy_placeholder_paths(self, config: AppConfig) -> None:
        """清理旧版本遗留的占位音频路径，避免首次预检直接报错。"""
        placeholders = {"noise.wav", "wakeup.wav"}
        for scenario in config.scenarios:
            noise_path = scenario.noise_file.strip()
            wakeup_path = scenario.wakeup_file.strip()
            if noise_path in placeholders and not (self._config_base_dir / noise_path).exists():
                scenario.noise_file = ""
            if wakeup_path in placeholders and not (self._config_base_dir / wakeup_path).exists():
                scenario.wakeup_file = ""

    def _sync_combo_items(self, combo: QtWidgets.QComboBox, values: list[str]) -> None:
        """刷新下拉框内容时尽量保留当前用户输入。"""
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        if current and current not in values:
            combo.addItem(current)
        combo.setCurrentText(current)
        combo.blockSignals(False)

    def _selected_audio_device_value(self, combo: QtWidgets.QComboBox) -> str:
        """返回适合写入配置文件的稳定音频设备名。"""
        current_index = combo.currentIndex()
        if current_index >= 0 and combo.currentText() == combo.itemText(current_index):
            data = combo.itemData(current_index)
            if isinstance(data, str) and data.strip():
                return data.strip()
        return normalize_output_device_selection(combo.currentText())

    def _set_audio_combo_value(self, combo: QtWidgets.QComboBox, value: str) -> None:
        """按设备名回填到下拉框，尽量匹配现有枚举项。"""
        target = normalize_output_device_selection(value)
        if not target:
            combo.setCurrentText("")
            return
        for index in range(combo.count()):
            item_data = combo.itemData(index)
            if isinstance(item_data, str) and item_data.casefold() == target.casefold():
                combo.setCurrentIndex(index)
                return
            if combo.itemText(index).casefold() == target.casefold():
                combo.setCurrentIndex(index)
                return
        combo.setCurrentText(target)

    def _sync_audio_device_combo(self, combo: QtWidgets.QComboBox, devices: list[dict]) -> None:
        """同步音频设备下拉框，显示友好标签但保存稳定设备名。"""
        current_value = self._selected_audio_device_value(combo)
        combo.blockSignals(True)
        combo.clear()
        for device in devices:
            combo.addItem(format_output_device_label(device), str(device.get("name", "")))
        self._set_audio_combo_value(combo, current_value)
        combo.blockSignals(False)

    def _update_audio_device_hint(self, devices: list[dict]) -> None:
        """根据当前枚举结果给出更明确的蓝牙/通话模式提示。"""
        bluetooth_count = sum(1 for device in devices if device.get("is_bluetooth"))
        handsfree_count = sum(1 for device in devices if device.get("is_handsfree"))
        if handsfree_count:
            self.audio_hint_label.setText(
                "提示：已检测到 Hands-Free/AG Audio 通话模式输出。做音频测试时请优先选择 Stereo/A2DP，"
                "不要选 Hands-Free。蓝牙设备重连后如列表变化，请点击“刷新音频/蓝牙设备”。"
            )
            return
        if bluetooth_count:
            self.audio_hint_label.setText(
                "提示：已检测到蓝牙音频设备。噪声音箱可以使用蓝牙立体声输出；人工嘴仍建议使用有线输出。"
            )
            return
        self.audio_hint_label.setText(
            "提示：蓝牙音箱可用于噪声播放；请选择 Stereo/A2DP 输出，避开 Hands-Free/AG Audio。"
        )

    def _refresh_audio_devices(self, show_errors: bool) -> None:
        """刷新音频输出设备列表。"""
        try:
            devices = create_audio_backend(self.dry_run).list_output_devices()
        except (AudioDependencyError, RuntimeError) as exc:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, "音频设备", str(exc))
            return
        if not devices and self.dry_run:
            devices = create_audio_backend(self.dry_run).list_output_devices()
        self._sync_audio_device_combo(self.mouth_device_combo, devices)
        self._sync_audio_device_combo(self.noise_device_combo, devices)
        self._update_audio_device_hint(devices)
        if show_errors:
            self.status_label.setText("音频/蓝牙设备列表已刷新")

    def _refresh_serial_ports(self, show_errors: bool) -> None:
        """刷新串口设备列表。"""
        try:
            ports = list_serial_port_names()
        except LogSourceError as exc:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, "串口", str(exc))
            return
        self._sync_combo_items(self.serial_port_combo, ports)

    def _refresh_adb_devices(self, show_errors: bool) -> None:
        """刷新 adb 设备列表。"""
        try:
            devices = [device.serial for device in list_adb_devices()]
        except LogSourceError as exc:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, "ADB", str(exc))
            return
        self._sync_combo_items(self.adb_serial_combo, devices)

    def _refresh_device_lists(self, show_errors: bool) -> None:
        """一次性刷新所有可枚举的外设列表。"""
        self._refresh_audio_devices(show_errors=show_errors)
        self._refresh_serial_ports(show_errors=show_errors)
        self._refresh_adb_devices(show_errors=show_errors)

    def _update_platform_visibility(self, *_args) -> None:
        """按当前平台只显示对应的连接配置区。"""
        platform = self.platform_combo.currentText()
        self.rtos_group.setVisible(platform == "rtos")
        self.qualcomm_group.setVisible(platform == "qualcomm")

    def _append_empty_scenario(self) -> None:
        """在场景表追加一条空白场景。"""
        self._append_scenario_row(
            ScenarioConfig(
                name=f"scene_{self.scenario_table.rowCount() + 1}",
                noise_file="",
                wakeup_file="",
                trials=self.custom_trials_spin.value(),
            )
        )

    def _append_scenario_row(self, scenario: ScenarioConfig) -> None:
        """把场景对象渲染成表格的一行。"""
        row = self.scenario_table.rowCount()
        self.scenario_table.insertRow(row)

        enabled_item = QtWidgets.QTableWidgetItem()
        enabled_item.setFlags(enabled_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        enabled_item.setCheckState(
            QtCore.Qt.CheckState.Checked if scenario.enabled else QtCore.Qt.CheckState.Unchecked
        )
        self.scenario_table.setItem(row, 0, enabled_item)
        self.scenario_table.setItem(row, 1, QtWidgets.QTableWidgetItem(scenario.name))
        self.scenario_table.setItem(row, 2, QtWidgets.QTableWidgetItem(scenario.noise_file))
        self.scenario_table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(scenario.noise_gain_db)))
        self.scenario_table.setItem(row, 4, QtWidgets.QTableWidgetItem(scenario.wakeup_file))
        self.scenario_table.setItem(row, 5, QtWidgets.QTableWidgetItem(str(scenario.wakeup_gain_db)))
        self.scenario_table.setItem(row, 6, QtWidgets.QTableWidgetItem(str(scenario.trials)))
        self.scenario_table.selectRow(row)
        self._sync_custom_trials_from_selected_row()

    def _selected_scenario_row(self) -> int:
        """返回当前选中的场景行号；只有一行时默认落到首行。"""
        row = self.scenario_table.currentRow()
        if row >= 0:
            return row
        if self.scenario_table.rowCount() == 1:
            return 0
        return -1

    def _set_scenario_trials(self, row: int, trials: int) -> None:
        """更新指定场景行的试次数字。"""
        item = self.scenario_table.item(row, 6)
        if item is None:
            item = QtWidgets.QTableWidgetItem(str(trials))
            self.scenario_table.setItem(row, 6, item)
            return
        item.setText(str(trials))

    def _sync_custom_trials_from_selected_row(self) -> None:
        """把选中场景的试次回填到自定义次数输入框。"""
        row = self._selected_scenario_row()
        if row < 0:
            return
        item = self.scenario_table.item(row, 6)
        if item is None:
            return
        try:
            trials = int(item.text().strip())
        except ValueError:
            return
        if trials <= 0:
            return
        self.custom_trials_spin.blockSignals(True)
        self.custom_trials_spin.setValue(trials)
        self.custom_trials_spin.blockSignals(False)

    def _apply_custom_trials_to_selected(self) -> None:
        """将自定义次数应用到当前选中的场景。"""
        row = self._selected_scenario_row()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "自定义次数", "请先选中一条场景。")
            return
        trials = self.custom_trials_spin.value()
        self._set_scenario_trials(row, trials)
        self.scenario_table.selectRow(row)
        self.status_label.setText(f"已将试次设置为 {trials} 次")

    def _apply_custom_trials_to_all(self) -> None:
        """将自定义次数批量应用到全部场景。"""
        row_count = self.scenario_table.rowCount()
        if row_count == 0:
            QtWidgets.QMessageBox.information(self, "自定义次数", "请先添加至少一条场景。")
            return
        trials = self.custom_trials_spin.value()
        for row in range(row_count):
            self._set_scenario_trials(row, trials)
        self._sync_custom_trials_from_selected_row()
        self.status_label.setText(f"已将全部场景试次设置为 {trials} 次")

    def _remove_selected_scenario(self) -> None:
        """删除当前选中的场景行。"""
        row = self.scenario_table.currentRow()
        if row >= 0:
            self.scenario_table.removeRow(row)
            self._sync_custom_trials_from_selected_row()

    def _browse_scenario_file(self, column: int) -> None:
        """为当前行选择噪声或唤醒词 WAV 文件。"""
        row = self.scenario_table.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "选择场景", "请先选中一条场景。")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 WAV 文件", str(self.project_root), "WAV Files (*.wav)")
        if path:
            self.scenario_table.setItem(row, column, QtWidgets.QTableWidgetItem(path))

    def _choose_output_root(self) -> None:
        """选择报告输出根目录。"""
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择报告输出根目录", str(self.project_root))
        if directory:
            self.output_root_edit.setText(directory)

    def _config_from_ui(self) -> AppConfig:
        """把当前界面内容收敛为配置对象。"""
        scenarios: list[ScenarioConfig] = []
        for row in range(self.scenario_table.rowCount()):
            enabled = self.scenario_table.item(row, 0).checkState() == QtCore.Qt.CheckState.Checked
            name = (self.scenario_table.item(row, 1).text() if self.scenario_table.item(row, 1) else "").strip()
            noise_file = (self.scenario_table.item(row, 2).text() if self.scenario_table.item(row, 2) else "").strip()
            noise_gain = float((self.scenario_table.item(row, 3).text() if self.scenario_table.item(row, 3) else "0").strip() or 0)
            wakeup_file = (self.scenario_table.item(row, 4).text() if self.scenario_table.item(row, 4) else "").strip()
            wakeup_gain = float((self.scenario_table.item(row, 5).text() if self.scenario_table.item(row, 5) else "0").strip() or 0)
            trials = int((self.scenario_table.item(row, 6).text() if self.scenario_table.item(row, 6) else "0").strip() or 0)
            scenarios.append(
                ScenarioConfig(
                    name=name or f"scene_{row + 1}",
                    noise_file=noise_file,
                    noise_gain_db=noise_gain,
                    wakeup_file=wakeup_file,
                    wakeup_gain_db=wakeup_gain,
                    trials=trials,
                    enabled=enabled,
                )
            )

        rules = parse_rules_text(self.rules_edit.toPlainText())
        if not rules:
            # 如果文本框为空，则按当前平台回退到默认匹配规则。
            rules = default_config(self.platform_combo.currentText()).match_rules

        config = AppConfig(
            platform=self.platform_combo.currentText(),
            dut=DutConfig(
                serial_port=self.serial_port_combo.currentText().strip(),
                baudrate=self.baudrate_spin.value(),
                adb_serial=self.adb_serial_combo.currentText().strip(),
            ),
            audio_devices=AudioDeviceConfig(
                mouth_output=self._selected_audio_device_value(self.mouth_device_combo),
                noise_output=self._selected_audio_device_value(self.noise_device_combo),
            ),
            match_rules=rules,
            timing=TimingConfig(
                pre_noise_roll_ms=self.pre_noise_spin.value(),
                trial_interval_ms=self.trial_interval_spin.value(),
                success_window_ms=self.success_window_spin.value(),
            ),
            scenarios=scenarios,
            allow_same_device=self.allow_same_device_checkbox.isChecked(),
            output_root=self.output_root_edit.text().strip(),
            base_dir=str(self._config_base_dir),
        )
        config.validate()
        return config

    def _load_config_into_ui(self, config: AppConfig) -> None:
        """把配置对象回填到界面控件。"""
        self.platform_combo.setCurrentText(config.platform)
        self._set_audio_combo_value(self.mouth_device_combo, config.audio_devices.mouth_output)
        self._set_audio_combo_value(self.noise_device_combo, config.audio_devices.noise_output)
        self.serial_port_combo.setCurrentText(config.dut.serial_port)
        self.baudrate_spin.setValue(config.dut.baudrate)
        self.adb_serial_combo.setCurrentText(config.dut.adb_serial)
        self.rules_edit.setPlainText(rules_to_text(config.match_rules))
        self.pre_noise_spin.setValue(config.timing.pre_noise_roll_ms)
        self.trial_interval_spin.setValue(config.timing.trial_interval_ms)
        self.success_window_spin.setValue(config.timing.success_window_ms)
        self.allow_same_device_checkbox.setChecked(config.allow_same_device)
        self.output_root_edit.setText(config.output_root)
        self.scenario_table.setRowCount(0)
        for scenario in config.scenarios:
            self._append_scenario_row(scenario)
        if self.scenario_table.rowCount() > 0:
            self.scenario_table.selectRow(0)
            self._sync_custom_trials_from_selected_row()
        self._update_platform_visibility()

    def _load_config_from_file(self) -> None:
        """从用户选择的 YAML 文件加载配置。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "加载 YAML 配置", str(self.project_root), "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            config = load_config(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "加载配置失败", str(exc))
            return
        self._config_base_dir = Path(path).parent
        self._config = config
        self._load_config_into_ui(config)
        self._refresh_audio_devices(show_errors=False)
        if Path(path) == self.persistence_path:
            self._record_persistence_snapshot()
        self.status_label.setText(f"已加载 {path}")

    def _save_config_to_file(self) -> None:
        """把当前界面配置另存为 YAML 文件。"""
        try:
            config = self._config_from_ui()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "配置无效", str(exc))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存 YAML 配置", str(self.project_root / "config.yaml"), "YAML (*.yaml *.yml)")
        if not path:
            return
        self._config_base_dir = Path(path).parent
        config.base_dir = str(self._config_base_dir)
        save_config(path, config)
        self.status_label.setText(f"已保存 {path}")

    def _persist_current_config(self) -> None:
        """静默保存最近一次配置，方便下次启动恢复。"""
        try:
            save_config(self.persistence_path, self._config_from_ui())
            self._record_persistence_snapshot()
        except Exception:
            return

    def _set_running_state(self, running: bool) -> None:
        """根据任务执行状态启用或禁用相关按钮。"""
        self.start_button.setEnabled(not running)
        self.precheck_button.setEnabled(not running)
        self.preview_wakeup_button.setEnabled(not running)
        self.preview_noise_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _start_preview(self, preview_noise: bool) -> None:
        """启动噪声或唤醒词试听。"""
        row = self.scenario_table.currentRow()
        if row < 0:
            row = 0
        if self.scenario_table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "试听", "请先添加一条场景。")
            return
        file_column = 2 if preview_noise else 4
        gain_column = 3 if preview_noise else 5
        device = (
            self._selected_audio_device_value(self.noise_device_combo)
            if preview_noise
            else self._selected_audio_device_value(self.mouth_device_combo)
        )
        asset_path = (self.scenario_table.item(row, file_column).text() if self.scenario_table.item(row, file_column) else "").strip()
        gain_text = (self.scenario_table.item(row, gain_column).text() if self.scenario_table.item(row, gain_column) else "0").strip() or "0"
        if not asset_path:
            QtWidgets.QMessageBox.information(self, "试听", "请先填写对应的 WAV 文件路径。")
            return
        self._start_worker("preview", preview_asset=asset_path, preview_device=device, preview_gain_db=float(gain_text))

    def _start_worker(
        self,
        mode: str,
        preview_asset: str = "",
        preview_device: str = "",
        preview_gain_db: float = 0.0,
    ) -> None:
        """创建后台线程并启动指定任务。"""
        if self._task_thread is not None:
            QtWidgets.QMessageBox.information(self, "忙碌中", "当前已有任务在执行。")
            return
        if not self._maybe_reload_external_persistence():
            return
        try:
            config = self._config_from_ui()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "配置无效", str(exc))
            return

        self._persist_current_config()
        self._config = config
        if mode == "run":
            # 正式运行前先清掉上一轮残留展示。
            self.log_output.clear()
            self.result_table.setRowCount(0)
            self.progress_bar.setValue(0)
            self.run_dir_label.setText("-")
            self.success_rate_label.setText("成功率: -")
            self.latency_label.setText("时延: -")

        worker = EngineWorker(
            config=config,
            mode=mode,
            dry_run=self.dry_run,
            preview_asset=preview_asset,
            preview_device=preview_device,
            preview_gain_db=preview_gain_db,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        # 所有 UI 更新都通过 signal 回到主线程，避免跨线程操作控件。
        worker.status.connect(self._append_status)
        worker.log_event.connect(self._append_log_event)
        worker.trial_result.connect(self._append_trial_result)
        worker.progress.connect(self._update_progress)
        worker.done.connect(self._handle_worker_done)
        worker.done.connect(thread.quit)
        worker.failed.connect(self._handle_worker_failed)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._cleanup_worker(thread))
        self.stop_requested.connect(worker.request_stop)

        self._task_thread = thread
        self._task_worker = worker
        self._set_running_state(True)
        thread.start()

    def _cleanup_worker(self, thread: QtCore.QThread) -> None:
        """任务结束后回收线程与 worker 引用。"""
        if self._task_thread is thread:
            self._task_thread = None
            self._task_worker = None
            self._set_running_state(False)
        try:
            self.stop_requested.disconnect()
        except Exception:
            pass

    def _stop_current_task(self) -> None:
        """向后台线程发送停止信号。"""
        self.stop_requested.emit()
        self.status_label.setText("停止请求已发送")

    def _append_status(self, message: str) -> None:
        """在状态栏和日志框中追加状态文本。"""
        self.status_label.setText(message)
        self.log_output.appendPlainText(f"[status] {message}")

    def _append_log_event(self, event) -> None:
        """把日志事件追加到界面日志窗口。"""
        prefix = "[match]" if event.matched else "[log]"
        self.log_output.appendPlainText(f"{prefix} {event.source}: {event.raw_line}")

    def _append_trial_result(self, result, summary) -> None:
        """把单轮结果追加到结果表，并刷新顶部统计。"""
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QtWidgets.QTableWidgetItem(result.scenario_name))
        self.result_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(result.trial_index)))
        self.result_table.setItem(row, 2, QtWidgets.QTableWidgetItem(result.status))
        self.result_table.setItem(
            row,
            3,
            QtWidgets.QTableWidgetItem("" if result.latency_ms is None else f"{result.latency_ms:.3f}"),
        )
        self.result_table.setItem(row, 4, QtWidgets.QTableWidgetItem(result.failure_reason))
        overall = summary.get("overall", {})
        latency = overall.get("latency_ms", {})
        self.success_rate_label.setText(f"成功率: {overall.get('success_rate', 0.0)}%")
        self.latency_label.setText(
            "时延: "
            f"avg={latency.get('avg')} ms, median={latency.get('median')} ms, p95={latency.get('p95')} ms"
        )

    def _update_progress(self, payload) -> None:
        """根据已完成轮次刷新进度条。"""
        total = max(int(payload.get("total_trials", 0)), 1)
        completed = int(payload.get("completed_trials", 0))
        self.progress_bar.setValue(int(completed / total * 100))

    def _handle_worker_done(self, payload) -> None:
        """处理后台任务成功结束后的界面收尾。"""
        mode = payload.get("mode")
        if mode == "run":
            summary = payload["summary"]
            self.run_dir_label.setText(summary.get("run_dir", "-"))
            self.status_label.setText("测试完成")
            self.log_output.appendPlainText(f"[done] 报告输出目录: {summary.get('run_dir', '-')}")
            return
        if mode == "precheck":
            self.status_label.setText("预检通过")
            QtWidgets.QMessageBox.information(self, "预检通过", "\n".join(payload.get("messages", [])))
            return
        if mode == "preview":
            self.status_label.setText("试听完成")
            self.log_output.appendPlainText(f"[preview] 已完成试听: {payload.get('asset')}")

    def _handle_worker_failed(self, message: str) -> None:
        """处理后台任务异常，并弹出错误对话框。"""
        self.status_label.setText("任务失败")
        self.log_output.appendPlainText(f"[error] {message}")
        QtWidgets.QMessageBox.critical(self, "任务失败", message)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """关闭窗口时尽量保存配置，并通知后台停止。"""
        self._persist_current_config()
        if self._task_worker is not None:
            self.stop_requested.emit()
        super().closeEvent(event)


def launch_gui(initial_config: AppConfig | None = None, dry_run: bool = False, project_root: Path | None = None) -> int:
    """GUI 启动入口。"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    root = project_root or Path.cwd()
    window = MainWindow(project_root=root, initial_config=initial_config, dry_run=dry_run)
    window.show()
    return app.exec()
