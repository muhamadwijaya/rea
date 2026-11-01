"""Token tema ABYSS NEON — satu-satunya tempat hex color di seluruh aplikasi.

Navy Blue gelap sebagai basis + Monster Energy Green yang dinaikkan jadi neon
(#00FF5F, lebih nyala dari hijau Monster asli #00E025).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Palette:
    # ---- Navy layers -------------------------------------------------------
    void: str = "#04070E"          # latar terdalam
    bg: str = "#081120"            # window background
    navy_900: str = "#0B1B34"      # panel
    navy_800: str = "#102544"      # kartu / elevated
    navy_700: str = "#16305A"      # hover / aktif
    navy_600: str = "#1E3A66"      # thumb scrollbar / border kuat
    # ---- Border ------------------------------------------------------------
    line: str = "#12284A"
    line_strong: str = "#224C7A"
    # ---- Neon green (dibuat LEBIH NYALA dari Monster asli #00E025) ---------
    neon: str = "#00FF5F"          # aksen utama — menyala
    neon_bright: str = "#6BFF9E"   # highlight / teks aksen
    neon_dim: str = "#00C94A"      # state pressed / secondary
    neon_deep: str = "#007A33"     # base gradien bawah
    glow: str = "rgba(0,255,95,0.55)"
    glow_soft: str = "rgba(0,255,95,0.22)"
    # ---- Teks ---------------------------------------------------------------
    text: str = "#DCE8F5"
    text_dim: str = "#8FA6C2"
    text_mute: str = "#5C7494"
    on_neon: str = "#03130A"       # teks di atas tombol neon
    # ---- Semantik -----------------------------------------------------------
    ok: str = "#00FF5F"
    warn: str = "#FFB300"
    err: str = "#FF3355"
    info: str = "#38BDF8"
    running: str = "#00FF5F"
    idle: str = "#5C7494"
    paused: str = "#FFB300"


ACCENT_PRESETS: dict[str, tuple[str, str, str, str, str]] = {
    # nama: (neon, bright, dim, deep, glow)
    "Neon Green": ("#00FF5F", "#6BFF9E", "#00C94A", "#007A33", "rgba(0,255,95,0.55)"),
    "Azure": ("#22D3EE", "#7DE8F8", "#0EA5C9", "#0B6E86", "rgba(34,211,238,0.55)"),
    "Amber": ("#FFC53D", "#FFE08A", "#D99A00", "#8A6200", "rgba(255,197,61,0.55)"),
}


@dataclass
class Sizes:
    radius_s: int = 6
    radius_card: int = 10
    radius_dialog: int = 14
    radius_pill: int = 999
    border: int = 1
    sp: int = 4                     # grid spacing
    pad_s: int = 8
    pad_m: int = 12
    pad_l: int = 16
    pad_xl: int = 20
    fs_caption: int = 11
    fs_body: int = 13
    fs_sub: int = 15
    fs_title: int = 20
    fs_display: int = 34
    led: int = 10
    scrollbar_w: int = 10


FONT_STACK = ('"Segoe UI","Inter","Ubuntu","DejaVu Sans",sans-serif')
MONO_STACK = '"JetBrains Mono","Cascadia Mono","Consolas","DejaVu Sans Mono",monospace'

P = Palette()
SZ = Sizes()


def apply_accent(name: str) -> Palette:
    """Kembalikan Palette dengan accent hue diganti (Neon Green default)."""
    if name not in ACCENT_PRESETS:
        return P
    neon, bright, dim, deep, glow = ACCENT_PRESETS[name]
    return replace(P, neon=neon, neon_bright=bright, neon_dim=dim, neon_deep=deep,
                   glow=glow, glow_soft=glow.replace("0.55", "0.22"),
                   ok=neon, running=neon)
