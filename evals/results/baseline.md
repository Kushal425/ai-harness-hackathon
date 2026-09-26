# Raven eval baseline

Resolved: 13/13  ·  avg tokens: 3106  ·  avg tool calls: 2.2

| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |
|---|---|---|---|---|---|---|---|---|
| bug_average_single_loop | bug_fix | single_loop | yes | 4216 | 4 | 6 | 0.545 |  |
| bug_average_plan_execute | bug_fix | plan_execute | yes | 2390 | 1 | 5 | 0.405 |  |
| bug_palindrome_single_loop | bug_fix | single_loop | yes | 4185 | 4 | 6 | 0.527 |  |
| bug_palindrome_plan_execute | bug_fix | plan_execute | yes | 2422 | 1 | 5 | 0.372 |  |
| feature_multiply_single_loop | feature | single_loop | yes | 4374 | 4 | 6 | 0.555 |  |
| feature_reverse_words_plan_execute | feature | plan_execute | yes | 3353 | 2 | 6 | 0.376 |  |
| question_average_bug_single_loop | question | single_loop | yes | 1730 | 1 | 3 | 0.412 |  |
| question_palindrome_bug_single_loop | question | single_loop | yes | 1734 | 1 | 3 | 0.382 |  |
| refactor_add_docstrings_single_loop | refactor | single_loop | yes | 4309 | 4 | 6 | 0.51 |  |
| test_writing_add_edge_case_single_loop | test_writing | single_loop | yes | 3485 | 3 | 5 | 0.509 |  |
| bug_average_delegated | bug_fix | delegated | yes | 2397 | 1 | 5 | 0.431 |  |
| bug_palindrome_delegated | bug_fix | delegated | yes | 2429 | 1 | 5 | 0.377 |  |
| feature_reverse_words_delegated | feature | delegated | yes | 3360 | 2 | 6 | 0.395 |  |