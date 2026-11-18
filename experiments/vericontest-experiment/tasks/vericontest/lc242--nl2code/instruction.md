<VERICONTEST_PROMPT>
# Instruction
You are an expert in Rust. Generate efficient Rust code from the natural language problem description, in a Verus-verifiable format. Do not include specifications or proofs.
# Example
Input:
Check whether each element of the second integer vector is smaller than the corresponding element of the first. The vectors have equal length.
Output:
```rust
fn is_smaller(arr1: &Vec<i32>, arr2: &Vec<i32>) -> bool {
    let mut index = 0;
    while index < arr1.len() {
        if arr1[index] <= arr2[index] { return false; }
        index += 1;
    }
    true
}
```
# Input Field
# Valid Anagram

Given two strings `s` and `t`, return `true` if `t` is an anagram of `s`, and `false` otherwise.

## Example 1:

> **Input:** s = "anagram", t = "nagaram"
> **Output:** true

## Example 2:

> **Input:** s = "rat", t = "car"
> **Output:** false

## Constraints:

- $1 \leq \texttt{s.length}, \texttt{t.length} \leq 5 \times 10^4$
- `s` and `t` consist of lowercase English letters.

## Starter Code

```rust
impl Solution {
    pub fn is_anagram(s: String, t: String) -> bool {
        
    }
}
```

# Output Field
Output exactly one complete program inside a ```rust code fence. No explanations, analysis, or alternative programs. Preserve the provided function signatures. Ensure the code is efficient and avoids timeouts.
</VERICONTEST_PROMPT>
