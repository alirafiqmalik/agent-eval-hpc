<VERICONTEST_PROMPT>
# Instruction
You are an expert in Verus. Generate efficient Rust code from the natural language problem description and Verus specification. Include the specification. Do not include proofs.
# Example
Input:
Check whether each element of the second integer vector is smaller than the corresponding element of the first. The vectors have equal length.
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
## Problem Description
# Contains Duplicate

Given an integer array `nums`, return `true` if any value appears **at least twice** in the array, and return `false` if every element is distinct.

## Example 1:
> **Input:** nums = [1,2,3,1]
> **Output:** true
> **Explanation:**
> The element 1 occurs at the indices 0 and 3.

## Example 2:
> **Input:** nums = [1,2,3,4]
> **Output:** false
> **Explanation:**
> All elements are distinct.

## Example 3:
> **Input:** nums = [1,1,1,3,3,4,3,2,4,2]
> **Output:** true

## Constraints:

- $1 <= nums.length <= 10^5$

- $-10^9 <= nums[i] <= 10^9$

## Starter Code

```rust
impl Solution {
    pub fn contains_duplicate(nums: Vec<i32>) -> bool {
        
    }
}
```

## Verus Specification
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
