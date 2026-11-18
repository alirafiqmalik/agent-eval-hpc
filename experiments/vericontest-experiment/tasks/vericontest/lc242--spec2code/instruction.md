<VERICONTEST_PROMPT>
# Instruction
You are an expert in Verus. Generate efficient Rust code that aligns with the provided Verus specification. Include the specification. Do not include proofs.
# Example
Input:
use vstd::prelude::*;
fn main() {}
verus! {
fn is_smaller(arr1: &Vec<i32>, arr2: &Vec<i32>) -> (result: bool)
    requires arr1.len() == arr2.len(),
    ensures result == (forall|i: int| 0 <= i < arr1.len() ==> arr1[i] > arr2[i]),
{}
}
Output:
```rust
use vstd::prelude::*;
fn main() {}
verus! {
fn is_smaller(arr1: &Vec<i32>, arr2: &Vec<i32>) -> (result: bool)
    requires arr1.len() == arr2.len(),
    ensures result == (forall|i: int| 0 <= i < arr1.len() ==> arr1[i] > arr2[i]),
{
    let mut index = 0;
    while index < arr1.len() {
        if arr1[index] <= arr2[index] { return false; }
        index += 1;
    }
    true
}
}
```
# Input Field
use vstd::prelude::*;

fn main() {}

verus! {

pub struct Solution;

pub open spec fn count_char(s: Seq<char>, c: char) -> int
    decreases s.len()
{
    if s.len() == 0 {
        0
    } else if s.last() == c {
        count_char(s.drop_last(), c) + 1
    } else {
        count_char(s.drop_last(), c)
    }
}

pub open spec fn is_lowercase_word(s: Seq<char>) -> bool {
    forall |i: int| 0 <= i < s.len() ==> 97 <= (#[trigger] s[i] as u32) && (s[i] as u32) <= 122
}

pub open spec fn letter(j: int) -> char {
    (j + 97) as u8 as char
}

pub open spec fn is_anagram_spec(s: Seq<char>, t: Seq<char>) -> bool {
    s.len() == t.len() && forall |j: int| 0 <= j < 26 ==>
        #[trigger] count_char(s, letter(j)) == count_char(t, letter(j))
}

impl Solution {
    pub fn is_anagram(s: String, t: String) -> (res: bool)
        requires
            1 <= s@.len() <= 50_000,
            1 <= t@.len() <= 50_000,
            is_lowercase_word(s@),
            is_lowercase_word(t@),
        ensures
            res == is_anagram_spec(s@, t@),
    {
    }
}

}

# Output Field
Output exactly one complete program inside a ```rust code fence. No explanations, analysis, or alternative programs. Preserve the provided function signatures. Ensure the code is efficient and avoids timeouts.
</VERICONTEST_PROMPT>
