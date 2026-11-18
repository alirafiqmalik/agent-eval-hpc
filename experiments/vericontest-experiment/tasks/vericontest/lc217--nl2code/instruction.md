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

# Output Field
Output exactly one complete program inside a ```rust code fence. No explanations, analysis, or alternative programs. Preserve the provided function signatures. Ensure the code is efficient and avoids timeouts.
</VERICONTEST_PROMPT>
