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

impl Solution {
    pub fn contains_duplicate(nums: Vec<i32>) -> (res: bool) 
        requires
            1 <= nums.len() <= 100_000, 
            forall |i: int| 0 <= i < nums.len() ==> 
                -1_000_000_000 <= #[trigger] nums[i] <= 1_000_000_000,
        ensures 
            res == (exists |i: int, j: int| 0 <= i < j < nums.len() && nums[i] == nums[j]),
    {
        
    }
}

}
# Output Field
Output exactly one complete program inside a ```rust code fence. No explanations, analysis, or alternative programs. Preserve the provided function signatures. Ensure the code is efficient and avoids timeouts.
</VERICONTEST_PROMPT>
