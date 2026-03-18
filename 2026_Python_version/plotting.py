from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PyQt5 import QtWidgets

from caen_interface import CHANNEL_LABELS, FieldConfig, RunPointRecord

CHANNEL_COLORS = {
    "C": "#d9534f",
    "T1": "#f0ad4e",
    "B1": "#0275d8",
    "T2": "#5cb85c",
}


@dataclass
class PlotBundle:
    plot_widget: pg.PlotWidget
    curves: dict[str, pg.PlotDataItem]
    x_data: dict[str, list[float]]
    y_data: dict[str, list[float]]


class ScanPlotTabs(QtWidgets.QTabWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._bundles: dict[str, PlotBundle] = {}

    def reset_tabs(self, field_configs: list[FieldConfig] | tuple[FieldConfig, ...]) -> None:
        while self.count():
            self.removeTab(0)
        self._bundles.clear()

        for field_config in field_configs:
            plot_widget = pg.PlotWidget()
            plot_widget.showGrid(x=True, y=True, alpha=0.2)
            plot_widget.setLabel("bottom", "THGEM1 voltage [V]")
            plot_widget.setLabel("left", "Current [nA]")
            plot_widget.addLegend(offset=(10, 10))

            curves: dict[str, pg.PlotDataItem] = {}
            x_data = {label: [] for label in CHANNEL_LABELS}
            y_data = {label: [] for label in CHANNEL_LABELS}
            for label in CHANNEL_LABELS:
                curves[label] = plot_widget.plot(
                    [],
                    [],
                    name=label,
                    pen=pg.mkPen(CHANNEL_COLORS[label], width=2),
                    symbol="o",
                    symbolSize=6,
                    symbolBrush=CHANNEL_COLORS[label],
                )

            bundle = PlotBundle(plot_widget=plot_widget, curves=curves, x_data=x_data, y_data=y_data)
            self._bundles[field_config.label] = bundle

            container = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(6, 6, 6, 6)
            subtitle = QtWidgets.QLabel(
                f"UV expected: {'ON' if field_config.uv_expected else 'OFF'} | "
                f"Edrift={field_config.e_drift_kv_cm:+.1f} kV/cm | "
                f"Etransfer={field_config.e_transfer_kv_cm:+.1f} kV/cm"
            )
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)
            layout.addWidget(plot_widget)
            self.addTab(container, field_config.label)

    def activate_subscan(self, subscan_label: str) -> None:
        for index in range(self.count()):
            if self.tabText(index) == subscan_label:
                self.setCurrentIndex(index)
                return

    def append_record(self, record: RunPointRecord) -> None:
        bundle = self._bundles.get(record.subscan_label)
        if bundle is None:
            return

        for label, snapshot in record.channel_snapshots().items():
            bundle.x_data[label].append(record.v_thgem1_v)
            bundle.y_data[label].append(snapshot.imon_na)
            bundle.curves[label].setData(bundle.x_data[label], bundle.y_data[label])
