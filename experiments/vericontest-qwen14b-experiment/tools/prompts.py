"""Reconstructed Colab CodeGen prompts, not exact author prompts."""

CODE_TASKS = ["nl2code", "spec2code", "nl_spec2code"]

EXAMPLE_NL = (
    "Check whether each element of the second integer vector is smaller "
    "than the corresponding element of the first. "
    "The vectors have equal length."
)

EXAMPLE_SPEC = """use vstd::prelude::*;
fn main() {}
verus! {
fn is_smaller(arr1: &Vec<i32>, arr2: &Vec<i32>) -> (result: bool)
    requires arr1.len() == arr2.len(),
    ensures result == (forall|i: int| 0 <= i < arr1.len() ==> arr1[i] > arr2[i]),
{}
}"""

EXAMPLE_CODE = """fn is_smaller(arr1: &Vec<i32>, arr2: &Vec<i32>) -> bool {
    let mut index = 0;
    while index < arr1.len() {
        if arr1[index] <= arr2[index] { return false; }
        index += 1;
    }
    true
}"""

EXAMPLE_PROGRAM = EXAMPLE_SPEC.replace(
    "{}\n}",
    """{
    let mut index = 0;
    while index < arr1.len() {
        if arr1[index] <= arr2[index] { return false; }
        index += 1;
    }
    true
}
}""",
)


def prompt(task, problem):
    instructions = {
        "nl2code": (
            "You are an expert in Rust. Generate efficient Rust code from "
            "the natural language problem description, in a "
            "Verus-verifiable format. Do not include specifications or proofs."
        ),
        "spec2code": (
            "You are an expert in Verus. Generate efficient Rust code that "
            "aligns with the provided Verus specification. "
            "Include the specification. Do not include proofs."
        ),
        "nl_spec2code": (
            "You are an expert in Verus. Generate efficient Rust code from "
            "the natural language problem description and Verus specification. "
            "Include the specification. Do not include proofs."
        ),
    }

    examples = {
        "nl2code": (EXAMPLE_NL, EXAMPLE_CODE),
        "spec2code": (EXAMPLE_SPEC, EXAMPLE_PROGRAM),
        "nl_spec2code": (
            EXAMPLE_NL + "\n" + EXAMPLE_SPEC,
            EXAMPLE_PROGRAM,
        ),
    }

    if task == "nl2code":
        input_field = problem["description"]
    elif task == "spec2code":
        input_field = problem["spec"]
    elif task == "nl_spec2code":
        input_field = (
            "## Problem Description\n"
            + problem["description"]
            + "\n## Verus Specification\n"
            + problem["spec"]
        )
    else:
        raise ValueError(f"Unsupported CodeGen task: {task}")

    example_input, example_output = examples[task]

    return (
        f"# Instruction\n{instructions[task]}\n"
        f"# Example\nInput:\n{example_input}\n"
        f"Output:\n```rust\n{example_output}\n```\n"
        f"# Input Field\n{input_field}\n"
        "# Output Field\n"
        "Output exactly one complete program inside a ```rust code fence. "
        "No explanations, analysis, or alternative programs. "
        "Preserve the provided function signatures. "
        "Ensure the code is efficient and avoids timeouts."
    )
