"""Grid Slicer's shuffle: blocks trade cells and shift at random, reproducibly."""
import _isolate  # first: settings and caches stay in a temporary folder
import unittest

import numpy as np
import trimesh

from atlas_toolkit.tools.cube_grid_slicer import cube_grid_slicer as cgs


def sliced(n=3, gap=2.0):
    box = trimesh.creation.box(extents=(30, 30, 30))
    parts, cells = cgs.slice_grid(box, n, gap)
    _, cell, gap_abs = cgs.grid(box, n, gap)
    return parts, cells, cell, cell + gap_abs


def centres(parts):
    return np.array([m.bounds.mean(axis=0) for m in parts])


class ShuffleTests(unittest.TestCase):
    def test_nothing_asked_nothing_moves(self):
        parts, cells, cell, step = sliced()
        before = centres(parts)
        out = cgs.shuffle(parts, cells, step, cell)
        np.testing.assert_allclose(centres(parts), before)
        self.assertEqual(out, cells)

    def test_full_shuffle_moves_every_block_onto_another_cell(self):
        parts, cells, cell, step = sliced()
        before = centres(parts)
        out = cgs.shuffle(parts, cells, step, cell, share=1.0, seed=3)
        self.assertEqual(sorted(out), sorted(cells))                 # a permutation of the grid
        self.assertTrue(all(a != b for a, b in zip(out, cells)))     # nobody stays home
        # every block now sits where the block of its new cell was
        home = {c: x for c, x in zip(cells, before)}
        np.testing.assert_allclose(centres(parts), [home[c] for c in out], atol=1e-9)

    def test_trading_within_a_layer_keeps_heights(self):
        parts, cells, cell, step = sliced(4)
        before = centres(parts)
        out = cgs.shuffle(parts, cells, step, cell, share=1.0, within="layer", seed=1)
        self.assertTrue(all(a[2] == b[2] for a, b in zip(out, cells)))
        np.testing.assert_allclose(centres(parts)[:, 2], before[:, 2], atol=1e-9)

    def test_trading_within_a_column_keeps_the_spot(self):
        parts, cells, cell, step = sliced(4)
        before = centres(parts)
        cgs.shuffle(parts, cells, step, cell, share=1.0, within="column", seed=1)
        np.testing.assert_allclose(centres(parts)[:, :2], before[:, :2], atol=1e-9)

    def test_shift_is_bounded_and_follows_the_axes(self):
        parts, cells, cell, step = sliced()
        before = centres(parts)
        cgs.shuffle(parts, cells, step, cell, shift=0.5, axes="xy", seed=2)
        d = centres(parts) - before
        self.assertTrue(np.all(np.abs(d[:, :2]) <= 0.5 * cell[:2] + 1e-9))
        self.assertGreater(np.abs(d[:, :2]).max(), 0.0)
        np.testing.assert_allclose(d[:, 2], 0.0, atol=1e-9)

    def test_same_seed_same_result(self):
        a, cells, cell, step = sliced()
        b, _, _, _ = sliced()
        cgs.shuffle(a, cells, step, cell, share=0.6, shift=0.3, seed=7)
        cgs.shuffle(b, cells, step, cell, share=0.6, shift=0.3, seed=7)
        np.testing.assert_allclose(centres(a), centres(b))


if __name__ == "__main__":
    unittest.main()
