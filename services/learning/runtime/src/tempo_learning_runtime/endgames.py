"""Validation of saved/published material definitions; no position calculation."""

def normalized_material(value: str) -> str:
    pieces = value.upper().replace("&", "").replace("+", "").replace(" ", "")
    if pieces.count("K") != 1 or any(piece not in "KQRBNP" for piece in pieces):
        raise ValueError("Each side needs exactly one king and standard piece letters")
    return "K" + "".join(sorted(pieces.replace("K", ""), key="QRBNP".index))
