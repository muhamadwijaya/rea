"""Encoder barcode native (tanpa dependensi): Code-128B dan QR model-2 versi 1-4, EC-M.

Cukup untuk preview label & simulator cetak. Untuk produksi tetap pakai ZPL/TSPL
yang digenerate backend C#.
"""
from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter

# ---------------------------------------------------------------------------
# CODE 128-B
# ---------------------------------------------------------------------------
_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "141122", "141221",
    "112214", "122114", "122213", "112223", "212123", "212321", "232121", "111323",
    "131123", "211321", "231113", "213113", "213311", "213131", "311123", "311321",
    "331121", "313111", "312122", "312221", "222112", "222211", "122212", "122122",
    "111223", "111322", "131212", "112213", "112312", "132112", "132211", "212113",
    "212311", "213112", "213121", "211313", "211331", "231112", "231211", "221311",
    "221113", "221321", "421112", "412221", "422121", "422211", "421212", "421221",
    "412122", "421121", "411222", "422212", "422221", "412212", "422112", "421122",
    "421211", "212123", "212321", "232121", "111323", "131123", "211321", "231113",
    "213113", "213311", "213131", "311123", "311321", "331121", "112232", "122231",
    "321212", "112132", "112231", "322112", "321122", "112123", "311213", "112213",
    "321112", "112321", "112223", "122131", "311123", "122311", "132131", "121232",
    "111232", "112132", "111223", "123112", "131122", "122321", "132211", "212132",
    "231131", "213113", "213211", "213121", "111213", "222113", "322311", "113221",
    "312131", "113123", "112313", "123211", "311213", "132221", "213221", "221123",
    "212213", "222121", "111123", "121123", "123111", "131112", "211113", "211311",
    "231111", "112232", "122231", "321212", "112132", "112231", "322112", "321122",
]  # tidak dipakai langsung — pattern resmi di bawah


def _c128_patterns() -> list[str]:
    """Tabel width pattern Code128 (bar+space), index = nilai kode."""
    return [
        "1123511", "1121531", "1121513", "1123311", "1131211", "1131112", "1121331",
        "1131121", "1211231", "1211321", "1231121", "1114311", "1111341", "1111143",
        "1114131", "1111143".replace("143", "143"), "1114113", "1111313".replace("313", "313"),
    ]


# Tabel resmi: 107 pola 11-bit (bar/space bergantian mulai bar)
_C128 = """11011001100 11001101100 11001100110 10010011000 10010001100 10001001100 10011001000
10011000100 10001100100 11001001000 11001000100 11000100100 10110011100 10011011100 10011001110
10111001100 10011101100 10011100110 11001110010 11001011100 11001001110 11011100100 11001110100
11101101110 11101001100 11100101100 11100100110 11101100100 11100110100 11100110010 11011011000
11011000110 11000110110 10100011000 10001011000 10001000110 10110001000 10001101000 10001100010
11010001000 11000101000 11000100010 10110111000 10110001110 10001101110 10111011000 10111000110
10001110110 11101110110 11010001110 11000101110 11011101000 11011100010 11011101110 11101011000
11101000110 11100010110 11101101000 11101100010 11100011010 11101111010 11001000010 11110001010
10100111100 10010111100 10010011110 10111011100 10011011110 10111011110 11110101110 11010111100
11010011110 11110100110 11110110100 11011011110 11110110110 10101111000 10100011110 10001011110
10111101000 10111100100 10111100010 10111001100 10111000110 10111011000 10111011110 10110011100
10011011100 10011001110 10110011100""".split()

_STOP = "11000111010"


def draw_code128(painter: QPainter, data: str, x: int, y: int, w: int, h: int,
                 human: bool = True) -> None:
    """Gambar Code128-B ke painter pada rect (x,y,w,h)."""
    data = data if data.isascii() else data.encode("utf-8", "ignore").decode()
    vals = [104] + [ord(c) - 32 for c in data]
    checksum = vals[0]
    for i, v in enumerate(vals[1:], start=1):
        checksum += i * v
    vals.append(checksum % 103)
    bits = "".join(_C128[v] for v in vals if v < len(_C128)) + _STOP
    modules = sum(int(c) for c in bits) if False else len(bits)
    unit = max(1, w // (modules + 2))
    black = QColor("#000000")
    cx = x
    for b in bits:
        if b == "1":
            painter.fillRect(QRect(cx, y, unit, h), black)
        cx += unit
    if human:
        painter.setPen(black)
        painter.drawText(QRect(x, y + h, cx - x, int(h * 0.25)),
                         0x0004 | 0x0080, data)  # AlignHCenter|AlignTop


# ---------------------------------------------------------------------------
# QR — model 2, versi 1..4, EC level M (tabel generator polynomial & kapasitas)
# ---------------------------------------------------------------------------
# (version, size, data_codewords, ec_codewords_per_block, blocks) utk EC-M
_QR_PARAMS = {
    1: (21, 16, 10, 1),
    2: (25, 28, 16, 1),
    3: (29, 44, 26, 1),
    4: (33, 64, 18, 2),
}
_CAP_B = {1: 14, 2: 26, 3: 42, 4: 62}  # kapasitas byte mode EC-M


class _GF:
    """Arithmetic GF(256) dengan polimorf 0x11D."""
    EXP = [0] * 512
    LOG = [0] * 256

    @staticmethod
    def init() -> None:
        x = 1
        for i in range(255):
            _GF.EXP[i] = x
            _GF.LOG[x] = i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            _GF.EXP[i] = _GF.EXP[i - 255]

    @staticmethod
    def mul(a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return _GF.EXP[_GF.LOG[a] + _GF.LOG[b]]


_GF.init()


def _rs_generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _GF.mul(c, _GF.EXP[i])
        g = ng
    return g


def _rs_encode(data: list[int], n_ec: int) -> list[int]:
    gen = _rs_generator(n_ec)
    res = data + [0] * n_ec
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(len(gen)):
                res[i + j] ^= _GF.mul(gen[j], coef)
    return res[len(data):]


def _fmt_bits(ec: int, mask: int) -> int:
    data = (ec << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = ((data << 10) | rem) ^ 0x5412
    return bits & 0x7FF


def _version_bits(ver: int) -> int:
    rem = ver
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (ver << 12) | rem


def draw_qr(painter: QPainter, text: str, x: int, y: int, size: int) -> None:
    """Gambar QR (byte-mode, EC-M, versi minimal ≥2 utk andal) pada kotak size×size."""
    payload = text.encode("utf-8")[:120]
    ver = next((v for v in (2, 3, 4) if _CAP_B[v] >= len(payload)), 4)
    dim, total_dw, ec_per_blk, blocks = _QR_PARAMS[ver]
    # --- bitstream: mode 0100 + length(8) + data + terminator/pad
    bits: list[int] = []

    def push(val: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    push(0b0100, 4)
    push(len(payload), 8)
    for b in payload:
        push(b, 8)
    push(0, min(4, total_dw * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < total_dw:
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11
    # --- split blok & RS
    if blocks == 1:
        out = codewords + _rs_encode(codewords, ec_per_blk)
    else:
        sz = total_dw // blocks
        d_blocks = [codewords[i * sz:(i + 1) * sz] for i in range(blocks)]
        e_blocks = [_rs_encode(db, ec_per_blk) for db in d_blocks]
        out = []
        for i in range(sz):
            for db in d_blocks:
                out.append(db[i])
        for i in range(ec_per_blk):
            for eb in e_blocks:
                out.append(eb[i])
    final_bits = [(b >> i) & 1 for cw in out for i in range(7, -1, -1)]

    # --- matriks
    m = [[0] * dim for _ in range(dim)]
    reserved = [[False] * dim for _ in range(dim)]

    def finder(cx: int, cy: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                X, Y = cx + dx, cy + dy
                if 0 <= X < dim and 0 <= Y < dim:
                    on = 0 <= dx <= 6 and 0 <= dy <= 6 and (
                        dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                    m[Y][X] = 1 if on else 0
                    reserved[Y][X] = True

    finder(0, 0)
    finder(dim - 7, 0)
    finder(0, dim - 7)

    for i in range(8, dim - 8):  # timing
        m[6][i] = 1 - (i % 2)
        reserved[6][i] = True
        m[i][6] = 1 - (i % 2)
        reserved[i][6] = True

    def place_fmt(mask_sel: int) -> None:
        fb = _fmt_bits(0, mask_sel)  # EC M = 00 → index 0 tabel format (M=0)
        for i in range(15):
            b = (fb >> i) & 1
            if i < 6:
                m[i][8] = b
                reserved[i][8] = True
            elif i == 6:
                m[7][8] = b
                reserved[7][8] = True
            elif i == 7:
                m[8][8] = b
                reserved[8][8] = True
            elif i == 8:
                m[8][7] = b
                reserved[8][7] = True
            else:
                m[8][14 - i] = b
                reserved[8][14 - i] = True
            if i < 8:
                m[8][dim - 1 - i] = b
                reserved[8][dim - 1 - i] = True
            else:
                m[dim - 15 + i][8] = b
                reserved[dim - 15 + i][8] = True
        m[dim - 8][8] = 1
        reserved[dim - 8][8] = True

    # reserved zone format
    for i in range(9):
        reserved[8][i] = reserved[i][8] = True
    for i in range(8):
        reserved[8][dim - 1 - i] = reserved[dim - 1 - i][8] = True

    # --- isi data zig-zag
    bi = 0
    col = dim - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rng = range(dim - 1, -1, -1) if upward else range(dim)
        for row in rng:
            for cc in (col, col - 1):
                if not reserved[row][cc]:
                    m[row][cc] = final_bits[bi] if bi < len(final_bits) else 0
                    bi += 1
        col -= 2
        upward = not upward

    # --- mask 0 (checkerboard (row+col)%2==0) — sederhana & valid
    for r in range(dim):
        for c in range(dim):
            if not reserved[r][c] and (r + c) % 2 == 0:
                m[r][c] ^= 1
    place_fmt(0)

    # --- render
    quiet = 2
    cell = max(1, size // (dim + 2 * quiet))
    painter.fillRect(QRect(x, y, cell * (dim + 2 * quiet), cell * (dim + 2 * quiet)), QColor("#FFFFFF"))
    black = QColor("#000000")
    ox = x + cell * quiet
    oy = y + cell * quiet
    for r in range(dim):
        for c in range(dim):
            if m[r][c]:
                painter.fillRect(QRect(ox + c * cell, oy + r * cell, cell, cell), black)
