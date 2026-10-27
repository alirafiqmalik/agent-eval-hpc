# Verus Gym Task 976__B

You are solving a Verus specification task.

## Workspace

- Edit: `/home/dev/solve.rs`
- Problem statement: `/home/problem_artifacts/problem_statement.md`
- Visible sample tests and snippets: `/home/symbolic_tests`
- Task-specific artifacts: `/home/task_specific_artifacts`
- Examples: `/home/examples`
- Verus docs: `/home/verus_documentation`
- Readable evaluator source: `/home/evaluator_scripts/specgen_evaluator`
- Attempts/logs (agent): `/home/attempts` and `/logs/agent/compressed_verdicts`

## Useful command during generation (sample evaluation)

```bash
verus_gym_specgen_check
```

The default CLI arguments evaluate your submitted `/home/dev/solve.rs` on the
visible sample tests and write feedback under `/home/attempts`.

## Task instructions

## Verus Specification Task
You are working on a benchmark built from real competitive-programming problems
and test data. For each task, you receive a natural-language problem statement
and write formal Verus specifications that capture the valid inputs and correct
outputs for that problem.

The environment has already done the problem-specific setup for you. The
concrete input/output structure will be visible in `/home/dev/solve.rs` and in
the generated testcase snippets, but you do not need to implement that
conversion yourself. In particular, the task bundle already includes:

- Chosen logical types (`In1`, `Out`, …) for the problem.
- Parsed real `.in` / `.out` files into these types.
- Pre-generated **Verus-ready definition snippets** (files like `out.input_defn`
  and, for postcondition testcases, `out.gt_output_defn`) for each symbolic
  testcase.

Your job is:

> Given the problem statement and pre-generated Verus snippets, write
> **logical specifications** `pre_spec` and `post_spec` so that they correctly
> classify real inputs/outputs, i.e. they accept valid inputs/outputs and reject invalid ones.

The proof helpers in `/home/dev/solve.rs` are only there to help Verus establish
the fixed testcase assertions against your specs. You are not proving an
algorithm or implementing a solver for the problem.

You do **not** change parsing, printing, or snippet generation. You only edit the
**specs and proofs** in `/home/dev/solve.rs`.

To check your current implementation against the visible sample tests, run:

```bash
verus_gym_specgen_check
```

In this environment, the default CLI arguments evaluate the current
`/home/dev/solve.rs` and write feedback/artifacts under `/home/attempts`.

---

## 1. Files and environment overview

In this environment you will primarily work with `/home/dev/solve.rs` and the
problem artifacts under `/home/problem_artifacts`.

The current problem statement is at
`/home/problem_artifacts/problem_statement.md`. The visible sample testcases are
under `/home/symbolic_tests/<bucket>/<testcase_id>/`, where `<bucket>` is one of
`pre_sound`, `pre_complete`, `post_sound`, or `post_complete`. Some bucket
directories may be empty in the visible sample set. Non-empty testcase
directories contain:

- `pre_*` testcases: `test.in` and `out.input_defn`.
- `post_*` testcases: `test.in`, `test.out`, `out.input_defn`, and
  `out.gt_output_defn`.

The logical input type is `In1`, and the logical output type is `Out`. Their
executable counterparts are `ExecIn1` and `ExecOut`. You can inspect all of
these definitions in `/home/dev/solve.rs`.

Below are more details:

- `/home/dev/solve.rs`
  - This is the Verus file you edit for this benchmark.
  - It already contains:
    - Logical/spec types (`In1`, `Out`, …) defined via `exec_spec_unverified! { ... }`.
      - This also generates executable counterparts (`ExecIn1`, `ExecOut`, …) and
        runtime wrappers `exec_pre_spec` / `exec_post_spec` for exec fallback.
    - Stub specs:
      - `pub open spec fn pre_spec(in1: In1) -> bool { ... }`
        - Should return `true` exactly for inputs satisfying the problem
          statement's input constraints.
      - `pub open spec fn post_spec(in1: In1, out: Out) -> bool { ... }`
        - Should return `true` exactly for outputs that are correct for the
          given input according to the problem statement.
    - Proof helpers:
      - `proof fn pre_spec_soundness_proof(in1: In1) { ... }`
      - `proof fn pre_spec_completeness_proof(in1: In1) { ... }`
      - `proof fn post_spec_soundness_proof(in1: In1, out: Out) { ... }`
      - `proof fn post_spec_completeness_proof(in1: In1, out: Out) { ... }`
    - Four **check wrappers**:
      - `fn check_pre_spec_completeness() { ... }`
      - `fn check_pre_spec_soundness() { ... }`
      - `fn check_post_spec_completeness() { ... }`
      - `fn check_post_spec_soundness() { ... }`
        Each of these contains **paste markers** such as:
        - `// __PASTE_out.input_defn__`
        - `// __PASTE_out.gt_output_defn__`
        and calls to the corresponding proof helpers.
        The **assertion lines are fixed** by the provided testcase snippets and are not
        model-editable.
    - Runtime exec fallback entrypoints (fixed):
      - `pub fn main_exec_pre_spec_check() { ... }`
      - `pub fn main_exec_post_spec_check() { ... }`
      - `fn main() { unimplemented!(...) }` (placeholder; evaluator replaces it)

- `/home/examples/solve.rs`
  - A **worked code example** of a Verus specification file for another problem.
  - Use it as a reference for Verus structure, proof-helper style, and common
    spec patterns. The example specs may be incomplete or imperfect; for now,
    use this file mainly to understand the expected spec/proof format.
- `/home/examples/problem_statement_for_example.md`
  - The natural-language problem statement corresponding to
    `/home/examples/solve.rs`.
  - Use the pair as an example of how a statement can map to Verus spec format.
    Do not treat it as semantic guidance for your current task.

- `/home/problem_artifacts/problem_statement.md`
  - The full **natural-language problem statement** for the programming problem you are working on.
  - You must base both `pre_spec` and `post_spec` on this statement.

- `/home/symbolic_tests/`
  - Contains **sample symbolic test buckets**. Some buckets may be empty in the
    visible sample set.
    - `pre_sound/` — inputs that must violate the precondition (invalid inputs).
    - `pre_complete/` — inputs that must satisfy the precondition (valid inputs).
    - `post_sound/` — (input, output) pairs where the output is **incorrect**.
    - `post_complete/` — (input, output) pairs where the output is **correct**.
  - For each testcase id `T`, you will see:
    - `pre_*` testcases: `test.in` for precondition examples, plus
      `out.input_defn` to see the Verus encoding of input.
      Do not expect `test.out` in `pre_*` directories.
    - `post_*` testcases: `test.in`, `test.out`, `out.input_defn`,
      and `out.gt_output_defn`.
      These are **pre-generated Verus snippets** that encode the same data as
      the testcase files, but in Verus syntax (e.g., `let exec_in1: ExecIn1 = ...;`).
  - **Important:** these are only the **visible** sample tests. Local/debug
    feedback may be based on this visible subset, while final scoring uses the
    full evaluator suite, including hidden tests derived from the same fixed
    conversion setup.

- `/home/evaluator_scripts/specgen_evaluator/`
  - Readable Python evaluator source copied into the container for reference.
  - The active evaluation flow is in
    `/home/evaluator_scripts/specgen_evaluator/runner/evaluate_specgen.py`,
    with helpers under `runner/`, `phases/`, and `models/`.
  - The command you run is still `verus_gym_specgen_check`; it is installed
    from the trusted runtime bundle under `/opt/verus_gym_specgen_check_runtime`.
  - Editing files under `/home/evaluator_scripts` does not affect scoring.

- `/home/verus_documentation`
  - Verus guide and API docs. The file
    `/home/verus_documentation/guide/src/SUMMARY.md`
    lists all chapters and paths.
  - For `exec_spec` / `exec_spec_unverified`, you may find it helpful to read:
    `/home/verus_documentation/guide/src/exec_spec.md`
- `/home/verus-x86-linux`
  - The code for Verus.
  - You may also find it helpful to inspect the `exec_spec` implementation in the Verus source tree:
    - `/home/verus-x86-linux/vstd/contrib/exec_spec/`
    - `/home/verus-x86-linux/builtin_macros/src/contrib/exec_spec.rs`

---

## 2. What is fixed vs what you may edit

In `/home/dev/solve.rs`, **you may edit**:

- The bodies of:
  - `spec fn pre_spec(...) -> bool`
  - `spec fn post_spec(...) -> bool`
- The bodies of the four proof helpers:
  - `proof fn pre_spec_soundness_proof(...)`
  - `proof fn pre_spec_completeness_proof(...)`
  - `proof fn post_spec_soundness_proof(...)`
  - `proof fn post_spec_completeness_proof(...)`
- Any **additional spec or proof helpers** you introduce (e.g., helper predicates or lemmas),
  as long as they stay within the `verus! { ... }` block and do not break existing signatures.

You **must not**:

- Change the signatures or names of:
  - `pre_spec`, `post_spec`, the four `*_proof` functions, or any `check_*` wrapper.
- Edit the bodies or structure of the four `check_*` functions:
  - Do **not** remove or move:
    - The `// __PASTE_out.*__` marker comments.
    - The `proof { ... }` blocks that call your `*_proof` functions.
    - The `assert(...)` lines; they have been wired from the testcase snippets.
- Edit the runtime exec fallback functions or the placeholder `main()`:
  - `main_exec_pre_spec_check`, `main_exec_post_spec_check`, or `main`.
- Change type or struct definitions such as `In1`, `Out`, or any additional input types.
  - These are synchronized with the fixed conversion layer (`model_mod.rs`, `main.rs`),
    which you **cannot** see or edit in this task.

If you break these constraints (e.g., by deleting `check_post_spec_soundness` or
removing `// __PASTE_out.input_defn__`), the evaluator will reject your submission
with a **syntax / shape error** before running any proofs.

## 3. What `pre_spec` and `post_spec` should mean

Conceptually, we think in terms of sets:

- Let `I` be all possible inputs (each encoded as a `.in` file).
- Let `I_correct ⊆ I` be those inputs that satisfy **all** constraints in the problem statement.
- Let `I_wrong = I \ I_correct` be structurally well-formed but logically invalid inputs.

For each valid input `i ∈ I_correct`:

- Let `O_i` be all possible outputs (`.out` files).
- Let `O_i_correct ⊆ O_i` be outputs that satisfy the problem’s requirements on `i`.
- Let `O_i_wrong = O_i \ O_i_correct` be incorrect outputs for that input.

Your specs should capture:

- `pre_spec(in1, ...)` is **true** iff the logical input corresponds to a valid `.in` file:
  - Completeness: `pre_spec(i)` holds for every `i ∈ I_correct`.
  - Soundness:    `pre_spec(i)` does **not** hold for any `i ∈ I_wrong`.

- `post_spec(in1, ..., out)` is **true** iff `out` is a correct solution for `in1`:
  - Completeness: for all `i ∈ I_correct` and all `o ∈ O_i_correct`,
    `post_spec(i, o)` holds.
  - Soundness:    for all `i ∈ I_correct` and all `o ∈ O_i_wrong`,
    `post_spec(i, o)` does **not** hold.

In practice:

- `pre_spec` should encode **all structural and range constraints** on the input
  (e.g., bounds on `n`, array lengths, value ranges) that appear in the problem statement.
- `post_spec` should encode the **mathematical correctness** of the output:
  - Not just format (e.g., “`out.len() == n`”), but the intended semantics
    (e.g., “this is a valid matching with minimal cost”, “this sequence satisfies the constraints”).

Weak specs such as “`post_spec` always returns true” or specs that only check trivial
format properties will almost certainly fail the soundness/completeness tests.

---

## 4. How `.in` / `.out` become symbolic testcases

You **do not** implement the conversion, but it helps to understand
what is happening under the hood.

For each testcase, we have already extracted:

- **Input snippets**:

  ```rust
  // out.input_defn
  let exec_in1: ExecIn1 = ...;

  // out.assert_pre_spec
  assert(pre_spec(exec_in1.deep_view()));
  ```

- **Output snippets** (for post categories):

  ```rust
  // out.gt_output_defn
  let exec_out: ExecOut = ...;

  // out.assert_post_spec_gt
  assert(post_spec(exec_in1.deep_view(), exec_out.deep_view()));
  ```
These snippets are generated from real `.in` / `.out` files by the fixed
conversion layer. For an input testcase, `out.input_defn` constructs an
`ExecIn1` value corresponding to `test.in`; calling `exec_in1.deep_view()`
produces the logical `In1` value passed to `pre_spec` and `post_spec`. For an
output testcase, `out.gt_output_defn` constructs an `ExecOut` value
corresponding to the candidate output in `test.out`; calling
`exec_out.deep_view()` produces the logical `Out` value passed to `post_spec`.

The evaluator plugs these snippets into your `solve.rs`
inside the `check_*` wrappers.

---

## 4.1 exec_spec_unverified runtime fallback (SMT → exec)

The evaluator primarily tries to establish correctness **symbolically** via SMT.
For each bucket, it checks the expected polarity:

- Completeness buckets should prove `pre_spec(...)` or `post_spec(...)`.
- Soundness buckets should prove `!pre_spec(...)` or `!post_spec(...)`.

When SMT cannot decide the expected assertion, the evaluator may fall back to
**runtime execution** using the exec versions generated by
`exec_spec_unverified!`:
- `exec_pre_spec(&ExecIn1) -> bool`
- `exec_post_spec(&ExecIn1, &ExecOut) -> bool`

Practical implication: write specs that are both SMT-friendly *and* exec-friendly.
Also note: inside `exec_spec_unverified!`, quantifier variables should use concrete Rust
types (e.g., `i64`, `usize`), not `int`/`nat`.

---

## 5. How the evaluator uses your code

The evaluator runs the following steps for each testcase:

1. **Starting point**:

   - It uses the `/home/dev/solve.rs` file you edited, with the provided fixed
     types and printers.
   - It also uses four **derived files** that extract only:
     - Your `pre_spec` / `post_spec`,
     - Your four `*_proof` helpers,
     - The relevant `check_*` wrapper,
     - And any helper functions you introduced.

2. **Snippet injection**:

   - For each testcase and category, it reads:
     - `out.input_defn` and possibly `out.gt_output_defn`.
   - It then substitutes these into the appropriate `check_*` function bodies:

     ```rust
     // __PASTE_out.input_defn__
     // __PASTE_out.gt_output_defn__
     ```

   - The assertions inside the `check_*` functions are already wired to use
     the correct sign:
     - For completeness tests:
       - `assert(pre_spec(exec_in1.deep_view()));`
       - `assert(post_spec(exec_in1.deep_view(), exec_out.deep_view()));`
     - For soundness tests:
       - `assert(!pre_spec(exec_in1.deep_view()));`
       - `assert(!post_spec(exec_in1.deep_view(), exec_out.deep_view()));`
     These were filled in automatically from the snippets.

3. **Verus execution**:

   - For each testcase, the evaluator runs Verus on the derived check wrapper
     for that testcase's bucket:
     - `pre_complete` uses `check_pre_spec_completeness`.
     - `pre_sound` uses `check_pre_spec_soundness`.
     - `post_complete` uses `check_post_spec_completeness`.
     - `post_sound` uses `check_post_spec_soundness`.
   - It records:
     - Whether verification of the corresponding `assert(...)` succeeded.
     - Any Verus errors, plus paths to the `.rs`, `.stdout`, and `.stderr` logs
       under `/home/attempts/<run_id>/snippets/...`.

4. **Pass/fail logic**:

   - For **pre-completeness** tests (`pre_complete`):
     - The inputs are known valid.
     - The test passes if Verus can prove `assert(pre_spec(...))`.
   - For **pre-soundness** tests (`pre_sound`):
     - The inputs are invalid.
     - The test passes if Verus can prove `assert(!pre_spec(...))`.
   - For **post-completeness** tests (`post_complete`):
     - `(in1, out)` pairs are known correct.
     - The test passes if Verus can prove `assert(post_spec(exec_in1.deep_view(), exec_out.deep_view()))`.
   - For **post-soundness** tests (`post_sound`):
     - `(in1, out)` are known to be logically incorrect outputs.
     - The test passes if Verus can prove `assert(!post_spec(exec_in1.deep_view(), exec_out.deep_view()))`.

5. **Visible vs hidden tests**:

   - Running `verus_gym_specgen_check` in this environment may give local/debug
     feedback on only the visible sample tests.
   - Final scoring uses the full evaluator suite, including hidden tests not
     present under `/home/symbolic_tests`.
   - Use visible failures as diagnostics, but write specs from the problem
     statement rather than tuning only to the visible sample.

---

## 6. What to put inside the proof blocks

Each `check_*` wrapper looks roughly like:

```rust
fn check_pre_spec_completeness() {
    // __PASTE_out.input_defn__

    proof {
        pre_spec_completeness_proof(exec_in1.deep_view());
    }

    assert(pre_spec(exec_in1.deep_view()));
}
```

The proof functions are called inside `proof { ... }` blocks. Their role is to
help Verus discharge the verification conditions created by the `assert(...)`
lines.

Guidelines:

- You **may**:
  - Introduce additional lemmas/proof helpers and call them from the four
    main proof functions.
  - Use Verus ghost/state features (e.g., `assert_by`, `reveal`, or helper specs)
    to structure your reasoning.
- You **do not** have to fully prove deep properties if they are not needed:
  - It is acceptable to leave a proof body mostly empty if Verus can already
    verify the assertions from the specs alone.
  - However, if you strengthen the specs or introduce more complex conditions,
    you may need to fill in more detailed proofs.

The proof helpers are generic over `in1` or `(in1, out)`, not tied to a single
testcase. Use them for general lemmas and proof steps that help Verus prove the
concrete assertions after testcase snippets are injected.

If verification fails, the logs in
`/home/attempts/<run_id>/snippets/<category>/<test_id>/`
will contain the `.rs`, `.stdout`, and `.stderr` files for each check; the
error messages will point to these paths.

---

## 7. Practical strategy

Here is a suggested workflow:

1. **Understand the problem:**
   - Read `/home/problem_artifacts/problem_statement.md` carefully.
   - Inputs are represented by a single logical input struct `In1`.
   - Outputs are represented by `Out`.
   - Check `/home/examples/solve.rs` for a worked example of a complete Verus specification file.

2. **Inspect the existing types and snippets:**
   - Look at `/home/dev/solve.rs` and confirm the definitions of `In1` and `Out`.
   - Look at a few `out.input_defn` / `out.gt_output_defn` files under `/home/symbolic_tests/*/*/`
     to see concrete examples of how `ExecIn1` / `ExecOut` are constructed.

3. **Sketch `pre_spec`:**
   - Encode all input constraints from the problem statement.

4. **Sketch `post_spec`:**
   - Express the output correctness condition from the problem statement.

5. **Add or refine helpers and proofs:**
   - Introduce helper spec functions to keep `pre_spec`/`post_spec` readable.
   - Fill in the four `*_proof` functions enough to help Verus verify the
     `check_*` assertions.

6. **Use the feedback loop:**
   - After running `verus_gym_specgen_check`, read
     `/home/attempts/<run_id>/natural_language_feedback.txt`.
   - For failing tests, inspect:
     - `/home/attempts/<run_id>/snippets/<category>/<test_id>/*.rs`
     - Corresponding `.stdout` / `.stderr` paths mentioned in the error messages.
   - Adjust specs and proofs to fix the misclassified cases or verification failures.

---

## 8. Summary of your task

- **You edit only**:
  - The bodies of `pre_spec`, `post_spec`.
  - The bodies of the four `*_proof` helpers.
  - Any additional spec/proof helpers you introduce.

- **You do not edit**:
  - Types `In1`, `Out`, etc. (already fixed by the conversion layer).
  - Any fixed parsing, printing, or conversion code.
  - `check_*` wrappers’ structure or paste markers.
  - Any conversion-related Rust files (`model_mod.rs`, `main.rs`).

- **Goal**:
  - Make `pre_spec` and `post_spec` match the problem’s true notion of
    valid inputs and correct outputs as closely as possible.
  - Ensure that, when evaluated on the symbolic tests derived from real
    `.in` / `.out` files, your specs are both **sound** and **complete**
    to the extent that Verus can prove.

NOTE:
- Verus syntax documentation, tips, and instructions can be found in `/home/verus_documentation`. `/home/verus_documentation/guide/src/SUMMARY.md` contains a list of all the chapters and their paths in the filesystem in the guide.
- Some examples of Verus Code in HumanEval problems can be found in `/home/verus_documentation/humaneval_verus_solved_examples`
- Some other examples can be found in: `/home/verus_documentation/some_more_examples`
- Readable evaluator source under `/home/evaluator_scripts/specgen_evaluator/`
  as a guide while you work. The runnable checker command is
  `verus_gym_specgen_check`.
- Feel free to search anywhere in /home/verus_documentation to find helpful information, documentation and take inspiration from the examples!

Now go ahead and start implementing in /home/dev/solve.rs!
Also, you have a limited budget, so please submit whenever ready but do not wait for too long.
