from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from caen_interface import RunPointRecord


class DataLogger:
    def __init__(self, output_directory: str | Path) -> None:
        self.output_directory = Path(output_directory)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._writer = None
        self._current_path: Path | None = None

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def open_run(self, mode: str) -> Path:
        self.close()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_mode = mode.lower().replace(" ", "_")
        self._current_path = self.output_directory / f"{timestamp}_{safe_mode}.csv"
        self._file = self._current_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=RunPointRecord.csv_fieldnames())
        self._writer.writeheader()
        self._file.flush()
        return self._current_path

    def write_record(self, record: RunPointRecord) -> None:
        if self._writer is None or self._file is None:
            raise RuntimeError("No active CSV file. Call open_run() before write_record().")
        self._writer.writerow(record.to_csv_row())
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None
