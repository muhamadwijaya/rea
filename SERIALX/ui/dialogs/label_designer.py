"""Label Designer WYSIWYG sederhana (§3.5) — drag-drop, grid snap, undo/redo, ekspor .slx."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ui.theme.tokens import P, SZ


class El:
    """Satu elemen label pada kanvas (mm coordinate)."""

    def __init__(self, kind: str, x: float, y: float, **kw):
        self.kind = kind
        self.x = x
        self.y = y
        self.props: dict = {"content": kw.get("content", ""), "w": kw.get("w", 20),
                            "h": kw.get("h", 10), "font_pt": kw.get("font_pt", 10),
                            "bold": kw.get("bold", False)}

    def to_dict(self) -> dict:
        return {"type": self.kind, "x": round(self.x, 2), "y": round(self.y, 2), **self.props}

    @staticmethod
    def from_dict(d: dict) -> "El":
        e = El(d.pop("type", "text"), d.pop("x", 0), d.pop("y", 0))
        e.props.update(d)
        return e


class Canvas(QWidget):
    """Kanvas drag-drop dengan grid snapping + zoom."""

    GRID = 2.0  # mm

    def __init__(self, designer: "LabelDesigner"):
        super().__init__(designer)
        self.dz = designer
        self.zoom = 4.0  # px per mm
        self.setFixedSize(int(100 * self.zoom) + 20, int(150 * self.zoom) + 20)
        self._drag_idx: int | None = None
        self._off = QPointF()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        z = self.zoom
        # grid halus
        p.setPen(QColor(220, 230, 245))
        for gx in range(0, 101, 5):
            p.drawLine(int(gx * z), 10, int(gx * z), int(150 * z) + 10)
        for gy in range(0, 151, 5):
            p.drawLine(10, int(gy * z), int(100 * z) + 10, int(gy * z))
        # ruler tipis
        p.setPen(QColor(P.navy_700))
        p.drawRect(9, 9, int(100 * z) + 2, int(150 * z) + 2)
        demo = {"serial": "SRX241102-L1000042-KZ", "sku": "SKU-DEMO", "lot": "LOT2401",
                "exp": "01-07-2026", "date": "02-11-2026", "weight": "330 ml", "origin": "ID"}
        for i, el in enumerate(self.dz.els):
            c = _bind(el.props.get("content", ""), demo)
            x, y = 10 + el.x * z, 10 + el.y * z
            if el.kind == "text":
                f = QFont("DejaVu Sans", max(6, int(el.props.get("font_pt", 10) * z / 4)))
                f.setBold(bool(el.props.get("bold")))
                p.setFont(f)
                p.setPen(QColor("#0B1B34"))
                p.drawText(QPointF(x, y + 4 * z / 2), c[:60])
            elif el.kind == "code128":
                from ui.qr import draw_code128
                draw_code128(p, c or "CODE128", int(x), int(y), int(el.props.get("w", 60) * z),
                             int(el.props.get("h", 12) * z))
            elif el.kind == "qr":
                from ui.qr import draw_qr
                draw_qr(p, c or "QR", int(x), int(y), int(el.props.get("size_mm", 24) * z))
            elif el.kind == "rect":
                p.setPen(QPen(QColor(P.neon), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(int(x), int(y), int(el.props.get("w", 20) * z), int(el.props.get("h", 10) * z))
            if i == self.dz.sel:
                p.setPen(QPen(QColor(P.err), 1, style=Qt.PenStyle.DashLine))
                p.drawRect(int(x - 3), int(y - 3), int((el.props.get("w", 24)) * z) + 6,
                           int(max(el.props.get("h", 6), 6) * z) + 6)
        p.end()

    # mouse ----------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        z = self.zoom
        mx, my = (e.position().x() - 10) / z, (e.position().y() - 10) / z
        self.dz.sel = None
        for i, el in enumerate(reversed(self.dz.els)):
            j = len(self.dz.els) - 1 - i
            w = el.props.get("w", 24); h = el.props.get("h", 6)
            if el.x <= mx <= el.x + w and el.y <= my <= el.y + max(h, 5):
                self.dz.sel = j
                self._drag_idx = j
                self._off = QPointF(mx - el.x, my - el.y)
                break
        self.dz.sync_props()
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag_idx is None:
            return
        z = self.zoom
        mx, my = (e.position().x() - 10) / z, (e.position().y() - 10) / z
        el = self.dz.els[self._drag_idx]
        nx = round((mx - self._off.x()) / self.GRID) * self.GRID   # grid snapping
        ny = round((my - self._off.y()) / self.GRID) * self.GRID
        el.x = max(0, min(100 - 5, nx)); el.y = max(0, min(150 - 2, ny))
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag_idx is not None:
            self.dz.push_undo()
        self._drag_idx = None

    def keyPressEvent(self, e) -> None:  # noqa: N802
        step = 1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else self.GRID
        if self.dz.sel is not None:
            el = self.dz.els[self.dz.sel]
            k = e.key()
            if k == Qt.Key.Key_Left:
                el.x -= step; self.update()
            elif k == Qt.Key.Key_Right:
                el.x += step; self.update()
            elif k == Qt.Key.Key_Up:
                el.y -= step; self.update()
            elif k == Qt.Key.Key_Down:
                el.y += step; self.update()
            elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.dz._delete()
        super().keyPressEvent(e)


def _bind(text: str, ctx: dict) -> str:
    import re as _re
    return _re.sub(r"\{\{(\w+)\}\}", lambda m: str(ctx.get(m.group(1), m.group(0))), text)


class LabelDesigner(QDialog):
    """Dialog WYSIWYG: list elemen, kanvas, property panel, undo/redo, simpan JSON/.slx."""

    def __init__(self, ctx, job_id: int | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.job_id = job_id
        self.setWindowTitle("Label Designer")
        self.setMinimumSize(1000, 640)
        self.els: list[El] = []
        self.sel: int | None = None
        self._undo: list[str] = []
        self._redo: list[str] = []

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        b_add_row = QHBoxLayout()
        for kind, ic in (("text", "edit"), ("code128", "code128"), ("qr", "qr"),
                         ("datamatrix", "datamatrix"), ("rect", "tag"), ("line", "minus")):
            from ui.theme.icons import icon
            b = QPushButton(kind.upper())
            b.setIcon(icon(ic, P.text, 16))
            b.clicked.connect(lambda _=False, k=kind: self._add(k))
            b.setToolTip(f"Tambah elemen {kind}")
            b_add_row.addWidget(b)
        left.addLayout(b_add_row)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.canvas = Canvas(self)
        scroll.setWidget(self.canvas)
        left.addWidget(scroll, 1)
        nav = QHBoxLayout()
        self.btn_undo = QPushButton("Undo"); self.btn_redo = QPushButton("Redo")
        self.btn_undo.clicked.connect(self._undo_step)
        self.btn_redo.clicked.connect(self._redo_step)
        nav.addWidget(self.btn_undo); nav.addWidget(self.btn_redo); nav.addStretch(1)
        left.addLayout(nav)
        root.addLayout(left, 3)

        prop = QVBoxLayout()
        prop.addWidget(QLabel("PROPERTI ELEMEN"))
        self.ed_content = QLineEdit(); self.ed_content.setPlaceholderText("teks / {{serial}} …")
        self.cb_align = QComboBox(); self.cb_align.addItems(["left", "center", "right"])
        self.sp_font = QDoubleSpinBox(); self.sp_font.setRange(4, 72); self.sp_font.setValue(10)
        self.sp_w = QDoubleSpinBox(); self.sp_w.setRange(1, 100); self.sp_w.setValue(40)
        self.sp_h = QDoubleSpinBox(); self.sp_h.setRange(1, 150); self.sp_h.setValue(12)
        self.cb_ec = QComboBox(); self.cb_ec.addItems(["L", "M", "Q", "H"])
        for lab, wdg in (("Content/binding", self.ed_content), ("Ukuran pt", self.sp_font),
                         ("Lebar mm", self.sp_w), ("Tinggi mm", self.sp_h),
                         ("QR EC level", self.cb_ec)):
            prop.addWidget(QLabel(lab)); prop.addWidget(wdg)
        self.ed_content.editingFinished.connect(self._apply_props)
        self.sp_font.editingFinished.connect(self._apply_props)
        self.sp_w.editingFinished.connect(self._apply_props)
        self.sp_h.editingFinished.connect(self._apply_props)
        align_row = QHBoxLayout()
        for a in ("◀ left", "center", "right ▶", "top", "middle", "bottom"):
            b = QPushButton(a.split()[0]); b.setObjectName("Flat")
            b.clicked.connect(lambda _=False, aa=a: self._align(aa))
            align_row.addWidget(b)
        prop.addLayout(align_row)
        b_del = QPushButton("Hapus elemen (Del)")
        b_del.setObjectName("Danger"); b_del.clicked.connect(self._delete)
        b_dup = QPushButton("Salin elemen")
        b_dup.clicked.connect(self._duplicate)
        prop.addWidget(b_del); prop.addWidget(b_dup)
        prop.addStretch(1)
        save_row = QHBoxLayout()
        b_save = QPushButton("Simpan ke Job")
        b_save.clicked.connect(self._save_to_job)
        b_exp = QPushButton("Ekspor .slx")
        b_exp.clicked.connect(self._export_slx)
        b_imp = QPushButton("Impor .slx")
        b_imp.clicked.connect(self._import_slx)
        save_row.addWidget(b_save); save_row.addWidget(b_exp); save_row.addWidget(b_imp)
        prop.addLayout(save_row)
        root.addLayout(prop, 1)

        if job_id:
            row = ctx.con.execute("SELECT template_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["template_json"]:
                self.load_template(json.loads(row["template_json"]))
        else:
            self.load_default()

    # ------------------------------------------------------------------
    def load_default(self) -> None:
        self.els = [
            El("text", 6, 8, content="{{serial}}", font_pt=14, bold=True, w=60),
            El("text", 6, 20, content="SKU {{sku}} · LOT {{lot}} · EXP {{exp}}", font_pt=8, w=70),
            El("code128", 6, 30, content="{{serial}}", w=60, h=14),
            El("qr", 70, 95, content="https://verify.serialx.id/?s={{serial}}", w=24, h=24,
               size_mm=24),
            El("rect", 4, 4, w=92, h=142),
        ]
        self.canvas.update()

    def load_template(self, tmpl: dict) -> None:
        self.els = [El.from_dict(dict(e)) for e in tmpl.get("elements", [])]
        self.canvas.update()

    def to_template(self) -> dict:
        return {"ver": 1, "name": "Custom", "size_mm": [100, 150], "dpi": 300,
                "elements": [e.to_dict() for e in self.els]}

    def push_undo(self) -> None:
        self._undo.append(json.dumps(self.to_template()))
        self._undo = self._undo[-50:]
        self._redo.clear()

    def _add(self, kind: str) -> None:
        self.push_undo()
        defaults = {"text": dict(content="Teks baru", w=30, h=6),
                    "code128": dict(content="{{serial}}", w=50, h=12),
                    "qr": dict(content="{{serial}}", w=24, h=24, size_mm=24),
                    "datamatrix": dict(content="{{serial}}", w=20, h=20),
                    "rect": dict(content="", w=20, h=10),
                    "line": dict(content="", w=30, h=1)}[kind]
        self.els.append(El(kind, 10, 10, **defaults))
        self.sel = len(self.els) - 1
        self.sync_props(); self.canvas.update()

    def _delete(self) -> None:
        if self.sel is not None:
            self.push_undo()
            del self.els[self.sel]
            self.sel = None
            self.sync_props(); self.canvas.update()

    def _duplicate(self) -> None:
        if self.sel is not None:
            self.push_undo()
            src = self.els[self.sel].to_dict()
            src["x"] += 4; src["y"] += 4
            self.els.append(El.from_dict(src))
            self.canvas.update()

    def _align(self, mode: str) -> None:
        if self.sel is None:
            return
        el = self.els[self.sel]
        if mode.startswith("left"):
            el.x = 4
        elif mode == "center":
            el.x = (100 - el.props.get("w", 20)) / 2
        elif mode.startswith("right"):
            el.x = 96 - el.props.get("w", 20)
        elif mode == "top":
            el.y = 4
        elif mode == "middle":
            el.y = (150 - el.props.get("h", 6)) / 2
        elif mode == "bottom":
            el.y = 146 - el.props.get("h", 6)
        self.canvas.update()

    def sync_props(self) -> None:
        if self.sel is None:
            self.ed_content.setText("")
            return
        pr = self.els[self.sel].props
        self.ed_content.setText(str(pr.get("content", "")))
        self.sp_font.setValue(float(pr.get("font_pt", 10)))
        self.sp_w.setValue(float(pr.get("w", 20)))
        self.sp_h.setValue(float(pr.get("h", 10)))

    def _apply_props(self) -> None:
        if self.sel is None:
            return
        self.push_undo()
        pr = self.els[self.sel].props
        pr["content"] = self.ed_content.text()
        pr["font_pt"] = self.sp_font.value()
        pr["w"] = self.sp_w.value()
        pr["h"] = self.sp_h.value()
        self.canvas.update()

    def _undo_step(self) -> None:
        if not self._undo:
            return
        self._redo.append(json.dumps(self.to_template()))
        tmpl = json.loads(self._undo.pop())
        self.load_template(tmpl)

    def _redo_step(self) -> None:
        if not self._redo:
            return
        self._undo.append(json.dumps(self.to_template()))
        self.load_template(json.loads(self._redo.pop()))

    def _save_to_job(self) -> None:
        if not self.job_id:
            QMessageBox.information(self, "Designer", "Tidak ada job terpilih — buka dari Job Wizard.")
            return
        with self.ctx.con as con:
            con.execute("UPDATE jobs SET template_json=?, updated_at=? WHERE id=?",
                        (json.dumps(self.to_template()), db_now(), self.job_id))
        self.ctx.toast.push("Template tersimpan ke job.", "ok")

    def _export_slx(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor .slx", "label.slx", "SLX (*.slx)")
        if not path:
            return
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("template.json", json.dumps(self.to_template(), indent=2))
        self.ctx.toast.push(f"Ekspor {path}", "ok")

    def _import_slx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Impor .slx", "", "SLX (*.slx)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path) as zf:
                self.push_undo()
                self.load_template(json.loads(zf.read("template.json")))
            self.ctx.toast.push("Template diimpor.", "ok")
        except Exception as e:
            QMessageBox.warning(self, "Impor gagal", str(e))


def db_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
