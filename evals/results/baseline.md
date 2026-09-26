# Raven eval baseline

Resolved: 14/14  ·  avg tokens: 3439  ·  avg tool calls: 2.4

| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |
|---|---|---|---|---|---|---|---|---|
| bug_average_single_loop | bug_fix | single_loop | yes | 4700 | 4 | 6 | 0.945 |  |
| bug_average_plan_execute | bug_fix | plan_execute | yes | 2552 | 1 | 5 | 0.667 |  |
| bug_palindrome_single_loop | bug_fix | single_loop | yes | 4669 | 4 | 6 | 0.89 |  |
| bug_palindrome_plan_execute | bug_fix | plan_execute | yes | 2583 | 1 | 5 | 0.677 |  |
| feature_multiply_single_loop | feature | single_loop | yes | 4253 | 4 | 6 | 0.911 |  |
| feature_reverse_words_plan_execute | feature | plan_execute | yes | 3609 | 2 | 6 | 0.688 |  |
| question_average_bug_single_loop | question | single_loop | yes | 1683 | 1 | 3 | 0.663 |  |
| question_palindrome_bug_single_loop | question | single_loop | yes | 1688 | 1 | 3 | 0.637 |  |
| refactor_add_docstrings_single_loop | refactor | single_loop | yes | 4188 | 4 | 6 | 0.882 |  |
| test_writing_add_edge_case_single_loop | test_writing | single_loop | yes | 3389 | 3 | 5 | 0.88 |  |
| bug_average_delegated | bug_fix | delegated | yes | 2559 | 1 | 5 | 0.654 |  |
| bug_palindrome_delegated | bug_fix | delegated | yes | 2590 | 1 | 5 | 0.656 |  |
| feature_reverse_words_delegated | feature | delegated | yes | 3616 | 2 | 6 | 0.661 |  |
| bug_average_reproduce_hidden_test | bug_fix | single_loop | yes | 6066 | 4 | 7 | 1.396 |  |