# Raven eval baseline

Resolved: 16/16  ·  avg tokens: 3265  ·  avg tool calls: 2.4

| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |
|---|---|---|---|---|---|---|---|---|
| bug_average_single_loop | bug_fix | single_loop | yes | 4700 | 4 | 6 | 0.546 |  |
| bug_average_plan_execute | bug_fix | plan_execute | yes | 2552 | 1 | 5 | 0.398 |  |
| bug_palindrome_single_loop | bug_fix | single_loop | yes | 4669 | 4 | 6 | 0.528 |  |
| bug_palindrome_plan_execute | bug_fix | plan_execute | yes | 2583 | 1 | 5 | 0.398 |  |
| feature_multiply_single_loop | feature | single_loop | yes | 4253 | 4 | 6 | 0.533 |  |
| feature_reverse_words_plan_execute | feature | plan_execute | yes | 3609 | 2 | 6 | 0.383 |  |
| question_average_bug_single_loop | question | single_loop | yes | 1683 | 1 | 3 | 0.392 |  |
| question_palindrome_bug_single_loop | question | single_loop | yes | 1688 | 1 | 3 | 0.392 |  |
| refactor_add_docstrings_single_loop | refactor | single_loop | yes | 4188 | 4 | 6 | 0.541 |  |
| test_writing_add_edge_case_single_loop | test_writing | single_loop | yes | 3389 | 3 | 5 | 0.521 |  |
| bug_average_delegated | bug_fix | delegated | yes | 2559 | 1 | 5 | 0.398 |  |
| bug_palindrome_delegated | bug_fix | delegated | yes | 2590 | 1 | 5 | 0.388 |  |
| feature_reverse_words_delegated | feature | delegated | yes | 3616 | 2 | 6 | 0.388 |  |
| bug_average_reproduce_hidden_test | bug_fix | single_loop | yes | 6066 | 4 | 7 | 0.853 |  |
| crux_chunk_plausible_wrong_patch | bug_fix | crux | yes | 2230 | 3 | 6 | 1.301 |  |
| crux_average_unspecified_crux | bug_fix | crux | yes | 1867 | 3 | 6 | 1.074 |  |