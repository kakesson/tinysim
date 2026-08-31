# Proving that contracts compose, in Lean

> **Status: design note.** No Lean code is in this repository yet; this says
> what would be built, what it would prove, and what it would not.

TinySim checks contracts by *monitoring runs*. Monitoring can falsify a
contract and can build confidence, but the compositional claim -- *if every
component keeps its contract, the system keeps the composed one* -- is not a
statement about any particular run. It is a theorem, and a proof assistant is
the right place for it.

---

## 1. What has to be proved

Write a contract as a pair of behaviour sets `C = (A, G)`, and use the
**saturated** guarantee

    G↑  =  A → G

which says a component owes nothing outside its assumption. That is the same
convention TinySim reports as the verdict *not tested*.

| | statement | what it buys |
| --- | --- | --- |
| **T1** composition soundness | `M₁ ⊨ C₁` and `M₂ ⊨ C₂` implies `M₁ ∥ M₂ ⊨ C₁ ⊗ C₂` | components may be checked separately |
| **T2** refinement is a precongruence | `C₁' ≼ C₁` and `C₂' ≼ C₂` implies `C₁' ⊗ C₂' ≼ C₁ ⊗ C₂` | a component may be replaced by any refinement without redoing the system proof |
| **T3** discharge | if `M₁ ∥ M₂` entails `A₂` then `G₂` holds in the system | exactly what "assumption discharged by the system" claims in the reports |
| **T4** circular assume-guarantee | `A₁` from `G₂` and `A₂` from `G₁` gives both -- **only** under a causality side condition | the rule everyone reaches for, and the one that is unsound without the side condition |

T1 to T3 are set algebra. T4 needs induction over time, and it is the one worth
mechanising, because the failure mode is invisible on paper.

## 2. Why the theory applies to acausal models at all

The algebra needs composition of components to be **intersection of behaviour
sets**. In TinySim it is exactly that: flattening conjoins the components'
equations, `connect` adds more, and the solution set of the system is the
intersection of the components' solution sets over the shared variables.

That is worth saying out loud in a course. A contract theory built for
input/output components transfers to acausal models without adjustment
*because* flattening is intersection.

## 3. The encoding

Plain Lean 4, no mathlib: behaviours are predicates rather than `Set`, so the
development compiles in seconds and depends on nothing.

```lean
/-- A component is the set of behaviours it admits; composition is intersection. -/
abbrev Behaviour (B : Type) := B → Prop

structure Contract (B : Type) where
  assume    : Behaviour B
  guarantee : Behaviour B

/-- The saturated guarantee: outside its assumption, a component promises nothing. -/
def owes (C : Contract B) (b : B) : Prop := C.assume b → C.guarantee b

def Implements (M : Behaviour B) (C : Contract B) : Prop := ∀ b, M b → owes C b

def Refines (C D : Contract B) : Prop :=
  (∀ b, D.assume b → C.assume b) ∧ (∀ b, owes C b → owes D b)

def compose (C D : Contract B) : Contract B where
  assume    := fun b => (C.assume b ∧ D.assume b) ∨ ¬(owes C b ∧ owes D b)
  guarantee := fun b => owes C b ∧ owes D b
```

**T1** is then three lines, and the shape of the proof is the lesson: the
composed guarantee is saturated, so the environment assumption is never used.

```lean
theorem compose_sound {M N : Behaviour B} {C D : Contract B}
    (hM : Implements M C) (hN : Implements N D) :
    Implements (fun b => M b ∧ N b) (compose C D) := by
  intro b ⟨hMb, hNb⟩ _
  exact ⟨hM b hMb, hN b hNb⟩
```

**T3**, which is what the per-instance reports lean on:

```lean
theorem discharge {M N : Behaviour B} {C D : Contract B}
    (hN : Implements N D) (hEnv : ∀ b, M b → N b → D.assume b) :
    ∀ b, M b → N b → D.guarantee b :=
  fun b hm hn => hN b hn (hEnv b hm hn)
```

Read against a TinySim report, that is: *the system kept the inductor inside
its rated voltage, so the inductor owed its current bound.*

**T4** is where the work is. Instantiate `B := ℕ → State`, state the rule with
a causality side condition, and prove it by strong induction on time:

```lean
theorem circular
    (h₁ : ∀ σ n, (∀ k < n, A₂ σ k) → G₁ σ n)     -- strictly causal: uses the past only
    (h₂ : ∀ σ n, (∀ k ≤ n, G₁ σ k) → A₂ σ n)
    : ∀ σ n, G₁ σ n ∧ A₂ σ n := by
  intro σ n
  induction n using Nat.strong_induction_on with
  | _ n ih => ...
```

Weaken `h₁` from `k < n` to `k ≤ n` and the theorem becomes false. Lean will
simply refuse to close the induction, which is a better explanation of why
circular reasoning needs a delay than any paragraph.

## 4. Layers, and what each is worth

| layer | size | what it establishes |
| --- | --- | --- |
| 1. contract algebra | ~150 lines | T1-T3, plus conjunction and quotient. Justifies what the reports already claim. |
| 2. traces and circularity | ~120 lines | T4 and its counterexample. The part that is easy to get wrong. |
| 3. an STL layer | ~300 lines | the fragment TinySim uses, over sampled traces, with `ρ(φ, σ) > 0 → σ ⊨ φ` -- the monitor validated *semantically*, where SignalTemporalLogic.jl validates it *empirically* |
| 4. export from TinySim | ~200 lines | `tinysim export-lean FILE`: a model's contracts become Lean definitions, and the composition obligations for that system become theorems to prove. The tool says what must be proved; Lean makes you prove it. |

Layers 1 and 2 are the ones that pay immediately; 3 and 4 are what turn it into
coursework.

## 5. What a proof would and would not settle

Lean proves the **reasoning principle**: *if* the parts keep their contracts,
the whole keeps the composed contract. It cannot supply the premises. That
`M ⊨ C` for a real model is exactly what simulation only samples -- one run at
a time, at the output points, with the caveats in
[`contracts.md`](contracts.md).

So the two halves are complementary, and neither replaces the other:

* Lean: the step from component contracts to the system contract is valid.
* Monitoring: evidence, always incomplete, that a component keeps its contract.

Being explicit about that boundary is the honest version of "we verified the
system", and it is the thing worth teaching.

## 6. Prior art

* Kastenbaum, Boyer & Talpin, [*A Mechanically Verified Theory of Contracts*](https://arxiv.org/abs/2108.13647) -- the same algebra in Coq, generic over the underlying logic. The obvious thing to follow rather than repeat.
* Benveniste et al., [*Contracts for System Design*](https://www.semanticscholar.org/paper/Contracts-for-Systems-Design:-Theory-Benveniste-Caillaud/0ba4f16bed2262591d4233685c51229501c74715) -- the reference for the definitions used above.
* [Pacti](https://arxiv.org/pdf/2303.17751) -- the algebra implemented, unverified, as a tool.
