"""Conformance tests for MORAM SPEC v0.2 §12. stdlib unittest only."""
import unittest

from moram import Moram, MoramTrap, parse_program

COUNTDOWN = """
  TINSERT 3
  STORE 0
loop:
  LOAD 0
  JZ done
  DROP_T
  LOAD 0
  OUT
  LOAD 0
  TINSERT 1
  SUB
  STORE 0
  JMP loop
done:
  DROP_T
  HALT
"""

FACTORIAL5 = """
  TINSERT 5
  STORE 0
  TINSERT 1
  STORE 1
loop:
  LOAD 0
  JZ done
  DROP_T
  LOAD 1
  LOAD 0
  MUL
  STORE 1
  LOAD 0
  TINSERT 1
  SUB
  STORE 0
  JMP loop
done:
  DROP_T
  LOAD 1
  OUT
  HALT
"""


class TestDataMovement(unittest.TestCase):
    def test_dup_t(self):
        m = Moram()
        m.run_text("TINSERT 7\nDUP_T")
        self.assertEqual(m.view(), [7, 7])

    def test_dup_t_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("DUP_T")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_swap_t(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nSWAP_T")
        self.assertEqual(m.view(), [2, 1])

    def test_swap_t_underflow(self):
        m = Moram()
        m.run_text("TINSERT 1")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("SWAP_T")
        self.assertEqual(ctx.exception.code, "UNDERFLOW")
        self.assertEqual(m.view(), [1])  # atomic

    def test_over_t(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nTINSERT 3\nOVER_T")
        self.assertEqual(m.view(), [1, 2, 3, 2])

    def test_over_t_pair(self):
        m = Moram()
        m.run_text("TINSERT 8\nTINSERT 9\nOVER_T")
        self.assertEqual(m.view(), [8, 9, 8])

    def test_over_t_underflow(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("TINSERT 1\nOVER_T")
        self.assertEqual(ctx.exception.code, "UNDERFLOW")

    def test_pop_h_drop_h(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nTINSERT 3")
        self.assertEqual(m.pop_h(), 1)
        self.assertEqual(m.view(), [2, 3])
        m.drop_h()
        self.assertEqual(m.view(), [3])

    def test_pop_h_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("POP_H")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_ferry_head_to_tail(self):
        m = Moram()
        m.run_text("TINSERT 1\nTINSERT 2\nADD")  # result [3] at head
        v = m.pop_h()
        m.tinsert(v)
        self.assertEqual(m.view(), [3])

    def test_dup_closes_fanout_gap(self):
        # x*x + x with x=5 -> 30, no host temp needed anymore
        m = Moram()
        m.run_text("TINSERT 5\nDUP_T\nDUP_T\nMUL\nADD")
        # [5] -> [5,5] -> [5,5,5] -> MUL pops 5,5 pushes 25 head -> [25,5] -> ADD -> [30]
        self.assertEqual(m.view(), [30])


class TestArithmeticExt(unittest.TestCase):
    def test_div_trunc_vectors(self):
        for b, a, q in ((7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3)):
            m = Moram()
            m.run_text(f"TINSERT {b}\nTINSERT {a}\nDIV")
            self.assertEqual(m.view(), [q], f"{b}/{a}")

    def test_mod_vectors(self):
        for b, a, r in ((7, 2, 1), (-7, 2, -1), (7, -2, 1), (-7, -2, -1)):
            m = Moram()
            m.run_text(f"TINSERT {b}\nTINSERT {a}\nMOD")
            self.assertEqual(m.view(), [r], f"{b}%{a}")

    def test_div_zero_atomic(self):
        for op in ("DIV", "MOD"):
            m = Moram()
            m.run_text("TINSERT 7\nTINSERT 0")
            before = m.view()
            with self.assertRaises(MoramTrap) as ctx:
                m.run_text(op)
            self.assertEqual(ctx.exception.code, "DIV_ZERO")
            self.assertEqual(m.view(), before)

    def test_div_underflow(self):
        m = Moram()
        m.run_text("TINSERT 1")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("DIV")
        self.assertEqual(ctx.exception.code, "UNDERFLOW")

    def test_neg(self):
        m = Moram()
        m.run_text("TINSERT 4\nNEG")
        self.assertEqual(m.view(), [-4])

    def test_neg_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("NEG")
        self.assertEqual(ctx.exception.code, "UNDERFLOW")

    def test_eq_lt(self):
        m = Moram()
        m.run_text("TINSERT 5\nTINSERT 5\nEQ")
        self.assertEqual(m.view(), [1])
        m = Moram()
        m.run_text("TINSERT 5\nTINSERT 6\nEQ")
        self.assertEqual(m.view(), [0])
        m = Moram()
        m.run_text("TINSERT 3\nTINSERT 9\nLT")
        self.assertEqual(m.view(), [1])
        m = Moram()
        m.run_text("TINSERT 9\nTINSERT 3\nLT")
        self.assertEqual(m.view(), [0])

    def test_eq_underflow(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("TINSERT 1\nEQ")
        self.assertEqual(ctx.exception.code, "UNDERFLOW")


class TestMemory(unittest.TestCase):
    def test_load_unset_is_zero(self):
        m = Moram()
        m.run_text("LOAD 999")
        self.assertEqual(m.view(), [0])

    def test_roundtrip_to_tail(self):
        m = Moram()
        m.run_text("TINSERT 42\nSTORE 3\nLOAD 3")
        self.assertEqual(m.view(), [42])
        self.assertEqual(m.mem[3], 42)

    def test_store_consumes(self):
        m = Moram()
        m.run_text("TINSERT 11\nSTORE 0")
        self.assertEqual(m.view(), [])

    def test_invalid_addr(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("LOAD -1")
        self.assertEqual(ctx.exception.code, "INVALID_ADDR")
        m = Moram()
        m.run_text("TINSERT 1")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("STORE -2")
        self.assertEqual(ctx.exception.code, "INVALID_ADDR")
        self.assertEqual(m.view(), [1])  # atomic, no pop

    def test_store_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("STORE 0")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")


class TestBranches(unittest.TestCase):
    def test_jz_taken_nondestructive(self):
        m = Moram()
        m.run_text("TINSERT 0\nJZ done\nTINSERT 99\ndone:\nHALT")
        self.assertEqual(m.view(), [0])  # peeked 0 still there

    def test_jz_not_taken_nondestructive(self):
        m = Moram()
        m.run_text("TINSERT 7\nJZ done\nHALT\ndone:\nHALT")
        self.assertEqual(m.view(), [7])

    def test_jnz_taken(self):
        m = Moram()
        m.run_text("TINSERT 4\nJNZ yes\nTINSERT 0\nyes:\nHALT")
        self.assertEqual(m.view(), [4])

    def test_jnz_not_taken(self):
        m = Moram()
        m.run_text("TINSERT 0\nJNZ yes\nHALT\nyes:\nHALT")
        self.assertEqual(m.view(), [0])

    def test_jz_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("JZ 0")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_jmp(self):
        m = Moram()
        m.run_text("JMP skip\nTINSERT 1\nskip:\nTINSERT 2")
        self.assertEqual(m.view(), [2])

    def test_invalid_jump(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("JMP 999")
        self.assertEqual(ctx.exception.code, "INVALID_JUMP")
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("JMP -1")
        self.assertEqual(ctx.exception.code, "INVALID_JUMP")

    def test_step_limit(self):
        m = Moram()
        with self.assertRaises(RuntimeError):
            m.run_text("loop:\nJMP loop", max_steps=10)


class TestCalls(unittest.TestCase):
    def test_call_ret_single(self):
        m = Moram()
        m.run_text("TINSERT 10\nTINSERT 20\nCALL sum2\nOUT\nHALT\nsum2:\nADD\nRET")
        self.assertEqual(m.output(), [30])
        self.assertEqual(m.view(), [])

    def test_call_nested(self):
        prog = """
          TINSERT 5
          CALL twice
          OUT
          HALT
        twice:
          CALL once
          CALL once
          RET
        once:
          TINSERT 1
          ADD
          RET
        """
        m = Moram()
        m.run_text(prog)
        self.assertEqual(m.output(), [7])

    def test_ret_underflow(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("RET")
        self.assertEqual(ctx.exception.code, "RSTACK_UNDERFLOW")

    def test_call_bad_target_atomic(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("CALL 50")
        self.assertEqual(ctx.exception.code, "INVALID_JUMP")
        self.assertEqual(m.rstack, [])


class TestPrograms(unittest.TestCase):
    def test_countdown(self):
        m = Moram()
        m.run_text(COUNTDOWN)
        self.assertEqual(m.output(), [3, 2, 1])
        self.assertEqual(m.view(), [])

    def test_factorial5(self):
        m = Moram()
        m.run_text(FACTORIAL5)
        self.assertEqual(m.output(), [120])

    def test_halt_stops(self):
        m = Moram()
        m.run_text("TINSERT 1\nHALT\nTINSERT 2")
        self.assertEqual(m.view(), [1])

    def test_implicit_halt(self):
        m = Moram()
        m.run_text("TINSERT 1")
        self.assertEqual(m.view(), [1])

    def test_v01_still_works(self):
        m = Moram()
        m.run_text("TINSERT 2\nTINSERT 5\nTINSERT 1\nTINSERT 4\nADD\nADD\nMUL")
        self.assertEqual(m.view(), [35])


class TestIO(unittest.TestCase):
    def test_in_out(self):
        m = Moram()
        m.run_text("IN\nIN\nADD\nOUT", inputs=[20, 22])
        self.assertEqual(m.output(), [42])

    def test_io_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("IN")
        self.assertEqual(ctx.exception.code, "IO_EMPTY")

    def test_out_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("OUT")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_dup_out_keeps_copy(self):
        m = Moram()
        m.run_text("TINSERT 9\nDUP_T\nOUT")
        self.assertEqual(m.output(), [9])
        self.assertEqual(m.view(), [9])


class TestParserLabels(unittest.TestCase):
    def test_label_resolve(self):
        prog = parse_program("JMP end\nTINSERT 1\nend:\nHALT")
        self.assertEqual(prog, [("JMP", 2), ("TINSERT", 1), ("HALT",)])

    def test_same_line_label(self):
        prog = parse_program("loop: LOAD 0")
        self.assertEqual(prog, [("LOAD", 0)])

    def test_unknown_label(self):
        with self.assertRaises(ValueError):
            parse_program("JMP nowhere")

    def test_duplicate_label(self):
        with self.assertRaises(ValueError):
            parse_program("a:\nHALT\na:\nHALT")

    def test_comments_case(self):
        prog = parse_program("# c\n  load 5 ; trailing\nDUP_t\n")
        self.assertEqual(prog, [("LOAD", 5), ("DUP_T",)])


if __name__ == "__main__":
    unittest.main()
