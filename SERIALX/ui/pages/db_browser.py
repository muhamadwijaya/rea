"""Database Browser (§3.8) — lazy fetch LIMIT/OFFSET, filter, query builder, raw SQL."""
from __future__ import annotations

import json
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableView, QTextEdit,
                               QVBoxLayout, QWidget)

from ui import db
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class LazySerialModel(QAbstractTableModel):
    """Model virtual: hanya halaman aktif dimuat (LIMIT/OFFSET + index)."""

    COLS = ["id", "serial", "job_id", "lot", "status", "station", "printed_at", "sig"]

    def __init__(self, con, page_size: int = 200):
        super().__init__()
        self.con = con
        self.page_size = page_size
        self.where = "1=1"
        self.params: list = []
        self._count = 0
        self._rows: list = []
        self._page = 0

    def set_filter(self, where: str, params: list) -> None:
        self.where, self.params = where, params
        self.refresh()

    def refresh(self) -> None:
        with db._LOCK:
            self._count = self.con.execute(
                f"SELECT COUNT(*) c FROM serial_records WHERE {self.where}",
                self.params).fetchone()["c"]
        self._load(self._page)

    def _load(self, page: int) -> None:
        self._page = max(0, page)
        off = self._page * self.page_size
        with db._LOCK:
            self._rows = self.con.execute(
                f"SELECT {','.join(self.COLS)} FROM serial_records WHERE {self.where} "
                f"ORDER BY id LIMIT ? OFFSET ?", self.params + [self.page_size, off]).fetchall()
        self.beginResetModel()
        self.endResetModel()

    # Qt API -----------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return self._count if not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLS)

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        return False

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        pos_in_page = index.row() - self._page * self.page_size
        if not (0 <= pos_in_page < len(self._rows)):
            return "…"
        if role == Qt.ItemDataRole.DisplayRole:
            v = self._rows[pos_in_page][index.column()]
            return "" if v is None else str(v)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            s = str(self._rows[pos_in_page][4])
            from PySide6.QtGui import QColor
            col = {"PRINTED": P.ok, "VERIFIED": P.neon_bright, "ISSUED": P.info,
                   "VOID": P.err}.get(s, P.text_dim)
            return QColor(col)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.COLS[section] if orientation == Qt.Orientation.Horizontal else \
                str(self._page * self.page_size + section + 1)
        return None

    def goto_page(self, page: int) -> None:
        self._load(page)


class DbBrowserPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        t = QLabel("DATABASE BROWSER — serial_records")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)

        # ---- bar filter ----------------------------------------------------
        fbar = QHBoxLayout()
        self.ed_search = QLineEdit(); self.ed_search.setPlaceholderText("Cari serial (LIKE)…")
        self.cb_status = QComboBox(); self.cb_status.addItems(["(semua)", "ISSUED", "PRINTED", "VERIFIED", "VOID"])
        self.ed_lot = QLineEdit(); self.ed_lot.setPlaceholderText("LOT…")
        self.ed_regex = QLineEdit(); self.ed_regex.setPlaceholderText("regex serial…")
        for w in (self.ed_search, self.cb_status, self.ed_lot, self.ed_regex):
            w.setToolTip("Filter")
        b_go = QPushButton("Terapkan")
        b_go.setIcon(icon("search", P.text, 16))
        b_go.clicked.connect(self._apply_filters)
        fbar.addWidget(self.ed_search, 2)
        fbar.addWidget(self.cb_status, 1)
        fbar.addWidget(self.ed_lot, 1)
        fbar.addWidget(self.ed_regex, 2)
        fbar.addWidget(b_go)
        lay.addLayout(fbar)

        # ---- tabel ----------------------------------------------------------
        self.model = LazySerialModel(ctx.con)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.doubleClicked.connect(self._show_detail)
        lay.addWidget(self.view, 1)

        pager = QHBoxLayout()
        b_prev = QPushButton("◀"); b_next = QPushButton("▶")
        self.lbl_page = QLabel("")
        b_prev.clicked.connect(lambda: self.model.goto_page(self.model._page - 1))
        b_next.clicked.connect(lambda: self.model.goto_page(self.model._page + 1))
        pager.addWidget(b_prev); pager.addWidget(self.lbl_page); pager.addWidget(b_next)
        b_void = QPushButton("Void terpilih")
        b_void.setIcon(icon("warning", P.err, 16))
        b_void.clicked.connect(self._void_selected)
        b_reprint = QPushButton("Reprint-flag terpilih")
        b_reprint.setIcon(icon("history", P.text, 16))
        b_reprint.clicked.connect(self._flag_reprint)
        pager.addStretch(1)
        pager.addWidget(b_void)
        pager.addWidget(b_reprint)
        lay.addLayout(pager)

        # ---- raw SQL (admin read-only) ---------------------------------------
        self.raw_host = QWidget()
        rl = QVBoxLayout(self.raw_host)
        rl.addWidget(QLabel("RAW SQL (read-only · admin):"))
        self.ed_sql = QLineEdit("SELECT status, COUNT(*) n FROM serial_records GROUP BY status")
        self.txt_raw = QTextEdit(); self.txt_raw.setReadOnly(True); self.txt_raw.setMaximumHeight(120)
        b_run = QPushButton("Jalankan")
        b_run.setIcon(icon("play", P.text, 16))
        b_run.clicked.connect(self._run_raw)
        rl.addWidget(self.ed_sql)
        rlr = QHBoxLayout(); rlr.addWidget(b_run); rlr.addStretch(1)
        rl.addLayout(rlr)
        rl.addWidget(self.txt_raw)
        self.raw_host.hide()
        lay.addWidget(self.raw_host)
        b_toggle = QPushButton("SQL ▾")
        b_toggle.setObjectName("Flat")
        b_toggle.clicked.connect(lambda: self._toggle_raw(b_toggle))
        pager.addWidget(b_toggle)

        self.model.refresh()
        self._update_page_lbl()
        self.view.selectionModel().selectionChanged.connect(self._update_page_lbl)

    def _update_page_lbl(self) -> None:
        self.lbl_page.setText(f"hal {self.model._page + 1} · total {self.model._count:,}")

    def _apply_filters(self) -> None:
        where, params = ["1=1"], []
        if self.ed_search.text().strip():
            where.append("serial LIKE ?"); params.append(f"%{self.ed_search.text().strip()}%")
        if self.cb_status.currentIndex() > 0:
            where.append("status=?"); params.append(self.cb_status.currentText())
        if self.ed_lot.text().strip():
            where.append("lot LIKE ?"); params.append(f"%{self.ed_lot.text().strip()}%")
        if self.ed_regex.text().strip():
            try:
                re.compile(self.ed_regex.text())
            except re.error as e:
                QMessageBox.warning(self, "Regex", str(e)); return
            where.append("serial REGEXP ?"); params.append(self.ed_regex.text())
        self.model.set_filter(" AND ".join(where), params)
        self._update_page_lbl()

    def _selected_serials(self) -> list[str]:
        out = []
        for idx in self.view.selectionModel().selectedRows():
            p = idx.row() - self.model._page * self.model.page_size
            if 0 <= p < len(self.model._rows):
                out.append(str(self.model._rows[p]["serial"]))
        return out

    def _void_selected(self) -> None:
        if self.ctx.role != "admin":
            QMessageBox.warning(self, "Akses", "Hanya admin yang boleh void.")
            return
        ser = self._selected_serials()
        if not ser:
            return
        ok = QMessageBox.question(self, "Void", f"Void {len(ser)} serial? Alasan di catatan.",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        with db._LOCK:
            self.con.executemany("UPDATE serial_records SET status='VOID', note='void manual' "
                                 "WHERE serial=?", [(s,) for s in ser])
            db.audit(self.con, self.ctx.user, "VOID", "serial_records", "", ",".join(ser[:5]))
            self.con.commit()
        self.ctx.toast.push(f"{len(ser)} serial divoid.", "warn")
        self.model.refresh()

    def _flag_reprint(self) -> None:
        ser = self._selected_serials()
        for s in ser:
            with db._LOCK:
                self.con.execute("UPDATE serial_records SET reprint_count=reprint_count+1 "
                                 "WHERE serial=?", (s,))
                db.audit(self.con, self.ctx.user, "REPRINT_FLAG", "serial_records", "", s)
                self.con.commit()
        if ser:
            self.ctx.toast.push(f"{len(ser)} serial ditandai reprint.", "info")

    def _show_detail(self) -> None:
        ser = self._selected_serials()
        if not ser:
            return
        row = self.con.execute("SELECT * FROM serial_records WHERE serial=?", (ser[0],)).fetchone()
        if row:
            QMessageBox.information(self, "Detail serial", json.dumps(dict(row), indent=2,
                                          ensure_ascii=False, default=str))

    def _toggle_raw(self, btn: QPushButton) -> None:
        show = not self.raw_host.isVisible()
        self.raw_host.setVisible(show)
        btn.setText("SQL ▴" if show else "SQL ▾")

    def _run_raw(self) -> None:
        if self.ctx.role != "admin":
            QMessageBox.warning(self, "Akses", "Raw SQL khusus admin.")
            return
        sql = self.ed_sql.text().strip().rstrip(";")
        if not sql.lower().startswith(("select", "with", "pragma")) or ";" in sql:
            QMessageBox.warning(self, "Ditolak", "Hanya SELECT/WITH/PRAGMA read-only tanpa ';' ganda.")
            return
        try:
            rows = self.con.execute(sql).fetchmany(200)
            self.txt_raw.setPlainText("\n".join(" | ".join(str(c) for c in r) for r in rows))
        except Exception as e:
            self.txt_raw.setPlainText(f"error: {e}")
