"""Generator QSS global dari token — tidak ada hex di luar tokens.py."""
from __future__ import annotations

from ui.theme.tokens import FONT_STACK, MONO_STACK, Palette, Sizes


def build_qss(p: Palette, sz: Sizes) -> str:
    return f"""
/* ============ SERIALX · ABYSS NEON ============ */
* {{ font-family: {FONT_STACK}; font-size: {sz.fs_body}px; color: {p.text}; }}
QWidget {{ background: {p.bg}; }}
QMainWindow, QDialog {{ background: {p.bg}; }}
#RootShell {{ background: {p.void}; border: 1px solid {p.line_strong}; }}

/* ---- Title bar ---- */
#TitleBar {{ background: {p.navy_900}; border-bottom: 1px solid {p.line}; }}
#AppTitle {{ font-size: {sz.fs_sub}px; font-weight: 800; letter-spacing: 1px; color: {p.neon_bright}; }}
#WinBtn {{ background: transparent; border: none; color: {p.text_dim}; font-size: 14px;
          padding: 6px 12px; border-radius: {sz.radius_s}px; }}
#WinBtn:hover {{ background: {p.navy_700}; color: {p.text}; }}
#WinClose:hover {{ background: {p.err}; color: white; }}
#MachineChip {{ background: {p.navy_800}; border: 1px solid {p.line_strong};
               border-radius: {sz.radius_pill}px; padding: 3px 10px; color: {p.text_dim};
               font-size: {sz.fs_caption}px; }}

/* ---- Nav sidebar ---- */
#NavPanel {{ background: {p.navy_900}; border-right: 1px solid {p.line}; }}
#NavButton {{ background: transparent; border: none; border-left: 3px solid transparent;
             text-align: left; padding: 10px 14px; color: {p.text_dim}; font-size: {sz.fs_body}px; }}
#NavButton:hover {{ background: {p.navy_800}; color: {p.text}; }}
#NavButton:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 {p.navy_700}, stop:1 {p.navy_900});
    border-left: 3px solid {p.neon}; color: {p.neon_bright}; font-weight: 700; }}

/* ---- Cards / panels ---- */
#Card {{ background: {p.navy_800}; border: 1px solid {p.line};
        border-radius: {sz.radius_card}px; }}
#Card:hover {{ border: 1px solid {p.line_strong}; }}
#StatValue {{ font-family: {MONO_STACK}; font-size: {sz.fs_display}px; font-weight: 800;
             color: {p.neon}; letter-spacing: 1px; }}
#StatLabel {{ color: {p.text_mute}; font-size: {sz.fs_caption}px; letter-spacing: 1px; }}
#SectionTitle {{ font-size: {sz.fs_title}px; font-weight: 800; letter-spacing: 0.5px; color: {p.text}; }}
#Subtle {{ color: {p.text_dim}; }}
#MonoText {{ font-family: {MONO_STACK}; color: {p.neon_bright}; }}

/* ---- Buttons ---- */
QPushButton {{ background: {p.navy_800}; border: 1px solid {p.line_strong};
              border-radius: {sz.radius_s}px; padding: 8px 16px; color: {p.text}; font-weight: 600; }}
QPushButton:hover {{ background: {p.navy_700}; border-color: {p.neon_dim}; }}
QPushButton:pressed {{ background: {p.navy_900}; }}
QPushButton:disabled {{ color: {p.text_mute}; border-color: {p.line}; background: {p.navy_900}; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {p.neon}, stop:0.55 {p.neon_dim}, stop:1 {p.neon_deep});
    color: {p.on_neon}; border: 1px solid {p.neon_bright}; border-radius: {sz.radius_pill}px;
    font-weight: 800; letter-spacing: 1px; padding: 10px 22px; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 {p.neon_bright}, stop:0.55 {p.neon}, stop:1 {p.neon_dim}); }}
QPushButton#Danger {{ background: {p.navy_800}; border: 1px solid {p.err}; color: {p.err}; }}
QPushButton#Danger:hover {{ background: {p.err}; color: white; }}
QPushButton#Flat {{ background: transparent; border: none; color: {p.text_dim}; }}
QPushButton#Flat:hover {{ color: {p.neon}; }}

/* ---- Inputs ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
    background: {p.navy_900}; border: 1px solid {p.line_strong};
    border-radius: {sz.radius_s}px; padding: 7px 10px; color: {p.text};
    selection-background-color: {p.neon_dim}; selection-color: {p.on_neon}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {p.neon}; }}
QLineEdit:read-only {{ color: {p.text_dim}; }}
QToolTip {{ background: {p.navy_800}; color: {p.text}; border: 1px solid {p.neon_dim};
           padding: 4px; border-radius: 4px; }}

/* ---- ComboBox popup ---- */
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {p.navy_900}; border: 1px solid {p.neon_dim};
    selection-background-color: {p.navy_700}; selection-color: {p.neon_bright}; outline: none; }}

/* ---- Tab ---- */
QTabWidget::pane {{ border: 1px solid {p.line}; border-radius: {sz.radius_card}px; background: {p.navy_900}; }}
QTabBar::tab {{ background: transparent; color: {p.text_dim}; padding: 8px 16px;
   border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p.neon_bright}; border-bottom: 2px solid {p.neon}; font-weight: 700; }}
QTabBar::tab:hover {{ color: {p.text}; }}

/* ---- Table / tree / list ---- */
QTableView, QTreeView, QListView {{ background: {p.navy_900}; alternate-background-color: {p.bg};
    border: 1px solid {p.line}; gridline-color: {p.line};
    selection-background-color: {p.navy_700}; selection-color: {p.neon_bright}; }}
QHeaderView::section {{ background: {p.navy_800}; color: {p.text_dim}; padding: 6px;
    border: none; border-right: 1px solid {p.line}; border-bottom: 2px solid {p.neon_dim};
    font-weight: 700; }}
QTableView::item:selected {{ border: 1px solid {p.neon}; }}

/* ---- Scrollbar ---- */
QScrollBar:vertical {{ background: {p.bg}; width: {sz.scrollbar_w}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.navy_600}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {p.neon_dim}; }}
QScrollBar:horizontal {{ background: {p.bg}; height: {sz.scrollbar_w}px; }}
QScrollBar::handle:horizontal {{ background: {p.navy_600}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {p.neon_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{ background: none; }}

/* ---- Progress ---- */
QProgressBar {{ background: {p.navy_900}; border: 1px solid {p.line_strong};
   border-radius: {sz.radius_s}px; text-align: right; color: {p.neon_bright};
   font-family: {MONO_STACK}; padding-right: 6px; }}
QProgressBar::chunk {{ border-radius: 4px;
   background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {p.neon}, stop:1 {p.neon_dim}); }}

/* ---- Group / menu / misc ---- */
QGroupBox {{ border: 1px solid {p.line}; border-radius: {sz.radius_card}px;
   margin-top: 12px; padding-top: 8px; color: {p.text_dim}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {p.neon_bright}; }}
QMenu {{ background: {p.navy_900}; border: 1px solid {p.line_strong}; }}
QMenu::item {{ padding: 6px 26px; }}
QMenu::item:selected {{ background: {p.navy_700}; color: {p.neon_bright}; }}
QMenuBar {{ background: {p.navy_900}; border-bottom: 1px solid {p.line}; }}
QStatusBar {{ background: {p.navy_900}; color: {p.text_dim}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{ border: 1px solid {p.line_strong}; border-radius: 4px; background: {p.navy_900}; }}
QCheckBox::indicator:checked {{ background: {p.neon}; border: 1px solid {p.neon_bright}; border-radius: 4px; }}
QRadioButton::indicator:unchecked {{ border: 1px solid {p.line_strong}; border-radius: 8px; background: {p.navy_900}; }}
QRadioButton::indicator:checked {{ border: 4px solid {p.neon}; border-radius: 8px; background: {p.navy_900}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.navy_700}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; background: {p.neon}; }}
QSplitter::handle {{ background: {p.line}; }}
"""
