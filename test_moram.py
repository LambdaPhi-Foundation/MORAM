"""Conformance tests for MORAM SPEC v0.1 §9. stdlib unittest only."""
import unittest
from collections import deque

from moram import Moram, MoramTrap, parse_program


class TestFlipIdentity(unittest.TestCase):
    def test_double_flip_empty(self):
        m = Moram()
        m.flip()
        m.flip()
        self.assertEqual(m.view(), [])
        self.assertEqual(m.orientation, 0)

    def test_double_flip_singleton(self):
        m = Moram()
        m.tinsert(9)
        v0 = m.view()
        m.flip()
        self.assertEqual(m.view(), v0)  # observable no-op on singleton
        m.flip()
        self.assertEqual(m.view(), v0)
        self.assertEqual(m.orientation, 0)

    def test_double_flip_multi_view_restored_but_o_toggles(self):
        m = Moram()
        for v in (1, 2, 3):
            m.tinsert(v)
        m.flip()
        # same multiset, reversed view (labels swapped)
        self.assertEqual(m.view(), [3, 2, 1])
        m.flip()
        self.assertEqual(m.view(), [1, 2, 3])
        self.assertEqual(m.orientation, 0)

    def test_flip_is_o1_not_data_move(self):
        m = Moram()
        for v in (1, 2, 3):
            m.tinsert(v)
        phys0 = m.physical()
        m.flip()
        self.assertEqual(m.physical(), phys0)  # storage untouched


class TestRedundancy(unittest.TestCase):
    def test_hinsert_equiv_flip_tinsert_flip(self):
        for o in (0, 1):
            a = Moram()
            b = Moram()
            for v in (1, 2):
                a.tinsert(v)
                b.tinsert(v)
            if o:
                a.flip()
                b.flip()
            a.hinsert(99)
            b.flip()
            b.tinsert(99)
            b.flip()
            self.assertEqual(a.view(), b.view())
            self.assertEqual(a.orientation, b.orientation)

    def test_head_drop_equiv(self):
        m1, m2 = Moram(), Moram()
        for v in (1, 2, 3):
            m1.tinsert(v)
            m2.tinsert(v)
        m1.drop_head()
        m2.flip()
        m2.drop_t()
        m2.flip()
        self.assertEqual(m1.view(), m2.view())


class TestReductions(unittest.TestCase):
    def test_sum_123(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nTINSERT 3\nADD\nADD")
        self.assertEqual(m.view(), [6])

    def test_tree(self):
        # (2+5)*(1+4) = 35
        m = Moram()
        m.run_text("TINSERT 2\nTINSERT 5\nTINSERT 1\nTINSERT 4\nADD\nADD\nMUL")
        self.assertEqual(m.view(), [35])


class TestSubOrder(unittest.TestCase):
    def test_stack_like(self):
        m = Moram()
        m.run_text("TINSERT 5\nTINSERT 3\nSUB")
        self.assertEqual(m.view(), [2])  # b - a = 5 - 3, not -2

    def test_sub_flip_sign(self):
        # physical [10, 3]: o=0 tail=right -> 10-3=7 to head(left)
        m = Moram()
        m.tinsert(10)
        m.tinsert(3)
        m.sub()
        self.assertEqual(m.view(), [7])
        # same physical store, o=1 tail=left -> 3-10=-7
        m2 = Moram()
        m2.tinsert(10)
        m2.tinsert(3)
        m2.flip()
        m2.sub()
        self.assertEqual(m2.view(), [-7])


class TestTraps(unittest.TestCase):
    def test_underflow_leaves_state_unchanged(self):
        for prog, setup in (
            ("ADD", []),
            ("MUL", [4]),
            ("SUB", [4]),
            ("ADD", []),
        ):
            m = Moram()
            for v in setup:
                m.tinsert(v)
            before_v, before_o = m.view(), m.orientation
            with self.assertRaises(MoramTrap) as ctx:
                m.run_text(prog)
            self.assertEqual(ctx.exception.code, "UNDERFLOW")
            self.assertEqual(m.view(), before_v)
            self.assertEqual(m.orientation, before_o)

    def test_empty_pop(self):
        for op in ("DROP_T", "POP_T"):
            m = Moram()
            with self.assertRaises(MoramTrap) as ctx:
                m.run_text(op)
            self.assertEqual(ctx.exception.code, "EMPTY_POP")


class TestDupGap(unittest.TestCase):
    def test_no_core_only_dup_of_computed_value(self):
        # BFS over closed-deque states (no host reuse of POP_T values):
        # immediates allowed are {0,1}; start [7] is "computed" (7 not insertable).
        # DROP_T allowed, POP_T allowed only as discard (value lost).
        # Depth <= 4. Assert [7,7] view never appears.
        start = (7,)
        frontier = {start}
        seen = {start}
        allowed_imms = (0, 1)

        def step_state(state, o, op):
            d = deque(state)  # stored in label view? model label view directly
            # represent state as label-view tuple + o; FLIP reverses view
            if op == "FLIP":
                return (tuple(reversed(d)), o ^ 1)
            if op[0] == "H":
                return (((op[1],) + tuple(d)), o)
            if op[0] == "T":
                return ((tuple(d) + (op[1],)), o)
            if op == "DROP":
                if not d:
                    return None
                return (tuple(list(d)[:-1]), o)  # pop tail = right of view
            if op in ("ADD", "MUL", "SUB"):
                if len(d) < 2:
                    return None
                l = list(d)
                a, b = l[-1], l[-2]
                rest = tuple(l[:-2])
                r = b + a if op == "ADD" else (b * a if op == "MUL" else b - a)
                return (((r,) + rest, o))  # push head = left of view
            raise AssertionError(op)

        ops = ["FLIP", "DROP", "ADD", "MUL", "SUB"] + [
            ("H", v) for v in allowed_imms
        ] + [("T", v) for v in allowed_imms]
        # o is folded into view reversal; track (view,) only with flip included
        cur = {(start, 0)}
        for _ in range(4):
            nxt = set()
            for view, o in cur:
                for op in ops:
                    r = step_state(view, o, op)
                    if r is not None and r not in seen:
                        seen.add(r)
                        nxt.add(r)
            cur |= nxt
        self.assertNotIn(((7, 7), 0), seen)
        self.assertNotIn(((7, 7), 1), seen)

    def test_host_temp_dup_is_the_only_path(self):
        m = Moram()
        m.tinsert(2)
        m.tinsert(5)
        m.add()  # computed 7 at head
        self.assertEqual(m.view(), [7])
        v = m.pop_t()  # escape to host
        m.tinsert(v)
        m.tinsert(v)
        self.assertEqual(m.view(), [7, 7])


class TestMovement(unittest.TestCase):
    def test_true_rotation_preserves_multiset(self):
        m = Moram()
        for v in (1, 2, 3):
            m.tinsert(v)
        m.rotate_tail_to_head()  # tail 3 -> head
        self.assertEqual(sorted(m.view()), [1, 2, 3])
        self.assertEqual(len(m.view()), 3)
        self.assertEqual(m.view()[0], 3)

    def test_head_pop_emulation(self):
        m = Moram()
        for v in (1, 2, 3):
            m.tinsert(v)
        o0 = m.orientation
        v = m.pop_head()
        self.assertEqual(v, 1)
        self.assertEqual(m.view(), [2, 3])
        self.assertEqual(m.orientation, o0)


class TestParserAliases(unittest.TestCase):
    def test_rot_remove_aliases(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nROT\nREMOVE")
        # flipped: view was [2,1], DROP_T removes view-tail=1 -> [2]
        self.assertEqual(m.view(), [2])

    def test_comments_case_blanks(self):
        prog = parse_program("# comment\n  tinsert 4 ; trailing\n\nHINSERT 5\nadd\n")
        self.assertEqual(prog, [("TINSERT", 4), ("HINSERT", 5), ("ADD",)])

    def test_bad_op_rejected(self):
        with self.assertRaises(ValueError):
            parse_program("PUSH 1")
        with self.assertRaises(ValueError):
            parse_program("HINSERT")


if __name__ == "__main__":
    unittest.main()
