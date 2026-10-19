"""MORAM deque VM — simulator (SPEC v0.2).

State = (D, o, PC, MEM, RSTACK, IN, OUT, HALTED).
D is a deque of bigint, o the orientation bit (FLIP toggles O(1)).
ALU pops tail-tail, pushes head. SUB = next-to-tail minus tail-top.
DIV truncates toward zero. JZ/JNZ peek (non-destructive).
LOAD pushes to tail; STORE pops tail. RSTACK is separate from D/MEM.
All traps atomic (no partial mutation). Text assembly is canonical.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["MoramTrap", "Moram", "parse_program", "OPCODE_NUMBERS"]

# Canonical op names. Aliases resolved at parse time:
#   ROT -> FLIP, REMOVE -> DROP_T
CANONICAL_OPS = frozenset(
    {
        "HINSERT", "TINSERT", "DROP_T", "POP_T", "FLIP", "ADD", "MUL", "SUB",
        "DUP_T", "SWAP_T", "OVER_T", "POP_H", "DROP_H",
        "DIV", "MOD", "NEG", "EQ", "LT",
        "JMP", "JZ", "JNZ", "CALL", "RET", "HALT",
        "LOAD", "STORE", "IN", "OUT",
        "LOADI", "STOREI",
    }
)
ALIASES = {"ROT": "FLIP", "REMOVE": "DROP_T"}

# Reserved opcode numbers (SPEC §8, for future binary codec).
OPCODE_NUMBERS = {
    "HALT": 0x00, "HINSERT": 0x01, "TINSERT": 0x02, "DROP_T": 0x03,
    "POP_T": 0x04, "FLIP": 0x05, "ADD": 0x06, "MUL": 0x07, "SUB": 0x08,
    "DUP_T": 0x09, "SWAP_T": 0x0A, "OVER_T": 0x0B, "POP_H": 0x0C,
    "DROP_H": 0x0D, "DIV": 0x0E, "MOD": 0x0F, "NEG": 0x10, "EQ": 0x11,
    "LT": 0x12, "JMP": 0x13, "JZ": 0x14, "JNZ": 0x15, "CALL": 0x16,
    "RET": 0x17, "LOAD": 0x18, "STORE": 0x19, "IN": 0x1A, "OUT": 0x1B,
    "LOADI": 0x1C, "STOREI": 0x1D,
}

_LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_INT_RE = re.compile(r"^[+-]?\d+$")

# Program op: (name,) or (name, operand)
Op = Tuple


class MoramTrap(Exception):
    """Fail-stop trap. .code names the trap (see SPEC §10)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{(': ' + detail) if detail else ''}")
        self.code = code
        self.detail = detail


def _trunc_div(b: int, a: int) -> int:
    """Truncate-toward-zero division (C-like). a != 0."""
    q = abs(b) // abs(a)
    if (b < 0) != (a < 0):
        q = -q
    return q


class Moram:
    """Deque machine with PC, flat memory, return stack, and I/O queues."""

    def __init__(self) -> None:
        self._store: Deque[int] = deque()
        self.o: int = 0
        self.pc: int = 0
        self.mem: Dict[int, int] = {}
        self.rstack: List[int] = []
        self.in_queue: Deque[int] = deque()
        self.out: List[int] = []
        self.halted: bool = False
        self._jumped: bool = False

    # -- low-level directional primitives (label-relative) --
    def _push_head(self, v: int) -> None:
        if self.o == 0:
            self._store.appendleft(int(v))
        else:
            self._store.append(int(v))

    def _push_tail(self, v: int) -> None:
        if self.o == 0:
            self._store.append(int(v))
        else:
            self._store.appendleft(int(v))

    def _pop_tail(self) -> int:
        if not self._store:
            raise MoramTrap("EMPTY_POP", "pop from empty deque")
        if self.o == 0:
            return self._store.pop()
        return self._store.popleft()

    def _peek_tail(self) -> int:
        if not self._store:
            raise MoramTrap("EMPTY_POP", "peek empty deque")
        if self.o == 0:
            return self._store[-1]
        return self._store[0]

    def _pop_head(self) -> int:
        if not self._store:
            raise MoramTrap("EMPTY_POP", "pop head from empty deque")
        if self.o == 0:
            return self._store.popleft()
        return self._store.pop()

    # -- core ISA (v0.1) --
    def hinsert(self, v: int) -> None:
        self._push_head(int(v))

    def tinsert(self, v: int) -> None:
        self._push_tail(int(v))

    def drop_t(self) -> None:
        self._pop_tail()  # discard; raises EMPTY_POP if empty

    def pop_t(self) -> int:
        return self._pop_tail()

    def flip(self) -> None:
        self.o ^= 1

    # aliases
    rot = flip
    remove = drop_t  # bare REMOVE = DROP_T per SPEC §7

    def _alu(self, fn, name: str) -> None:
        # Fail-stop: check first so state is unchanged on trap.
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"{name} needs 2 operands, has {len(self._store)}")
        a = self._pop_tail()  # top = tail-most
        b = self._pop_tail()  # next-to-tail
        self._push_head(fn(b, a))

    def add(self) -> None:
        self._alu(lambda b, a: b + a, "ADD")

    def mul(self) -> None:
        self._alu(lambda b, a: b * a, "MUL")

    def sub(self) -> None:
        self._alu(lambda b, a: b - a, "SUB")

    # -- derived ops built ONLY from core ops (kept for compat/tests) --
    def pop_head(self) -> int:
        """Head-pop emulation: FLIP; POP_T; FLIP. Restores o."""
        self.flip()
        try:
            return self.pop_t()
        finally:
            self.flip()

    def drop_head(self) -> None:
        self.flip()
        try:
            self.drop_t()
        finally:
            self.flip()

    def rotate_tail_to_head(self) -> None:
        """Length-preserving move of tail element to head (needs host temp)."""
        v = self.pop_t()
        self.hinsert(v)

    # -- data movement (v0.2) --
    def dup_t(self) -> None:
        v = self._peek_tail()  # raises EMPTY_POP, no mutation
        self._push_tail(v)

    def swap_t(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"SWAP_T needs 2, has {len(self._store)}")
        a = self._pop_tail()
        b = self._pop_tail()
        self._push_tail(a)
        self._push_tail(b)

    def over_t(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"OVER_T needs 2, has {len(self._store)}")
        a = self._pop_tail()
        b = self._peek_tail()
        self._push_tail(a)
        self._push_tail(b)

    def pop_h(self) -> int:
        return self._pop_head()

    def drop_h(self) -> None:
        self._pop_head()

    # -- arithmetic extension (v0.2) --
    def div(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"DIV needs 2, has {len(self._store)}")
        if self._peek_tail() == 0:
            raise MoramTrap("DIV_ZERO", "divisor (tail-top) is zero")
        a = self._pop_tail()
        b = self._pop_tail()
        self._push_head(_trunc_div(b, a))

    def mod(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"MOD needs 2, has {len(self._store)}")
        if self._peek_tail() == 0:
            raise MoramTrap("DIV_ZERO", "modulus (tail-top) is zero")
        a = self._pop_tail()
        b = self._pop_tail()
        self._push_head(b - _trunc_div(b, a) * a)

    def neg(self) -> None:
        if len(self._store) < 1:
            raise MoramTrap("UNDERFLOW", "NEG needs 1, has 0")
        a = self._pop_tail()
        self._push_head(-a)

    def eq(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"EQ needs 2, has {len(self._store)}")
        a = self._pop_tail()
        b = self._pop_tail()
        self._push_head(1 if b == a else 0)

    def lt(self) -> None:
        if len(self._store) < 2:
            raise MoramTrap("UNDERFLOW", f"LT needs 2, has {len(self._store)}")
        a = self._pop_tail()
        b = self._pop_tail()
        self._push_head(1 if b < a else 0)

    # -- memory / I/O (v0.2) --
    def load(self, addr: int) -> None:
        addr = int(addr)
        if addr < 0:
            raise MoramTrap("INVALID_ADDR", f"LOAD {addr}")
        self._push_tail(self.mem.get(addr, 0))

    def store(self, addr: int) -> None:
        addr = int(addr)
        if addr < 0:
            raise MoramTrap("INVALID_ADDR", f"STORE {addr}")
        v = self._pop_tail()  # EMPTY_POP before any MEM write
        self.mem[addr] = v

    # -- indirect memory (v0.3): address taken from the deque tail-top --
    def loadi(self) -> None:
        a = self._pop_tail()  # EMPTY_POP if empty
        if a < 0:
            raise MoramTrap("INVALID_ADDR", f"LOADI {a}")
        self._push_tail(self.mem.get(a, 0))

    def storei(self) -> None:
        v = self._pop_tail()  # EMPTY_POP if empty
        a = self._pop_tail()  # EMPTY_POP if only the value was present
        if a < 0:
            raise MoramTrap("INVALID_ADDR", f"STOREI {a}")
        self.mem[a] = v

    def in_op(self) -> None:
        if not self.in_queue:
            raise MoramTrap("IO_EMPTY", "IN with no input left")
        self._push_tail(self.in_queue.popleft())

    def out_op(self) -> None:
        self.out.append(self._pop_tail())

    def feed(self, values: Iterable[int]) -> None:
        """Append input words to IN queue."""
        for v in values:
            self.in_queue.append(int(v))

    # -- inspection --
    def __len__(self) -> int:
        return len(self._store)

    @property
    def orientation(self) -> int:
        return self.o

    def view(self) -> List[int]:
        """Label view [h ... t] left-to-right in current orientation."""
        if self.o == 0:
            return list(self._store)
        return list(reversed(self._store))

    def physical(self) -> List[int]:
        """Physical store left-to-right, ignoring o. For SUBxFLIP tests."""
        return list(self._store)

    def output(self) -> List[int]:
        return list(self.out)

    def reset(self) -> None:
        self._store.clear()
        self.o = 0
        self.pc = 0
        self.mem.clear()
        self.rstack.clear()
        self.in_queue.clear()
        self.out.clear()
        self.halted = False
        self._jumped = False
        self._prog_len = 0

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Moram(view={self.view()}, o={self.o}, pc={self.pc})"

    # -- program execution --
    def step(self, op: Sequence) -> Optional[int]:
        """Execute one op tuple. Control ops update self.pc.

        Returns POP_T/POP_H value if the op produced one, else None.
        Jump targets must be resolved integer indices (see parse_program).
        Bounds are checked here; bad targets raise INVALID_JUMP atomically.
        Sets self._jumped True when a jump/call/ret is taken (even self-jumps).
        """
        self._jumped = False
        name = str(op[0]).upper()
        name = ALIASES.get(name, name)
        if name == "HINSERT":
            self.hinsert(int(op[1]))
        elif name == "TINSERT":
            self.tinsert(int(op[1]))
        elif name == "DROP_T":
            self.drop_t()
        elif name == "POP_T":
            return self.pop_t()
        elif name == "FLIP":
            self.flip()
        elif name == "ADD":
            self.add()
        elif name == "MUL":
            self.mul()
        elif name == "SUB":
            self.sub()
        elif name == "DUP_T":
            self.dup_t()
        elif name == "SWAP_T":
            self.swap_t()
        elif name == "OVER_T":
            self.over_t()
        elif name == "POP_H":
            return self.pop_h()
        elif name == "DROP_H":
            self.drop_h()
        elif name == "DIV":
            self.div()
        elif name == "MOD":
            self.mod()
        elif name == "NEG":
            self.neg()
        elif name == "EQ":
            self.eq()
        elif name == "LT":
            self.lt()
        elif name == "LOAD":
            self.load(int(op[1]))
        elif name == "STORE":
            self.store(int(op[1]))
        elif name == "LOADI":
            self.loadi()
        elif name == "STOREI":
            self.storei()
        elif name == "IN":
            self.in_op()
        elif name == "OUT":
            self.out_op()
        elif name == "JMP":
            self._jump(int(op[1]))
        elif name == "JZ":
            if self._peek_tail() == 0:
                self._jump(int(op[1]))
            # else: fallthrough (caller increments pc)
        elif name == "JNZ":
            if self._peek_tail() != 0:
                self._jump(int(op[1]))
        elif name == "CALL":
            self._call(int(op[1]))
        elif name == "RET":
            self._ret()
        elif name == "HALT":
            self.halted = True
        else:
            raise ValueError(f"unknown op: {op[0]!r}")
        return None

    # -- PC helpers (bounds need program length; set by run loop) --
    _prog_len: int = 0  # set by run(); step() validates jumps against it

    def _check_target(self, target: int) -> int:
        # _prog_len==0 means standalone step() use: allow any non-negative
        # target (run loop revalidates). Negative is always invalid.
        if target < 0 or (self._prog_len and not (0 <= target < self._prog_len)):
            raise MoramTrap("INVALID_JUMP", f"target {target}")
        return target

    def _jump(self, target: int) -> None:
        self.pc = self._check_target(int(target))
        self._jumped = True

    def _call(self, target: int) -> None:
        target = self._check_target(int(target))  # validate BEFORE push (atomic)
        self.rstack.append(self.pc + 1)
        self.pc = target
        self._jumped = True

    def _ret(self) -> None:
        if not self.rstack:
            raise MoramTrap("RSTACK_UNDERFLOW", "RET with empty return stack")
        self.pc = self.rstack.pop()
        self._jumped = True
        if self._prog_len and not (0 <= self.pc <= self._prog_len):
            # == len is benign (return past end = halt); anything else traps
            if self.pc != self._prog_len:
                raise MoramTrap("INVALID_JUMP", f"ret to {self.pc}")

    def run(self, program: Iterable[Sequence], trace: bool = False,
            max_steps: int = 100000):
        """Execute program with PC loop. Returns POP_T/POP_H values.

        If trace, returns (pops, hist) where hist holds (view, o, pc) after
        each step. Implicit halt (fall off end) is clean. max_steps guards
        infinite loops (RuntimeError).
        """
        prog = list(program)
        self._prog_len = len(prog)
        self.pc = 0
        self.halted = False
        # NOTE: D/MEM/IN/OUT persist (caller preloads); RSTACK starts empty.
        self.rstack.clear()
        pops: List[int] = []
        hist = []
        steps = 0
        n = len(prog)
        while 0 <= self.pc < n and not self.halted:
            if steps >= max_steps:
                raise RuntimeError(f"STEP_LIMIT exceeded ({max_steps})")
            op = prog[self.pc]
            r = self.step(op)
            if r is not None:
                pops.append(r)
            steps += 1
            if trace:
                hist.append((self.view(), self.o, self.pc))
            if self.halted:
                break
            if self._jumped:
                # jump/call/ret taken (even self-jump): step() validated already
                if not (0 <= self.pc <= n):
                    raise MoramTrap("INVALID_JUMP", f"target {self.pc}")
                if self.pc == n:
                    break  # jumped past end = clean halt
            else:
                # no jump taken: fallthrough
                self.pc += 1
        self._prog_len = 0
        return (pops, hist) if trace else pops

    def run_text(self, text: str, trace: bool = False, inputs: Optional[Iterable[int]] = None,
                 max_steps: int = 100000):
        if inputs is not None:
            self.feed(inputs)
        return self.run(parse_program(text), trace=trace, max_steps=max_steps)


def parse_program(text: str) -> List[Tuple]:
    """Parse assembly text into op tuples with jump targets resolved to indices.

    One statement per line: [label:] OP [operand]. Case-insensitive.
    Comments '#' or ';' to end of line. Blank lines skipped.
    Labels: `name:` defines name = index of next instruction; may share a
    line with an instruction (`loop: LOAD 0`). Jumps take a label or an
    absolute integer index. Aliases: ROT->FLIP, REMOVE->DROP_T.
    """
    raw_ops: List[Tuple] = []  # (name, operand_str|None, lineno)
    labels: Dict[str, int] = {}

    for lineno, raw in enumerate(text.splitlines(), 1):
        for marker in ("#", ";"):
            if marker in raw:
                raw = raw.split(marker, 1)[0]
        line = raw.strip()
        if not line:
            continue
        # label prefix?
        m = _LABEL_RE.match(line)
        if m:
            lname, rest = m.group(1), m.group(2).strip()
            if lname.upper() in CANONICAL_OPS or lname.upper() in ALIASES:
                raise ValueError(f"line {lineno}: label {lname!r} collides with opcode")
            if lname in labels:
                raise ValueError(f"line {lineno}: duplicate label {lname!r}")
            labels[lname] = len(raw_ops)
            line = rest
            if not line:
                continue
        parts = line.split()
        name = parts[0].upper()
        name = ALIASES.get(name, name)
        if name in ("HINSERT", "TINSERT", "LOAD", "STORE", "JMP", "JZ", "JNZ", "CALL"):
            if len(parts) != 2:
                raise ValueError(f"line {lineno}: {parts[0]} needs one operand")
            raw_ops.append((name, parts[1], lineno))
        elif name in CANONICAL_OPS:
            if len(parts) != 1:
                raise ValueError(f"line {lineno}: {name} takes no operand")
            raw_ops.append((name, None, lineno))
        else:
            raise ValueError(f"line {lineno}: unknown op {parts[0]!r}")

    prog: List[Tuple] = []
    for name, operand, lineno in raw_ops:
        if name in ("HINSERT", "TINSERT"):
            try:
                prog.append((name, int(operand, 10)))
            except ValueError:
                raise ValueError(f"line {lineno}: bad immediate {operand!r}")
        elif name in ("LOAD", "STORE"):
            try:
                prog.append((name, int(operand, 10)))
            except ValueError:
                raise ValueError(f"line {lineno}: bad address {operand!r}")
        elif name in ("JMP", "JZ", "JNZ", "CALL"):
            assert operand is not None
            if _INT_RE.match(operand):
                prog.append((name, int(operand, 10)))
            else:
                if operand not in labels:
                    raise ValueError(f"line {lineno}: unknown label {operand!r}")
                prog.append((name, labels[operand]))
        else:
            prog.append((name,))
    return prog
