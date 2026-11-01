"""Halaman Aggregation & Track&Trace (§3.9) — pohon Unit→Pack→Case→Pallet + ekspor."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import (QAbstractItemModel, QModelIndex, Qt)
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTreeView, QVBoxLayout, QWidget)

from ui import db
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class TreeModel(QAbstractItemModel):
    """Model pohon dari aggregation_map (unit→pack→case→pallet)."""

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.children: dict[str, list[str]] = {}
        self.nodes: set[str] = set()
        self.roots: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.children.clear(); self.nodes.clear()
        with db._LOCK:
            rows = self.con.execute("SELECT unit,pack,case,pallet FROM aggregation_map").fetchall()
        for r in rows:
            chain = [x for x in (r["pallet"], r["case"], r["pack"], r["unit"]) if x]
            for a, b in zip(chain, chain[1:]):
                self.children.setdefault(a, []).append(b)
                self.nodes.update((a, b))
            if chain:
                pass
        parented = {c for kids in self.children.values() for c in kids}
        self.roots = sorted(n for n in self.nodes if n not in parented)
        self.endResetModel()

    def index(self, row, column, parent=QModelIndex()):
        node = self._node_at(parent, row)
        return self.createIndex(row, column, node)

    def _node_at(self, parent, row):
        kids = self.roots if not parent.isValid() else self.children.get(parent.data(), [])
        return kids[row] if 0 <= row < len(kids) else None

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        child = index.data()
        for par, kids in self.children.items():
            if child in kids:
                prow = kids.index(child)
                gp = next((k for k in self.children if par in self.children.get(k, [])), None)
                # cari indeks par di bawah gp/roots
                sibs = self.roots if gp is None else self.children.get(gp, [])
                if par in sibs:
                    return self.createIndex(sibs.index(par), 0, par)
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        kids = self.roots if not parent.isValid() else self.children.get(parent.data(), [])
        return len(kids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 2

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.data()
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return node
            kids = self.children.get(node, [])
            lvl = "PALLET" if any(node in v for v in self.children.values()) and node in self.roots and len(str(node)) > 18 else \
                  ("UNIT" if kids == [] or str(node)[:4] == "SRX" else "GROUP")
            return f"{len(kids)} anak · {lvl}"
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            from PySide6.QtGui import QColor
            return QColor(P.neon_bright if index.data() in self.roots else P.text)
        return None


class AggregatePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        t = QLabel("AGGREGATION & TRACK&TRACE")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)

        form = QHBoxLayout()
        self.ed_unit = QLineEdit(); self.ed_unit.setPlaceholderText("Serial UNIT (scan/manual)…")
        self.ed_parent = QLineEdit(); self.ed_parent.setPlaceholderText("Induk (PACK/CASE/PALLET ID)…")
        b_link = QPushButton("Link →")
        b_link.setIcon(icon("network", P.text, 16))
        b_link.clicked.connect(self._link)
        b_auto = QPushButton("Auto-agregasi job aktif")
        b_auto.setIcon(icon("box", P.text, 16))
        b_auto.clicked.connect(self._auto_aggregate)
        for w, lbl in ((self.ed_unit, "Unit:"), (self.ed_parent, "Induk:")):
            w.setToolTip(lbl.text_ if hasattr(w, "text_") else "")
        form.addWidget(QLabel("Unit:")); form.addWidget(self.ed_unit, 2)
        form.addWidget(QLabel("Induk:")); form.addWidget(self.ed_parent, 2)
        form.addWidget(b_link); form.addWidget(b_auto)
        lay.addLayout(form)

        self.tree = QTreeView()
        self.model = TreeModel(ctx.con)
        self.tree.setModel(self.model)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tree, 1)

        self.status = QLabel("Klik node untuk melihat keturunan.")
        self.status.setObjectName("Subtle")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.tree.clicked.connect(self._show_descendants)

        btns = QHBoxLayout()
        for label, fn, ic in (("Ekspor JSON EPCIS", self._export_json, "export"),
                              ("Ekspor CSV parent-child", self._export_csv, "export")):
            b = QPushButton(label); b.setIcon(icon(ic, P.text, 16)); b.clicked.connect(fn)
            b.setToolTip(label)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

    # ------------------------------------------------------------------
    def _link(self) -> None:
        u, p = self.ed_unit.text().strip().upper(), self.ed_parent.text().strip().upper()
        if not u or not p:
            self.ctx.toast.push("Isi unit & induk.", "warn")
            return
        res = self.ctx.backend.call("agg_link", {"child": u, "parent": p})
        verdict = res.get("result", "Ok")
        if verdict == "Conflict":
            self.status.setText(f"⚠ KONFLIK: {u} sudah teragregasi ke induk lain!")
            self.ctx.toast.push(f"Konflik agregasi pada {u}", "err")
            db.audit(self.con, self.ctx.user, "AGG_CONFLICT", "aggregation_map", "", u,
                     f"ditagih induk {p}")
            return
        if verdict != "Ok":
            self.ctx.toast.push(f"Agregasi gagal: {verdict}", "err")
            return
        with db._LOCK:
            self.con.execute(
                "INSERT INTO aggregation_map(unit,pack,job_id) VALUES(?,?,?) "
                "ON CONFLICT(unit) DO UPDATE SET pack=excluded.pack",
                (u, p, None))
            db.audit(self.con, self.ctx.user, "AGG_LINK", "aggregation_map", "", f"{u}→{p}")
            self.con.commit()
        self.model.reload()
        self.ctx.toast.push(f"Linked {u} → {p}", "ok")
        self.ed_unit.clear(); self.ed_parent.clear()

    def _auto_aggregate(self) -> None:
        """Ambil serial ISSUED/PRINTED terbaru dari job RUNNING → 12 unit per case."""
        rows = self.con.execute(
            "SELECT serial FROM serial_records WHERE status IN ('ISSUED','PRINTED') "
            "AND serial NOT IN (SELECT unit FROM aggregation_map) ORDER BY id DESC LIMIT 24"
        ).fetchall()
        if not rows:
            self.ctx.toast.push("Tidak ada unit tersisa utk diagregasi.", "info")
            return
        case = f"CASE-{self.con.execute('SELECT COUNT(DISTINCT case) c FROM aggregation_map').fetchone()['c'] + 1:05d}"
        with db._LOCK:
            for i, r in enumerate(rows):
                self.con.execute(
                    "INSERT OR IGNORE INTO aggregation_map(unit,pack,case) VALUES(?,?,?)",
                    (r["serial"], f"{case}-P{i // 6 + 1}", case))
            self.con.execute("INSERT OR IGNORE INTO aggregation_map(unit,pack,case,pallet) "
                             "VALUES(?,'','',?)", (case, "PLT-0001"))
            db.audit(self.con, self.ctx.user, "AGG_AUTO", "aggregation_map", "", case,
                     f"{len(rows)} unit")
            self.con.commit()
        self.model.reload()
        self.ctx.toast.push(f"Auto-agregasi {len(rows)} unit ke {case}.", "ok")

    def _show_descendants(self, idx) -> None:
        node = idx.data()
        if not node:
            return
        res = self.ctx.backend.call("agg_descendants", {"root": node})
        lst = res.get("list", [])
        self.status.setText(f"Keturunan {node}: {len(lst)} item\n" + ", ".join(lst[:40]))

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor EPCIS JSON", "epcis.json", "JSON (*.json)")
        if not path:
            return
        js = self.ctx.backend.call("agg_export", {"gtin": "08993123450017"})["json"]
        Path(path).write_text(js, encoding="utf-8")
        self.ctx.toast.push(f"EPCIS JSON: {path}", "ok")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor CSV", "agg.csv", "CSV (*.csv)")
        if not path:
            return
        rows = self.con.execute("SELECT unit,pack,case,pallet FROM aggregation_map").fetchall()
        lines = ["unit,pack,case,pallet"] + [
            ",".join([r["unit"], r["pack"] or "", r["case"] or "", r["pallet"] or ""]) for r in rows]
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.ctx.toast.push(f"CSV: {path}", "ok")
