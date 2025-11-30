import sys
import os
import json
import subprocess
import datetime
from typing import List, Dict, Any, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QFileDialog, QSplitter, QLineEdit, QProgressBar,
    QMessageBox, QInputDialog, QMenu, QAction, QDialog, QFormLayout,
    QDialogButtonBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QUrl

# --- Configuration Constants ---
DEFAULT_TIME_FIELD = "created_at"
PAGE_SIZE = 100  # Increased page size for better viewing experience


class FileScannerWorker(QThread):
    """
    Worker thread to load JSON file, count entries, and calculate time range.
    Does not block the UI.
    """
    finished_signal = pyqtSignal(str, dict)  # filepath, stats_dict
    error_signal = pyqtSignal(str, str)  # filepath, error_message

    def __init__(self, filepath: str, time_field: str):
        super().__init__()
        self.filepath = filepath
        self.time_field = time_field

    def run(self):
        try:
            file_size = os.path.getsize(self.filepath)

            # Streaming large JSONs is better, but for simplicity we load standard JSON arrays.
            # Assuming the export format is [ {...}, {...} ]
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                raise ValueError("JSON content is not a list (Array).")

            count = len(data)
            min_time = None
            max_time = None

            # Calculate time range
            if count > 0 and self.time_field:
                for doc in data:
                    val = self._get_nested_value(doc, self.time_field)

                    # Handle different date formats
                    dt = None
                    if val:
                        try:
                            # Case 1: Standard String "2023-01-01T12:00:00Z"
                            if isinstance(val, str):
                                # Fix potential ISO format issues
                                clean_val = val.replace('Z', '+00:00').replace(' ', 'T')
                                dt = datetime.datetime.fromisoformat(clean_val)

                            # Case 2: MongoDB Extended JSON {"$date": "..."} or {"$date": 1234567890}
                            elif isinstance(val, dict) and '$date' in val:
                                date_val = val['$date']
                                if isinstance(date_val, str):
                                    clean_val = date_val.replace('Z', '+00:00').replace(' ', 'T')
                                    dt = datetime.datetime.fromisoformat(clean_val)
                                elif isinstance(date_val, (int, float)):
                                    # timestamp in ms
                                    dt = datetime.datetime.fromtimestamp(date_val / 1000.0, datetime.timezone.utc)

                            # Case 3: Python datetime object (if loaded via specialized loader, unlikely here but possible)
                            elif isinstance(val, datetime.datetime):
                                dt = val

                            # Update Min/Max
                            if dt:
                                # Ensure timezone awareness for comparison if mixed
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=datetime.timezone.utc)

                                if min_time is None or dt < min_time:
                                    min_time = dt
                                if max_time is None or dt > max_time:
                                    max_time = dt

                        except (ValueError, TypeError) as e:
                            # print(f"Date parse error for {val}: {e}") # Debug
                            continue

            stats = {
                "size": file_size,
                "count": count,
                "start_time": min_time.isoformat() if min_time else "N/A",
                "end_time": max_time.isoformat() if max_time else "N/A",
                "data": data  # Keep data in memory for this session (careful with huge files)
            }
            self.finished_signal.emit(self.filepath, stats)

        except Exception as e:
            self.error_signal.emit(self.filepath, str(e))

    def _get_nested_value(self, doc: dict, path: str):
        keys = path.split('.')
        val = doc
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return None
        return val


class ImportWorker(QThread):
    """
    Worker thread to execute mongoimport subprocess.
    """
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, tool_path: str, uri: str, db: str, collection: str, filepaths: List[str]):
        super().__init__()
        self.tool_path = tool_path
        self.uri = uri
        self.db = db
        self.collection = collection
        self.filepaths = filepaths

    def run(self):
        # Determine executable name
        exe_name = "mongoimport.exe" if os.name == 'nt' else "mongoimport"
        # If tool_path is provided, prepend it
        cmd_base = os.path.join(self.tool_path, exe_name) if self.tool_path else exe_name

        for fp in self.filepaths:
            self.log_signal.emit(f"Importing {os.path.basename(fp)}...")

            # Construct command: mongoimport --uri "..." --db ... --collection ... --file ... --jsonArray
            # Using --upsert is often safer for re-imports
            args = [
                cmd_base,
                "--uri", self.uri,
                "--db", self.db,
                "--collection", self.collection,
                "--file", fp,
                "--jsonArray"
            ]

            try:
                # startupinfo to hide console window on Windows
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    startupinfo=startupinfo
                )
                stdout, stderr = process.communicate()

                if process.returncode == 0:
                    self.log_signal.emit(f"Success: {stderr.strip()}")  # mongoimport often logs to stderr
                else:
                    self.log_signal.emit(f"Error: {stderr.strip()}")

            except FileNotFoundError:
                self.finished_signal.emit(False, f"Executable not found: {cmd_base}")
                return
            except Exception as e:
                self.log_signal.emit(f"Exception: {str(e)}")

        self.finished_signal.emit(True, "Batch import completed.")


class SettingsDialog(QDialog):
    """
    Custom Settings Dialog with Browse functionality.
    """

    def __init__(self, parent=None, current_time_field="", current_mongo_path=""):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(500, 200)

        layout = QFormLayout(self)

        # Time Field
        self.edit_time = QLineEdit(current_time_field)
        self.edit_time.setPlaceholderText("e.g. created_at or meta.timestamp")
        layout.addRow("Time Field Name:", self.edit_time)

        # Mongo Tools Path
        path_layout = QHBoxLayout()
        self.edit_path = QLineEdit(current_mongo_path)
        self.edit_path.setPlaceholderText("Folder containing mongoimport executable")
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self.browse_path)
        path_layout.addWidget(self.edit_path)
        path_layout.addWidget(self.btn_browse)
        layout.addRow("MongoDB Tools Path:", path_layout)

        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def browse_path(self):
        directory = QFileDialog.getExistingDirectory(self, "Select MongoDB Tools Directory", self.edit_path.text())
        if directory:
            self.edit_path.setText(directory)

    def get_time_field(self):
        return self.edit_time.text().strip()

    def get_mongo_path(self):
        return self.edit_path.text().strip()


class FileDropTable(QTableWidget):
    """
    TableWidget that accepts file drops and delete key.
    """
    files_dropped = pyqtSignal(list)
    delete_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setAlternatingRowColors(True)
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["File Name", "Size (KB)", "Entries", "Start Time", "End Time"])
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Essential to override for consistent drop behavior on some OS."""
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        filepaths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path) and path.lower().endswith('.json'):
                filepaths.append(path)
        if filepaths:
            self.files_dropped.emit(filepaths)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_pressed.emit()
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MongoDB JSON Viewer & Importer")
        self.resize(1200, 700)
        self.settings = QSettings("MyCorp", "MongoViewer")

        # Internal data storage: filepath -> data list
        self.loaded_data = {}
        self.current_view_file = None
        self.current_page = 0

        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- Left Panel: File List & Controls ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # Toolbar
        tool_layout = QHBoxLayout()
        self.btn_add = QPushButton("Add Files")
        self.btn_import = QPushButton("Import Selected")
        self.btn_settings = QPushButton("Settings")
        tool_layout.addWidget(self.btn_add)
        tool_layout.addWidget(self.btn_import)
        tool_layout.addWidget(self.btn_settings)
        left_layout.addLayout(tool_layout)

        # File Table
        self.file_table = FileDropTable()
        left_layout.addWidget(self.file_table)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        splitter.addWidget(left_panel)

        # --- Right Panel: JSON Browser ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Info Header
        self.lbl_current_file = QLabel("No file selected")
        self.lbl_current_file.setStyleSheet("font-weight: bold; color: #555;")
        right_layout.addWidget(self.lbl_current_file)

        # Tree View (Fixed to show values)
        self.tree_view = QTreeWidget()
        self.tree_view.setColumnCount(2)
        self.tree_view.setHeaderLabels(["Key", "Value"])
        self.tree_view.setColumnWidth(0, 200)
        right_layout.addWidget(self.tree_view)

        # Pagination
        page_layout = QHBoxLayout()
        self.btn_first = QPushButton("<<")
        self.btn_prev = QPushButton("< Prev")
        self.lbl_page = QLabel("Page 0/0")
        self.btn_next = QPushButton("Next >")
        self.btn_last = QPushButton(">>")

        # Style buttons to be smaller
        for btn in [self.btn_first, self.btn_prev, self.btn_next, self.btn_last]:
            btn.setFixedWidth(60)

        self.btn_first.clicked.connect(lambda: self.change_page('first'))
        self.btn_prev.clicked.connect(lambda: self.change_page(-1))
        self.btn_next.clicked.connect(lambda: self.change_page(1))
        self.btn_last.clicked.connect(lambda: self.change_page('last'))

        page_layout.addStretch()
        page_layout.addWidget(self.btn_first)
        page_layout.addWidget(self.btn_prev)
        page_layout.addWidget(self.lbl_page)
        page_layout.addWidget(self.btn_next)
        page_layout.addWidget(self.btn_last)
        page_layout.addStretch()

        right_layout.addLayout(page_layout)

        splitter.addWidget(right_panel)
        splitter.setSizes([450, 750])

        # --- Signals ---
        self.file_table.files_dropped.connect(self.process_files)
        self.file_table.delete_pressed.connect(self.remove_selected_files)
        self.btn_add.clicked.connect(self.open_file_dialog)
        self.btn_settings.clicked.connect(self.show_settings_dialog)
        self.btn_import.clicked.connect(self.show_import_dialog)
        self.file_table.itemClicked.connect(self.on_file_selected)

    def _load_settings(self):
        self.time_field = self.settings.value("time_field", DEFAULT_TIME_FIELD)
        self.mongo_path = self.settings.value("mongo_path", "")

    # --- File Handling ---

    def open_file_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select JSON Files", "", "JSON Files (*.json)")
        if files:
            self.process_files(files)

    def process_files(self, filepaths):
        """Start worker threads to scan files."""
        existing_rows = self.file_table.rowCount()

        for fp in filepaths:
            # Avoid duplicates
            duplicate = False
            for i in range(existing_rows):
                if self.file_table.item(i, 0).data(Qt.UserRole) == fp:
                    duplicate = True
                    break
            if duplicate:
                continue

            row = self.file_table.rowCount()
            self.file_table.insertRow(row)

            # Placeholder items
            name_item = QTableWidgetItem(os.path.basename(fp))
            name_item.setData(Qt.UserRole, fp)  # Store full path
            self.file_table.setItem(row, 0, name_item)
            self.file_table.setItem(row, 1, QTableWidgetItem("Loading..."))

            # Start Worker
            self.start_scanner(fp)

    def start_scanner(self, filepath):
        """Helper to start scanner for a specific file."""
        worker = FileScannerWorker(filepath, self.time_field)
        worker.finished_signal.connect(self.on_scan_finished)
        worker.error_signal.connect(self.on_scan_error)
        worker.start()
        # Keep reference to avoid GC
        if not hasattr(self, 'workers'): self.workers = []
        self.workers.append(worker)

    def on_scan_finished(self, filepath, stats):
        # Find row
        row = -1
        for i in range(self.file_table.rowCount()):
            if self.file_table.item(i, 0).data(Qt.UserRole) == filepath:
                row = i
                break
        if row == -1: return

        # Update Table
        size_kb = f"{stats['size'] / 1024:.2f}"
        self.file_table.setItem(row, 1, QTableWidgetItem(size_kb))
        self.file_table.setItem(row, 2, QTableWidgetItem(str(stats['count'])))
        self.file_table.setItem(row, 3, QTableWidgetItem(stats['start_time']))
        self.file_table.setItem(row, 4, QTableWidgetItem(stats['end_time']))

        # Cache Data
        self.loaded_data[filepath] = stats['data']

        # If this file is currently being viewed, refresh the tree view to ensure data is consistent
        if self.current_view_file == filepath:
            self.render_tree_page()

        # Cleanup worker
        self.workers = [w for w in self.workers if w.isRunning()]

    def on_scan_error(self, filepath, err):
        # Find row
        for i in range(self.file_table.rowCount()):
            if self.file_table.item(i, 0).data(Qt.UserRole) == filepath:
                self.file_table.setItem(i, 1, QTableWidgetItem("Error"))
                self.file_table.setToolTip(err)
                break

    def remove_selected_files(self):
        """Remove selected files from table and memory."""
        selected_rows = sorted(set(item.row() for item in self.file_table.selectedItems()), reverse=True)

        if not selected_rows:
            return

        confirm = QMessageBox.question(self, "Delete Files", f"Remove {len(selected_rows)} file(s) from list?",
                                       QMessageBox.Yes | QMessageBox.No)

        if confirm == QMessageBox.Yes:
            for row in selected_rows:
                # Remove from memory
                filepath = self.file_table.item(row, 0).data(Qt.UserRole)
                if filepath in self.loaded_data:
                    del self.loaded_data[filepath]

                # If currently viewing this file, clear view
                if self.current_view_file == filepath:
                    self.current_view_file = None
                    self.tree_view.clear()
                    self.lbl_current_file.setText("No file selected")
                    self.lbl_page.setText("Page 0/0")

                # Remove from table
                self.file_table.removeRow(row)

    def refresh_file_stats(self):
        """Rescan all loaded files with new settings (e.g. time field)."""
        rows = self.file_table.rowCount()
        if rows == 0:
            return

        # Clear table stats columns temporarily to indicate loading
        for i in range(rows):
            filepath = self.file_table.item(i, 0).data(Qt.UserRole)
            self.file_table.setItem(i, 1, QTableWidgetItem("Reloading..."))
            self.file_table.setItem(i, 2, QTableWidgetItem(""))
            self.file_table.setItem(i, 3, QTableWidgetItem(""))
            self.file_table.setItem(i, 4, QTableWidgetItem(""))

            # Restart scanner
            self.start_scanner(filepath)

    # --- Data Viewing ---

    def on_file_selected(self, item):
        row = item.row()
        filepath = self.file_table.item(row, 0).data(Qt.UserRole)

        if filepath not in self.loaded_data:
            return

        self.current_view_file = filepath
        self.current_page = 0
        self.lbl_current_file.setText(f"Viewing: {os.path.basename(filepath)}")
        self.render_tree_page()

    def render_tree_page(self):
        if not self.current_view_file or self.current_view_file not in self.loaded_data:
            return

        data_list = self.loaded_data[self.current_view_file]
        total = len(data_list)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if total_pages == 0: total_pages = 1

        start_idx = self.current_page * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, total)

        page_data = data_list[start_idx:end_idx]

        self.tree_view.clear()

        # Populate Tree
        for idx, item in enumerate(page_data):
            root_item = QTreeWidgetItem(self.tree_view)
            root_item.setText(0, f"Record #{start_idx + idx + 1}")
            self.fill_item(root_item, item)

        self.lbl_page.setText(f"Page {self.current_page + 1}/{total_pages} (Total: {total})")

        # Button States
        self.btn_first.setEnabled(self.current_page > 0)
        self.btn_prev.setEnabled(self.current_page > 0)
        self.btn_next.setEnabled(self.current_page < total_pages - 1)
        self.btn_last.setEnabled(self.current_page < total_pages - 1)

    def fill_item(self, parent_item, value):
        """Recursive function to populate tree items"""
        if isinstance(value, dict):
            for key, val in value.items():
                child = QTreeWidgetItem(parent_item)
                child.setText(0, str(key))
                if isinstance(val, (dict, list)):
                    self.fill_item(child, val)
                else:
                    child.setText(1, str(val))
        elif isinstance(value, list):
            for i, val in enumerate(value):
                child = QTreeWidgetItem(parent_item)
                child.setText(0, f"[{i}]")
                if isinstance(val, (dict, list)):
                    self.fill_item(child, val)
                else:
                    child.setText(1, str(val))
        else:
            parent_item.setText(1, str(value))

    def change_page(self, action):
        if not self.current_view_file:
            return

        data_list = self.loaded_data[self.current_view_file]
        total = len(data_list)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

        if action == 'first':
            self.current_page = 0
        elif action == 'last':
            self.current_page = max(0, total_pages - 1)
        elif isinstance(action, int):
            self.current_page += action

        self.render_tree_page()

    # --- Import Logic ---

    def show_import_dialog(self):
        selected_rows = set(item.row() for item in self.file_table.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Warning", "Please select files to import.")
            return

        filepaths = [self.file_table.item(r, 0).data(Qt.UserRole) for r in selected_rows]

        # Simple Dialog for Connection Info
        dialog = QDialog(self)
        dialog.setWindowTitle("Import to MongoDB")
        layout = QFormLayout(dialog)

        edit_uri = QLineEdit("mongodb://localhost:27017")
        edit_db = QLineEdit("IntelligenceIntegrationSystem")
        edit_coll = QLineEdit("imported_data")

        layout.addRow("URI:", edit_uri)
        layout.addRow("Database:", edit_db)
        layout.addRow("Collection:", edit_coll)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Start Import")
        btn_ok.clicked.connect(dialog.accept)
        btn_box.addWidget(btn_ok)
        layout.addRow(btn_box)

        if dialog.exec_() == QDialog.Accepted:
            self.run_import(
                filepaths,
                edit_uri.text(),
                edit_db.text(),
                edit_coll.text()
            )

    def run_import(self, filepaths, uri, db, collection):
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate

        self.import_worker = ImportWorker(self.mongo_path, uri, db, collection, filepaths)
        self.import_worker.log_signal.connect(lambda msg: print(f"[Import] {msg}"))  # Could log to a text widget
        self.import_worker.finished_signal.connect(self.on_import_finished)
        self.import_worker.start()

    def on_import_finished(self, success, msg):
        self.progress_bar.setVisible(False)
        if success:
            QMessageBox.information(self, "Import Finished", msg)
        else:
            QMessageBox.critical(self, "Import Failed", msg)

    # --- Settings ---

    def show_settings_dialog(self):
        # Use custom dialog instead of simple input dialog
        dlg = SettingsDialog(self, self.time_field, self.mongo_path)
        if dlg.exec_() == QDialog.Accepted:
            new_time_field = dlg.get_time_field()
            new_mongo_path = dlg.get_mongo_path()

            # Check if settings changed
            time_field_changed = (new_time_field != self.time_field)

            # Update immediately
            self.time_field = new_time_field
            self.mongo_path = new_mongo_path

            # Persist
            self.settings.setValue("time_field", self.time_field)
            self.settings.setValue("mongo_path", self.mongo_path)

            # Refresh if necessary
            if time_field_changed:
                self.refresh_file_stats()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())