# Raven eval baseline

Resolved: 10/10  ·  avg tokens: 2957  ·  avg tool calls: 2.5

| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |
|---|---|---|---|---|---|---|---|---|
| bug_average_single_loop | bug_fix | single_loop | yes | 3957 | 4 | 5 | 0.758 |  |
| bug_average_plan_execute | bug_fix | plan_execute | yes | 2132 | 1 | 4 | 0.822 |  |
| bug_palindrome_single_loop | bug_fix | single_loop | yes | 3929 | 4 | 5 | 0.864 |  |
| bug_palindrome_plan_execute | bug_fix | plan_execute | yes | 2167 | 1 | 4 | 0.567 |  |
| feature_multiply_single_loop | feature | single_loop | yes | 4105 | 4 | 5 | 0.75 |  |
| feature_reverse_words_plan_execute | feature | plan_execute | yes | 3083 | 2 | 5 | 0.559 |  |
| question_average_bug_single_loop | question | single_loop | yes | 1468 | 1 | 2 | 0.532 |  |
| question_palindrome_bug_single_loop | question | single_loop | yes | 1471 | 1 | 2 | 0.528 |  |
| refactor_add_docstrings_single_loop | refactor | single_loop | yes | 4047 | 4 | 5 | 0.828 |  |
| test_writing_add_edge_case_single_loop | test_writing | single_loop | yes | 3211 | 3 | 4 | 0.807 |  |