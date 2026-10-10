import numpy as np

from aiengineer.screen.png import read_png, write_png
from aiengineer.screen.vision import changed_fraction, find


def test_png_roundtrip(tmp_path):
    img = np.random.default_rng(0).integers(0, 256, (37, 53, 3), dtype=np.uint8)
    assert np.array_equal(read_png(write_png(tmp_path / "a.png", img)), img)


def test_template_found_with_noise():
    rng = np.random.default_rng(1)
    screen = rng.integers(0, 256, (300, 400, 3)).astype(np.uint8)
    tpl = screen[120:150, 200:260].copy()
    noisy = np.clip(screen.astype(int) + rng.integers(-8, 9, screen.shape), 0, 255).astype(np.uint8)
    m = find(noisy, tpl, threshold=0.8)
    assert m and (m[0].x, m[0].y) == (200, 120) and m[0].center == (230, 135)
    assert not find(rng.integers(0, 256, (300, 400, 3)).astype(np.uint8), tpl, threshold=0.8)


def test_changed_fraction():
    a = np.zeros((50, 50, 3), np.uint8)
    b = a.copy()
    b[:25] = 255
    assert abs(changed_fraction(a, b) - 0.5) < 1e-9 and changed_fraction(a, a) == 0
