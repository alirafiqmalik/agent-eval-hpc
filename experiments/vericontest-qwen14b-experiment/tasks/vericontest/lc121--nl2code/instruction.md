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
# Best Time to Buy and Sell Stock

You are given an array `prices` where `prices[i]` is the price of a given stock on the $i^{th}$ day.

You want to maximize your profit by choosing a **single day** to buy one stock and choosing a **different day in the future** to sell that stock.

Return the *maximum profit you can achieve from this transaction*. If you cannot achieve any profit, return `0`.

## Example 1:

> **Input:** prices = [7,1,5,3,6,4]
> **Output:** 5
> **Explanation:** Buy on day 2 (price = 1) and sell on day 5 (price = 6), profit = 6-1 = 5.
> Note that buying on day 2 and selling on day 1 is not allowed because you must buy before you sell.

## Example 2:

> **Input:** prices = [7,6,4,3,1]
> **Output:** 0
> **Explanation:** In this case, no transactions are done and the max profit = 0.

## Constraints:

- $1 <= prices.length <= 10^5$

- $0 <= prices[i] <= 10^4$

## Starter Code

```rust
impl Solution {
    pub fn max_profit(prices: Vec<i32>) -> i32 {
        
    }
}
```

# Output Field
Output exactly one complete program inside a ```rust code fence. No explanations, analysis, or alternative programs. Preserve the provided function signatures. Ensure the code is efficient and avoids timeouts.
</VERICONTEST_PROMPT>
