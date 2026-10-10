"""Visión por computador básica con numpy: búsqueda de plantillas (correlación normalizada) y
detección de cambios en pantalla. Es lo que permite 'ver' dónde está cada botón de Cubase."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def gray(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        a = a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114
    return a


@dataclass
class Match:
    x: int  # esquina superior izquierda (coordenadas de la imagen buscada)
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def _window_sums(a: np.ndarray, h: int, w: int) -> np.ndarray:
    ii = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]


def match_template(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Mapa de correlación cruzada normalizada (-1..1) para cada posición válida."""
    I = gray(image)
    T = gray(template)
    H, W = I.shape
    h, w = T.shape
    if h > H or w > W:
        return np.full((0, 0), -1.0)
    t = T - T.mean()
    tnorm = np.sqrt((t * t).sum())
    fh, fw = 1 << int(np.ceil(np.log2(H + h))), 1 << int(np.ceil(np.log2(W + w)))
    F = np.fft.rfft2(I, (fh, fw))
    Ft = np.fft.rfft2(t[::-1, ::-1], (fh, fw))
    corr = np.fft.irfft2(F * Ft, (fh, fw))[h - 1:H, w - 1:W]
    s1 = _window_sums(I, h, w)
    s2 = _window_sums(I * I, h, w)
    var = np.maximum(s2 - s1 * s1 / (h * w), 0)
    den = np.sqrt(var) * tnorm
    with np.errstate(invalid="ignore", divide="ignore"):
        ncc = np.where(den > 1e-6, corr / den, 0.0)
    if tnorm < 1e-6:  # plantilla de color uniforme: se compara por diferencia absoluta de nivel
        ncc = 1 - np.abs(s1 / (h * w) - T.mean()) / 255.0
    return np.clip(ncc, -1, 1)


def find(image: np.ndarray, template: np.ndarray, threshold: float = 0.85, max_results: int = 5) -> list[Match]:
    """Coincidencias por encima del umbral, de mejor a peor, sin solaparse (supresión de no máximos)."""
    ncc = match_template(image, template)
    if ncc.size == 0:
        return []
    h, w = gray(template).shape
    out: list[Match] = []
    work = ncc.copy()
    for _ in range(max_results):
        idx = int(np.argmax(work))
        y, x = divmod(idx, work.shape[1])
        s = float(work[y, x])
        if s < threshold:
            break
        out.append(Match(int(x), int(y), w, h, round(s, 4)))
        work[max(0, y - h // 2):y + h // 2 + 1, max(0, x - w // 2):x + w // 2 + 1] = -1
    return out


def changed_fraction(a: np.ndarray, b: np.ndarray, region: tuple[int, int, int, int] | None = None,
                     tol: float = 12.0) -> float:
    """Fracción de píxeles que cambian entre dos capturas (opcionalmente en una región x, y, w, h)."""
    A, B = gray(a), gray(b)
    if A.shape != B.shape:
        return 1.0
    if region:
        x, y, w, h = region
        A, B = A[y:y + h, x:x + w], B[y:y + h, x:x + w]
    return float(np.mean(np.abs(A - B) > tol)) if A.size else 0.0
