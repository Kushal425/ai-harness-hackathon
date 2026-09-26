# Raven eval baseline

Resolved: 14/14  ·  avg tokens: 3405  ·  avg tool calls: 2.4

| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |
|---|---|---|---|---|---|---|---|---|
| bug_average_single_loop | bug_fix | single_loop | yes | 4650 | 4 | 6 | 0.895 |  |
| bug_average_plan_execute | bug_fix | plan_execute | yes | 2532 | 1 | 5 | 0.628 |  |
| bug_palindrome_single_loop | bug_fix | single_loop | yes | 4619 | 4 | 6 | 0.995 |  |
| bug_palindrome_plan_execute | bug_fix | plan_execute | yes | 2563 | 1 | 5 | 0.596 |  |
| feature_multiply_single_loop | feature | single_loop | yes | 4203 | 4 | 6 | 1.02 |  |
| feature_reverse_words_plan_execute | feature | plan_execute | yes | 3579 | 2 | 6 | 0.711 |  |
| question_average_bug_single_loop | question | single_loop | yes | 1663 | 1 | 3 | 0.917 |  |
| question_palindrome_bug_single_loop | question | single_loop | yes | 1668 | 1 | 3 | 0.729 |  |
| refactor_add_docstrings_single_loop | refactor | single_loop | yes | 4138 | 4 | 6 | 0.935 |  |
| test_writing_add_edge_case_single_loop | test_writing | single_loop | yes | 3349 | 3 | 5 | 0.856 |  |
| bug_average_delegated | bug_fix | delegated | yes | 2539 | 1 | 5 | 0.686 |  |
| bug_palindrome_delegated | bug_fix | delegated | yes | 2570 | 1 | 5 | 0.652 |  |
| feature_reverse_words_delegated | feature | delegated | yes | 3586 | 2 | 6 | 0.629 |  |
| bug_average_reproduce_hidden_test | bug_fix | single_loop | yes | 6006 | 4 | 7 | 1.539 |  |