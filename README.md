# MORAM ISA

**A minimal, deque-based virtual machine with bigint arithmetic, flat memory, and structured control flow — built to power [MORRIS](https://github.com/Stanislaw3737/MORRIS-shell).**

MORAM (v0.2, frozen draft) is a small instruction-set architecture built around an unusual core abstraction: a **deque with an orientation flag**. Operands live at the tail, results arrive at the head, and a single `FLIP` instruction retargets every tail-sensitive operation in O(1) — no data movement required. On top of that sit a flat word memory, a dedicated return-address stack, and a full complement of arithmetic, control, and I/O ops.

The ISA is expressive enough to simulate a 2-counter machine (and therefore Turing-complete with unbounded memory), while remaining small enough to implement in an afternoon and reason about formally.

---

## Why MORAM?

MORAM is not a general-purpose ISA. It is the **execution substrate for [MORRIS](https://github.com/Stanislaw3737/MORRIS-shell)** — *Memory Organisation as a Reactive and Recursive Intent Driven System* — an intent-driven shell and data environment written in Rust. Morris parses declarative intents (`set`, `ensure`, `craft`, `forge`, …), maintains a reactive variable graph, and applies changes transactionally with propagation controls, history, and a persistent change engine.

MORAM exists to be the low-level machine that the reactive layer compiles to and evaluates on. Its design follows directly from what a reactive, transactional, intent-driven environment actually needs:

| MORRIS requirement | MORAM response |
|---|---|
| **Reactive propagation** — a change to one variable must flow to its dependents in dependency order. | The **deque** is a natural evaluation stack for graph walks; `LOAD`/`STORE` give addressable variable cells; `JZ`/`JNZ` non-destructive peeks let the propagation loop test and branch without disturbing the pending value. |
| **Transactional changes** (`craft`/`forge`/`smelt`/`temper`) — snapshot, preview, atomic apply, rollback on failure. | All traps are **atomic and fail-stop**: no partial pops, pushes, memory writes, or PC/RSTACK mutation on trap. A failed `forge` simply stops; the embedding restores the snapshot. Clean `HALT` vs. trap distinction makes commit/abort unambiguous. |
| **Explicit return discipline** — recursive intents, composed sub-intents, and rule/hook invocation must not clobber user data. | `RSTACK` is a **dedicated return-address stack**, separate from `D` and `MEM`. Calls cannot smash deque data and vice versa — exactly the isolation a recursive intent-composition system needs. |
| **Reaction delay and limits** (`~+n` / `~-n`) — count how many times a dependent has reacted. | Bigint counters in `MEM` with `ADD`/`SUB`/`LT`/`JZ` handle arbitrary reaction budgets; no overflow means long-lived sessions are safe. |
| **Inspected, replayed, serialized intents** — every step must be observable. | Small, explicit opcode set with reserved binary codec numbers; text is canonical; state is a plain tuple. Everything is inspectable and replayable. |
| **Structured values, expression evaluation, JSON-path-like queries** | ALU results land at the **head** while operands live at the **tail**, so short expression trees evaluate without shuffling; `DUP_T`/`SWAP_T`/`OVER_T` handle the reordering Forth-style. `FLIP` retargets operand selection in O(1). |
| **Safety levels and operation guards** (`SystemCritical`, `Experimental`, …) | Traps are a closed, complete set (`EMPTY_POP`, `UNDERFLOW`, `DIV_ZERO`, `INVALID_ADDR`, `INVALID_JUMP`, `RSTACK_UNDERFLOW`, `IO_EMPTY`) — the embedding can map each to a safety policy deterministically. |
| **Transactional previews without mutation** | `JZ`/`JNZ` are **non-destructive peeks** (frozen decision) — inspection never perturbs the value being inspected. |

In short: Morris needs a machine whose control flow is structured but cheap, whose arithmetic is unbounded and never overflows, whose calls are isolated from data, whose failure mode is atomic, and whose full state can be snapshot, inspected, and replayed. MORAM is that machine.

> **Roadmap connection.** MORAM v0.3 adds indirect memory (`LOADI`/`STOREI`) for **computed addresses** — the missing piece for reentrant procedure frames and pointer-chasing over heap-structured values, which Morris's `craft`/`forge` compositions and JSON-path queries will need. The frame discipline (§14: keep `FP`/`SP` in low `MEM`, addresses as raw integers) is designed to slot directly into Morris's existing variable-environment model.

---

## Table of contents

- [Why MORAM?](#why-moram)
- [Quick start](#quick-start)
- [Machine model](#machine-model)
- [Instruction set](#instruction-set)
  - [Core (v0.1)](#core-v01)
  - [Data movement](#data-movement)
  - [Arithmetic](#arithmetic)
  - [Control flow](#control-flow)
  - [Memory](#memory)
  - [I/O](#io)
  - [Indirect memory (v0.3 draft)](#indirect-memory-v03-draft)
- [Assembly syntax](#assembly-syntax)
- [Worked examples](#worked-examples)
- [Traps](#traps)
- [Conventions and pitfalls](#conventions-and-pitfalls)
- [Versioning](#versioning)
- [Conformance checklist](#conformance-checklist)

---

## Quick start

Write a program in text assembly:

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

Run it with input `[]` and read the output list — you get `[120]` (factorial of 5). See [`test_moram_v2.py`](#conformance-checklist) for the reference conformance suite.

> **Note:** v0.2 ships **text assembly only**. The binary codec is reserved (§8) but deferred.

---

## Machine model

The machine state is a tuple:

```
(D, o, PC, MEM, RSTACK, IN, OUT, HALTED)
```

| Component | Description |
|---|---|
| `D` | Deque of signed arbitrary-precision integers (bigint). Unbounded. |
| `o` | Orientation bit. `0` = head is physical-left; `1` = head is physical-right. |
| `PC` | Program counter — index of the next instruction. |
| `MEM` | Flat word memory: `addr → bigint`. Unset addresses read as `0`. Auto-extending. |
| `RSTACK` | Dedicated return-address stack, separate from `D` and `MEM`. |
| `IN` | FIFO input queue. |
| `OUT` | Output word list. |
| `HALTED` | Set by `HALT`; stops fetching. |

Initial state: empty deque, `o=0`, `PC=0`, empty memory and stacks, empty I/O, not halted.

**Label view.** `D = [h ... t]` means *head on the left, tail on the right* under the current orientation. `FLIP` swaps `o` but never touches storage — the same physical bytes are reinterpreted. A double flip is the identity.

**Two placements, one discipline.** Operands are consumed from the **tail**; ALU and comparison results are pushed to the **head**. For single-word results (the common case) head and tail coincide, so placement is moot. For longer deques, ferry explicitly with `POP_H; TINSERT` or adopt a flip-per-step discipline (see [Conventions](#conventions-and-pitfalls)).

---

## Instruction set

### Core (v0.1)

| Op | Syntax | Effect |
|---|---|---|
| `HINSERT` | `HINSERT <imm>` | Push immediate to head. |
| `TINSERT` | `TINSERT <imm>` | Push immediate to tail. |
| `DROP_T` | `DROP_T` | Discard tail. `EMPTY_POP` if empty. |
| `POP_T` | `POP_T` | Send tail value to host, shrink. `EMPTY_POP` if empty. |
| `FLIP` | `FLIP` | Toggle orientation. O(1), storage untouched. |
| `ADD` | `ADD` | Pop `a`, `b`; push `b+a` to head. `UNDERFLOW` if len<2. |
| `MUL` | `MUL` | Pop `a`, `b`; push `b*a` to head. |
| `SUB` | `SUB` | Pop `a`, `b`; push `b-a` to head. |

Aliases (assembly-only): `ROT` → `FLIP`, `REMOVE` → `DROP_T`.

> **`SUB` × `FLIP` warning.** Both operands come from the current tail, so `FLIP` negates `SUB`: `[10, 3]` yields `7` at `o=0` and `-7` at `o=1`. Do **not** hoist a `FLIP` across `SUB`.

### Data movement

| Op | Effect | Trap |
|---|---|---|
| `DUP_T` | Duplicate tail-top. `[h..x]` → `[h..x, x]`. | `EMPTY_POP` |
| `SWAP_T` | Swap top two. `[h..y, x]` → `[h..x, y]`. | `UNDERFLOW` if len<2 |
| `OVER_T` | Copy second-to-tail to tail. `[.., y, .., x]` → `[.., y, .., x, y]`. | `UNDERFLOW` if len<2 |
| `POP_H` | Send head to host, shrink. | `EMPTY_POP` |
| `DROP_H` | Discard head. | `EMPTY_POP` |

`DUP_T` closes the v0.1 gap where a computed value could not be duplicated in-deque. `POP_H`/`DROP_H` give symmetric head access for ferrying ALU results (which land at the head) back to the operand side.

> The head-pop emulation `FLIP; POP_T; FLIP` still works, but `POP_H`/`DROP_H` are canonical in v0.2.

### Arithmetic

| Op | Effect | Trap |
|---|---|---|
| `DIV` | Pop `a`, `b`; push `trunc(b/a)` to head. | `UNDERFLOW` if len<2; `DIV_ZERO` if `a==0` |
| `MOD` | Pop `a`, `b`; push `b - trunc(b/a)*a` (C-style remainder). | Same as `DIV` |
| `NEG` | Pop `a`; push `-a`. | `UNDERFLOW` if empty |
| `EQ` | Pop `a`, `b`; push `1` if `b==a` else `0`. | `UNDERFLOW` if len<2 |
| `LT` | Pop `a`, `b`; push `1` if `b<a` else `0`. | `UNDERFLOW` if len<2 |

Division truncates toward zero; remainder takes the dividend's sign. Invariant: `b == a*q + r`.

```
 7/ 2 =  3     -7/ 2 = -3      7/-2 = -3     -7/-2 =  3
 7% 2 =  1     -7% 2 = -1      7%-2 =  1     -7%-2 = -1
```

Boolean results are `0`/`1` bigints ready for `JZ`/`JNZ` or `MUL`-masking.

### Control flow

| Op | Syntax | Effect |
|---|---|---|
| `JMP` | `JMP <L>` | `PC = L` |
| `JZ` | `JZ <L>` | Peek tail (**no pop**); if `0`, `PC=L`, else `PC+=1`. |
| `JNZ` | `JNZ <L>` | Peek tail (**no pop**); if nonzero, `PC=L`, else `PC+=1`. |
| `CALL` | `CALL <L>` | Push `PC+1` to `RSTACK`; `PC=L`. |
| `RET` | `RET` | `PC = RSTACK.pop()`. |
| `HALT` | `HALT` | Set `HALTED`; stop fetching. Clean stop, not a trap. |

- `JZ`/`JNZ` are **non-destructive peeks** (frozen decision). They trap `EMPTY_POP` on an empty deque.
- Typical discipline: `LOAD c; JZ done; DROP_T; ...` — explicit drop on the fallthrough, and `DROP_T` of the tested `0` on the taken path.
- `CALL` traps `INVALID_JUMP` on a bad target without pushing. `RET` traps `RSTACK_UNDERFLOW` when empty.
- Programs without control ops execute linearly then halt implicitly at end-of-program. All v0.1 programs behave identically under v0.2.

Jump operands are labels (resolved at load) or absolute integer indices (for generated code). Out-of-range targets trap `INVALID_JUMP` at execution.

### Memory

| Op | Syntax | Effect | Trap |
|---|---|---|---|
| `LOAD` | `LOAD <a>` | Push `MEM[a]` (or `0` if unset) to **tail**. | `INVALID_ADDR` if `a<0` |
| `STORE` | `STORE <a>` | Pop tail into `MEM[a]`. | `INVALID_ADDR` if `a<0`; `EMPTY_POP` if empty |

`LOAD` targets the tail (the operand side) so `LOAD x; LOAD y; ADD` just works. `STORE` checks `INVALID_ADDR` first, then `EMPTY_POP`; neither mutates on trap.

### I/O

| Op | Effect | Trap |
|---|---|---|
| `IN` | Shift next input word to tail. | `IO_EMPTY` if no input left |
| `OUT` | Pop tail, append to `OUT`. | `EMPTY_POP` if empty |

`OUT` consumes. To keep a copy, `DUP_T` before `OUT`.

### Indirect memory (v0.3 draft)

| Op | Effect | Trap |
|---|---|---|
| `LOADI` | Pop address `a` from tail; push `MEM[a]` (or `0`) to tail. | `EMPTY_POP` if empty; `INVALID_ADDR` if `a<0` |
| `STOREI` | Pop value `v`, pop address `a`; `MEM[a] = v`. | `EMPTY_POP` if len<2; `INVALID_ADDR` if `a<0` |

**Stack order:** value on top. `STOREI` consumes `... a v` (tail-top is the value). Rationale: ALU results land at the head, so an address computed last sits *below* a value pushed before it — value-on-top is the only order reachable with single-temporary patterns.

**Atomicity:** `EMPTY_POP` is checked first (nothing popped on trap). For `STOREI`, the address pop happens before validation, so on `INVALID_ADDR` the address is consumed but the value is preserved.

**Frame discipline:** keep `FP`/`SP` in low `MEM` cells; slot address = `FP + offset` computed with plain integer ops (`LOAD fp; TINSERT k; ADD`), then `LOADI`/`STOREI`. Addresses are raw integers, never tagged values.

---

## Assembly syntax

- One statement per line: `[label:] OP [operand]`. Case-insensitive.
- Comments: `#` or `;` to end of line. Blank lines ignored.
- `HINSERT`/`TINSERT` take signed decimal immediates.
- `LOAD`/`STORE` take decimal addresses.
- `JMP`/`JZ`/`JNZ`/`CALL` take a label or absolute index.
- A label on its own line (or prefixing an instruction) binds to the index of the next instruction.

**Reserved opcode numbers** (for a future binary codec; text is canonical in v0.2):

```
0x00 HALT     0x01 HINSERT  0x02 TINSERT  0x03 DROP_T   0x04 POP_T
0x05 FLIP     0x06 ADD      0x07 MUL      0x08 SUB      0x09 DUP_T
0x0A SWAP_T   0x0B OVER_T   0x0C POP_H    0x0D DROP_H   0x0E DIV
0x0F MOD      0x10 NEG      0x11 EQ       0x12 LT       0x13 JMP
0x14 JZ       0x15 JNZ      0x16 CALL     0x17 RET      0x18 LOAD
0x19 STORE    0x1A IN       0x1B OUT      0x1C LOADI    0x1D STOREI
```

Aliases (`ROT`, `REMOVE`) are assembly-only and have no distinct opcodes.

---

## Worked examples

### Countdown — outputs `3, 2, 1`

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

### Factorial(5) = 120 — `MEM[0]=counter`, `MEM[1]=acc`

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

### Function call convention

Caller pushes args to the tail and `CALL f`. The callee consumes args from the tail, pushes a single-word result (placement moot), and `RET`s. The caller finds the result at the tail/head. Return addresses live on `RSTACK` and never touch `D`, so calls cannot smash deque data.

---

## Traps

All traps are **fail-stop and atomic**: on trap, no partial pops, pushes, `MEM` writes, `PC`, or `RSTACK` mutations occur. The machine halts unless the embedding handles the trap.

| Trap | Raised by |
|---|---|
| `EMPTY_POP` | Any tail/head op on an empty deque |
| `UNDERFLOW` | Two-operand ops with `len<2`; `NEG` with `len<1` |
| `DIV_ZERO` | `DIV`/`MOD` with zero divisor (no pops) |
| `INVALID_ADDR` | `LOAD`/`STORE`/`LOADI`/`STOREI` with negative address |
| `INVALID_JUMP` | Jump/call target out of range (no `RSTACK` push) |
| `RSTACK_UNDERFLOW` | `RET` with empty `RSTACK` |
| `IO_EMPTY` | `IN` with no input remaining |

`HALT` and implicit end-of-program are clean stops, **not** traps. There is no overflow — integers are arbitrary-precision.

---

## Conventions and pitfalls

1. **Operands at tail, results at head.** Single-word results are placement-neutral. For longer deques, ferry with `POP_H; TINSERT` or flip-per-step.

2. **`FLIP` retargets everything tail-sensitive.** It changes which element `SUB`/`DIV`/`MOD`/`EQ`/`LT` select as operands, and which element `JZ`/`JNZ` test. Never sink or hoist a `FLIP` across these.

3. **Non-destructive branches.** Every `JZ`/`JNZ` peek must be followed by an explicit `DROP_T` on *both* paths — fallthrough drops the tested copy, the target drops the `0`/nonzero copy — unless the value is intentionally kept.

4. **`SUB` and `FLIP` interact.** See the warning in [Core](#core-v01).

5. **Keep return addresses on `RSTACK`.** Never on `D` or `MEM`. This is the whole point of separating them.

6. **`OUT` consumes.** Use `DUP_T` before `OUT` if you need the value afterward.

7. **v0.3 `STOREI` is not fully atomic on `INVALID_ADDR`.** The address is consumed but the value is preserved — by design, because address validation happens after the address pop. Check your addresses.

---

## Versioning

| Version | Adds |
|---|---|
| **v0.1** | Core 8 ops (with aliases), straight-line execution. |
| **v0.2** | `DUP_T`, `SWAP_T`, `OVER_T`, `POP_H`, `DROP_H`, `DIV`, `MOD`, `NEG`, `EQ`, `LT`, `JMP`, `JZ`, `JNZ`, `CALL`, `RET`, `LOAD`, `STORE`, `IN`, `OUT`, `HALT`; labels; traps; conventions. |
| **v0.3** (draft) | `LOADI`, `STOREI` for computed addresses (call frames, heap access). |

Still deferred: binary codec, `PEEK_H`/`PEEK_T`-to-host, structured macros.

v0.2 is **backward compatible** with v0.1: every v0.1 program behaves identically.

---

## Conformance checklist

The reference suite `test_moram_v2.py` covers:

- [ ] `DUP_T` copies tail-top; `EMPTY_POP` when empty.
- [ ] `SWAP_T`/`OVER_T` reorder; `UNDERFLOW` on `len<2`.
- [ ] `POP_H`/`DROP_H` symmetric; ferry `POP_H; TINSERT` moves head→tail.
- [ ] `DIV`/`MOD` trunc-toward-zero vectors; `DIV_ZERO` atomic (deque unchanged).
- [ ] `NEG`/`EQ`/`LT` including `UNDERFLOW` cases.
- [ ] `LOAD` unset→`0` roundtrip; `INVALID_ADDR` on negative address.
- [ ] `JZ`/`JNZ` non-destructive (deque unchanged by the test itself).
- [ ] `JMP` loop; countdown outputs `[3,2,1]`; factorial outputs `[120]`.
- [ ] `CALL`/`RET` single and nested; `RSTACK_UNDERFLOW`; `INVALID_JUMP`.
- [ ] `IN`/`OUT` sequence; `IO_EMPTY`.
- [ ] `HALT` stops (tails after `HALT` never run); implicit halt at end.
- [ ] v0.1 regression: all `test_moram.py` still pass unchanged.

---

## Completeness

`DUP_T` + `JZ`/`JNZ` + `LOAD`/`STORE` + `ADD`/`SUB` is enough to simulate a 2-counter machine (MEM cells as counters, deque as scratch, PC as control) — sufficient for Turing-completeness with unbounded memory and program. `CALL`/`RET` and `RSTACK` add structured reuse on top but are not load-bearing for the proof.

---

*MORAM ISA — SPEC v0.2, frozen draft. Supersedes v0.1; core unchanged and backward compatible. Execution substrate for [MORRIS](https://github.com/Stanislaw3737/MORRIS-shell).*