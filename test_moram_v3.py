"""Conformance tests for MORAM SPEC v0.3 §14 (indirect memory)."""
import unittest

from moram import Moram, MoramTrap, parse_program


class TestIndirect(unittest.TestCase):
    def test_loadi_basic(self):
        m = Moram()
        m.run_text("TINSERT 42\nSTORE 5\nTINSERT 5\nLOADI")
        self.assertEqual(m.view(), [42])

    def test_loadi_unset_is_zero(self):
        m = Moram()
        m.run_text("TINSERT 777\nLOADI")
        self.assertEqual(m.view(), [0])

    def test_loadi_empty(self):
        m = Moram()
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("LOADI")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_loadi_negative(self):
        m = Moram()
        m.run_text("TINSERT 9\nTINSERT -1")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("LOADI")
        self.assertEqual(ctx.exception.code, "INVALID_ADDR")
        self.assertEqual(m.view(), [9])  # address consumed, nothing pushed

    def test_storei_basic(self):
        m = Moram()
        m.run_text("TINSERT 5\nTINSERT 42\nSTOREI")
        self.assertEqual(m.view(), [])
        self.assertEqual(m.mem[5], 42)

    def test_storei_needs_two(self):
        m = Moram()
        m.run_text("TINSERT 5")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("STOREI")
        self.assertEqual(ctx.exception.code, "EMPTY_POP")

    def test_storei_negative(self):
        m = Moram()
        m.run_text("TINSERT -3\nTINSERT 42")
        with self.assertRaises(MoramTrap) as ctx:
            m.run_text("STOREI")
        self.assertEqual(ctx.exception.code, "INVALID_ADDR")
        self.assertEqual(m.view(), [])

    def test_frame_slot_pattern(self):
        # The exact pattern the K2 emitter uses: FP in MEM[1], slot k
        # addressed as FP+k, accessed indirectly.
        m = Moram()
        m.run_text("\n".join([
            "TINSERT 100", "STORE 1",      # FP = 100
            "TINSERT 7",                   # value
            "LOAD 1", "TINSERT 3", "ADD",  # address 103
            "STOREI",                      # MEM[103] = 7
            "LOAD 1", "TINSERT 3", "ADD",
            "LOADI",                       # -> 7
            "OUT",
        ]))
        self.assertEqual(m.out, [7])

    def test_parser(self):
        self.assertEqual(parse_program("LOADI\nSTOREI"), [("LOADI",), ("STOREI",)])
        with self.assertRaises(ValueError):
            parse_program("LOADI 5")


if __name__ == "__main__":
    unittest.main()
