# MORAM ISA — SPEC v0.2 (Deque Machine + Control/Memory)

Status: frozen draft v0.2. Supersedes v0.1. Core unchanged and backward compatible;
adds data-movement, arithmetic, control, memory, I/O.

Machine name: MORAM. Model: deque with orientation flag + PC + flat memory.

## 1. Machine state

State = `(D, o, PC, MEM, RSTACK, IN, OUT, HALTED)` where:

- `D` is a deque of signed arbitrary-precision integers (bigint), unbounded.
- `o ∈ {0, 1}` orientation bit. `o=0`: head=physical-left, tail=physical-right.
  `o=1`: head=physical-right, tail=physical-left. `H`/`T` are labels.
- `PC` is the program counter: index of next instruction in the loaded program.
- `MEM` is flat word memory: `dict addr -> bigint`, `addr` a non-negative int.
  Reads of never-written addresses yield `0`. Unbounded (auto-extending).
- `RSTACK` is a dedicated return-address stack (list of PCs). Separate from `D`
  and `MEM` so calls cannot smash deque data and vice versa.
- `IN` is an input word queue (list, FIFO). `OUT` is the output word list.
- `HALTED` is a boolean. `HALT` sets it; the machine stops fetching.

Notation: `D = [h ... t]` is the current label view (head left, tail right).
Initial state: `D=[]`, `o=0`, `PC=0`, `MEM={}`, `RSTACK=[]`, `IN=[]` (or host
supplied), `OUT=[]`, `HALTED=False`.

## 2. Core (v0.1, unchanged)

| Op | Syntax | Transition |
|---|---|---|
| `HINSERT` | `HINSERT <imm>` | push `imm` to head. `[h..t]` → `[imm,h..t]` |
| `TINSERT` | `TINSERT <imm>` | push `imm` to tail. `[h..t]` → `[h..t,imm]` |
| `DROP_T` | `DROP_T` | discard tail. Trap `EMPTY_POP` if empty |
| `POP_T` | `POP_T` | `v`=tail to host, shrink by 1. Trap `EMPTY_POP` if empty |
| `FLIP` | `FLIP` | `o ^= 1`, storage untouched, O(1). Double-flip = identity |
| `ADD` | `ADD` | `a=pop_T, b=pop_T`, push `b+a` to head. `UNDERFLOW` if len<2 |
| `MUL` | `MUL` | push `b*a` to head. Same trap/arity as `ADD` |
| `SUB` | `SUB` | push `b-a` (next-to-tail minus top) to head. Same trap/arity |

Aliases: `ROT`→`FLIP`, `REMOVE`→`DROP_T`. All traps are atomic: on trap no
pops/pushes happen, `D`/`o` unchanged, machine stops (fail-stop) unless the
embedding handles the trap.

`SUB`×`FLIP` warning (carried from v0.1): both operands come from the current
tail, so `FLIP` negates `SUB` (`[10,3]` gives `7` at `o=0`, `-7` at `o=1`).
Do not hoist `FLIP` across `SUB`.

## 3. Data movement (new)

Motivation: v0.1 cannot duplicate a computed value in-deque (`[x]`→`[x,x]`
impossible without host temp). `DUP_T` closes this; the rest give
Forth-familiar reorder plus symmetric head pops for ferrying ALU results
(head) back to the operand side (tail).

| Op | Syntax | Transition (label view) | Trap if |
|---|---|---|---|
| `DUP_T` | `DUP_T` | `[h..x_t]` → `[h..x,x]` (peek tail, copy to tail) | `EMPTY_POP` if empty |
| `SWAP_T` | `SWAP_T` | `[h..y,x_t]` → `[h..x,y]` | `UNDERFLOW` if len<2 |
| `OVER_T` | `OVER_T` | `[..,y,..,x_t]` → `[..,y,..,x,y]` (copy 2nd-to-tail to tail) | `UNDERFLOW` if len<2 |
| `POP_H` | `POP_H` | `v`=head to host, shrink by 1 | `EMPTY_POP` if empty |
| `DROP_H` | `DROP_H` | discard head | `EMPTY_POP` if empty |

All atomic on trap. `DUP_T`/`SWAP_T`/`OVER_T` operate on the current tail,
so `FLIP` retargets them exactly like `ADD` operands.

Head-pop emulation `FLIP;POP_T;FLIP` still works but `POP_H`/`DROP_H` are
canonical now. Ferry pattern (move ALU result head→tail side when `len>1`):
`POP_H v; TINSERT v` (host temp) or `FLIP`-discipline (flip before next
consume — but see §6 hazard notes).

## 4. Arithmetic extension (new)

| Op | Syntax | Transition |
|---|---|---|
| `DIV` | `DIV` | `a=pop_T, b=pop_T`, push `trunc(b/a)` to head |
| `MOD` | `MOD` | push `b - trunc(b/a)*a` to head (C-style remainder) |
| `NEG` | `NEG` | `a=pop_T`, push `-a` to head |
| `EQ` | `EQ` | `a=pop_T, b=pop_T`, push `1` if `b==a` else `0` to head |
| `LT` | `LT` | push `1` if `b<a` else `0` to head |

- `DIV`/`MOD` need `len>=2` else `UNDERFLOW` (atomic).
- Divisor is `a` (tail-top). If `a==0` → `DIV_ZERO` trap, atomic (no pops).
- Division truncates toward zero (C-like), remainder takes the dividend's sign:
  `7/2=3`, `-7/2=-3`, `7/-2=-3`, `-7/-2=3`;
  `7%2=1`, `-7%2=-1`, `7%-2=1`, `-7%-2=-1`.
  Invariant: `b == a*q + r`.
- `NEG` needs `len>=1` else `UNDERFLOW` (atomic).
- `EQ`/`LT` need `len>=2` else `UNDERFLOW`. Results are `0`/`1` bigints pushed
  to head, ready for `JZ`/`JNZ` or `MUL`-masking.

## 5. Control (new, PC + labels)

Assembly has labels: `name:` on its own line (or prefixing an instruction)
defines `name` = index of the next instruction. Jump operands are labels
(resolved at load) or absolute integer indices (for generated code).
Out-of-range target → `INVALID_JUMP` trap at execution.

| Op | Syntax | Transition |
|---|---|---|
| `JMP` | `JMP <L>` | `PC = L` |
| `JZ` | `JZ <L>` | `v = peek_T` (no pop); if `v==0` then `PC=L` else `PC+=1` |
| `JNZ` | `JNZ <L>` | `v = peek_T`; if `v!=0` then `PC=L` else `PC+=1` |
| `CALL` | `CALL <L>` | push `PC+1` to `RSTACK`, `PC=L` |
| `RET` | `RET` | `PC = RSTACK.pop()` |
| `HALT` | `HALT` | `HALTED=True`, stop fetching (clean stop, not a trap) |

- `JZ`/`JNZ` are **non-destructive peek** (frozen decision): they do not pop.
  `EMPTY_POP` if `D` empty. Typical discipline: `LOAD c; JZ done; DROP_T; ...`
  (explicit drop on the fallthrough) and `DROP_T` of the `0` on the taken path.
- `CALL` traps `INVALID_JUMP` on bad target (no push in that case). `RET`
  traps `RSTACK_UNDERFLOW` if `RSTACK` empty.
- Programs without control ops execute linearly `PC=0..n-1` then halt
  (implicit halt at end). All v0.1 programs behave identically under v0.2.

## 6. Memory — flat direct (new)

| Op | Syntax | Transition | Trap if |
|---|---|---|---|
| `LOAD` | `LOAD <a>` | push `MEM[a]` (or `0` if unset) to **tail** | `INVALID_ADDR` if `a<0` |
| `STORE` | `STORE <a>` | `v=pop_T` into `MEM[a]` | `INVALID_ADDR` if `a<0`; `EMPTY_POP` if empty |

- `LOAD` targets the tail (operand side) so `LOAD x; LOAD y; ADD` just works.
- `STORE` consumes the tail-top. Atomic: `INVALID_ADDR` checked first, then
  `EMPTY_POP`; neither mutates on trap.
- Indirect (`LOADI`/`STOREI` with address taken from `D`) is deferred to v0.3.

## 7. I/O (new)

| Op | Syntax | Transition | Trap if |
|---|---|---|---|
| `IN` | `IN` | shift next input word → push to tail | `IO_EMPTY` if no input left |
| `OUT` | `OUT` | `v=pop_T` → append to `OUT` list | `EMPTY_POP` if empty |

`OUT` consumes (like `STORE`). No non-destructive `OUT` in v0.2 (use `DUP_T`
before `OUT` to keep a copy).

## 8. Text assembly + opcode reservation

- One statement per line: `[label:] OP [operand]`. Case-insensitive.
  Comments `#` or `;` to end of line. Blank lines ignored.
- `HINSERT`/`TINSERT`: signed decimal immediates. `LOAD`/`STORE`: decimal addr.
  `JMP`/`JZ`/`JNZ`/`CALL`: label or absolute index.
- Reserved opcode numbers (for future binary codec; text is canonical in v0.2):
  `0x00 HALT, 0x01 HINSERT, 0x02 TINSERT, 0x03 DROP_T, 0x04 POP_T, 0x05 FLIP,
  0x06 ADD, 0x07 MUL, 0x08 SUB, 0x09 DUP_T, 0x0A SWAP_T, 0x0B OVER_T,
  0x0C POP_H, 0x0D DROP_H, 0x0E DIV, 0x0F MOD, 0x10 NEG, 0x11 EQ, 0x12 LT,
  0x13 JMP, 0x14 JZ, 0x15 JNZ, 0x16 CALL, 0x17 RET,
  0x18 LOAD, 0x19 STORE, 0x1A IN, 0x1B OUT`.
  Aliases (`ROT`, `REMOVE`) are assembly-only, no distinct opcodes.

## 9. Calling convention + orientation discipline (normative guidance)

- Operands live at the **tail**, ALU/compare results arrive at the **head**.
  For single-element results head==tail placement is moot; for longer deques
  ferry explicitly (`POP_H`→`TINSERT`) or adopt a `FLIP`-per-step discipline.
- `FLIP` retargets `SUB`/`DIV`/`MOD`/`EQ`/`LT` operand *selection* and
  `JZ`/`JNZ` tested element. Do not sink/hoist `FLIP` across them.
- Function convention: caller pushes args to tail, `CALL f`; callee consumes
  args from tail, pushes result (single word → placement moot), `RET`; caller
  finds result at tail/head (single word: same) or ferries if needed. Return
  addresses never touch `D` (they live on `RSTACK`).
- Branch discipline (non-destructive): every `JZ`/`JNZ` peek must be followed
  by an explicit `DROP_T` on *both* paths (fallthrough drops the tested copy,
  target drops the `0`/nonzero copy) unless the value is intentionally kept.

Worked countdown (outputs 3,2,1):
```
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
```

Worked factorial(5)=120 (MEM[0]=counter, MEM[1]=acc):
```
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
```

## 10. Traps (complete list)

`EMPTY_POP`, `UNDERFLOW`, `DIV_ZERO`, `INVALID_ADDR`, `INVALID_JUMP`,
`RSTACK_UNDERFLOW`, `IO_EMPTY`. All fail-stop and atomic (no partial pops,
pushes, MEM writes, PC or RSTACK mutation on trap). `HALT` and implicit
end-of-program are clean stops, not traps. No overflow (bigint).

## 11. Completeness note

`DUP_T + JZ/JNZ + LOAD/STORE + ADD/SUB` gives a 2-counter machine simulation
(MEM cells = counters, deque = scratch, PC = control): sufficient for
Turing-completeness with unbounded MEM/program. `CALL/RET` + `RSTACK` add
structured reuse on top; not load-bearing for the proof.

## 12. Conformance checklist v0.2 (maps to test_moram_v2.py)

- [ ] `DUP_T` copies tail-top; `EMPTY_POP` when empty.
- [ ] `SWAP_T`/`OVER_T` reorder + `UNDERFLOW` on <2.
- [ ] `POP_H`/`DROP_H` symmetric; ferry `POP_H;TINSERT` moves head→tail.
- [ ] `DIV`/`MOD` trunc-toward-zero vectors + `DIV_ZERO` atomic (D unchanged).
- [ ] `NEG`/`EQ`/`LT` incl. `UNDERFLOW` cases.
- [ ] `LOAD` unset→`0` roundtrip; `INVALID_ADDR` on negative.
- [ ] `JZ`/`JNZ` non-destructive (D unchanged by the test itself).
- [ ] `JMP` loop, countdown outputs `[3,2,1]`, factorial outputs `[120]`.
- [ ] `CALL`/`RET` single + nested; `RSTACK_UNDERFLOW`; `INVALID_JUMP`.
- [ ] `IN`/`OUT` sequence; `IO_EMPTY`.
- [ ] `HALT` stops (tail after `HALT` never runs); implicit halt at end.
- [ ] v0.1 regression: all `test_moram.py` still pass unchanged.

## 13. Versioning

- v0.1: core 8 ops (with aliases), straight-line only.
- v0.2: + `DUP_T/SWAP_T/OVER_T/POP_H/DROP_H/DIV/MOD/NEG/EQ/LT/
  JMP/JZ/JNZ/CALL/RET/LOAD/STORE/IN/OUT/HALT`, labels, traps, convention.
- v0.3 (this section, draft): + indirect `LOADI`/`STOREI` for
  computed addresses (call frames, heap access). Still deferred:
  binary codec, `PEEK_H/T`-to-host, structured macros.

## 14. v0.3 (draft) — indirect memory

Motivation: reentrant procedures need address-computed frame slots, and
heap objects need pointer chasing. Direct `LOAD`/`STORE` take assembly
immediates only, so v0.3 adds deque-addressed variants.

| Op | Syntax | Transition | Trap if |
|---|---|---|---|
| `LOADI` | `LOADI` | `a=pop_T`; push `MEM[a]` (or `0` if unset) to tail | `EMPTY_POP` if empty; `INVALID_ADDR` if `a<0` |
| `STOREI` | `STOREI` | `v=pop_T`; `a=pop_T`; `MEM[a]=v` | `EMPTY_POP` if fewer than 2 elements; `INVALID_ADDR` if `a<0` (both already consumed) |

- Stack order: value on **top**. `STOREI` consumes `... a v` (tail-top
  is the value). Rationale: ALU results land at the head, so an address
  computed last sits *below* a value pushed before it — value-on-top is
  the only order reachable with single-temporary patterns. `LOADI`
  consumes `... a`, produces `... MEM[a]`.
- Atomicity: `EMPTY_POP` is checked first (nothing popped on trap).
  For `STOREI`, the address pop happens before validation, so on
  `INVALID_ADDR` the address is consumed but the value is preserved.
- Opcode numbers: `0x1C LOADI`, `0x1D STOREI`. Text assembly is
  unchanged otherwise (no operands).
- Frame discipline (guidance): keep `FP`/`SP` in low `MEM` cells;
  slot address = `FP + offset` computed with plain integer ops
  (`LOAD fp; TINSERT k; ADD`), then `LOADI`/`STOREI`. Addresses are raw
  integers, never tagged values.
