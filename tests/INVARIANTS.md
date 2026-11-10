# Invariants and the tests that pin them

The design contract lists 80 numbered invariants, each a testable rule named after the
defect it prevents. This table maps every one to the tests that cover it. All 80 apply
to v1; none is out of scope.

Several invariants describe line-editor defects (1-12) that the canonical writer
removes by construction. They are still tested: the test feeds the hand-written shape
that used to break a line editor and checks the behaviour the invariant protects.

| # | Rule | Test(s) |
|---|---|---|
| 1 | one parser for every reader and writer | `test_model_io.py::test_one_parser_shared_by_readers_and_writers`<br>`test_model_io.py::test_decoy_ids_inside_block_and_quoted_scalars_are_content` |
| 2 | step markers count only under `steps:` | `test_model_io.py::test_step_markers_count_only_under_steps_key` |
| 3 | `- id:` inside block/quoted scalars is content | `test_model_io.py::test_decoy_ids_inside_block_and_quoted_scalars_are_content` |
| 4 | single-quoted scalar closes only on an unpaired quote | `test_model_io.py::test_single_quote_closed_only_by_unpaired_quote` |
| 5 | new steps go before trailing top-level keys | `test_model_io.py::test_new_steps_insert_before_trailing_top_level_keys` |
| 6 | mixed-indent: writers refuse, doctor fixes | `test_model_io.py::test_mixed_indent_is_refused_by_every_writer` |
| 7 | new step at the file's one indent | `test_model_io.py::test_new_step_uses_one_consistent_indent` |
| 8 | round-trip gate or nothing written | `test_model_io.py::test_round_trip_gate_writes_nothing_on_mismatch` |
| 9 | field edits replace the whole construct | `test_model_io.py::test_field_edit_replaces_the_whole_construct` |
| 10 | trailing comments never become values | `test_model_io.py::test_inline_trailing_comments_never_become_values` |
| 11 | block and inline `depends_on` both parse and rewrite | `test_model_io.py::test_block_and_inline_depends_both_parse_and_rewrite` |
| 12 | `a: b` never becomes a mapping | `test_model_io.py::test_prose_with_colon_never_becomes_a_mapping` |
| 13 | `--notes` over notes needs `--replace-notes` | `test_add_edit.py::test_notes_over_existing_notes_refuse_without_replace` |
| 14 | `--append-notes` keeps every prior line | `test_add_edit.py::test_append_notes_preserves_block_and_escaped_scalars` |
| 15 | empty payloads refused | `test_add_edit.py::test_empty_or_whitespace_payloads_are_refused` |
| 16 | multi-line notes survive add | `test_model_io.py::test_multiline_notes_survive_add_and_edit`<br>`test_add_edit.py::test_batch_jsonl_form_carries_multiline_notes` |
| 17 | batch: `\|` literal, overflow fails loudly, apostrophes survive | `test_add_edit.py::test_batch_escaped_pipe_overflow_and_apostrophes` |
| 18 | batch reports attempted/added/delta | `test_add_edit.py::test_batch_reports_attempted_added_and_delta` |
| 19 | target defaults to any; unknown refused on write | `test_add_edit.py::test_add_defaults_target_any_and_refuses_unknown` |
| 20 | no self-dependency | `test_add_edit.py::test_self_dependency_refused_in_add_and_edit_step` |
| 21 | cycles refused only through the edited step | `test_add_edit.py::test_depends_refuses_cycles_through_edited_step_only` |
| 22 | whole-task deps writable only when the task exists | `test_add_edit.py::test_whole_task_deps_writable_only_when_task_exists` |
| 23 | done/readiness/doctor resolve deps identically | `test_close.py::test_done_readiness_and_doctor_resolve_deps_identically` |
| 24 | skipped is terminal everywhere | `test_close.py::test_skipped_counts_as_terminal_for_every_reader` |
| 25 | dangling dep: done blocks, readiness MALFORMED | `test_close.py::test_dangling_dep_blocks_done_and_reads_malformed`<br>`test_doctor.py::test_check_bad_deps_against_global_index` |
| 26 | whole-task dep ignores task-level status | `test_close.py::test_whole_task_dep_ignores_task_level_status` |
| 27 | done never overwrites attribution | `test_close.py::test_done_never_overwrites_attribution` |
| 28 | done on done is a no-op | `test_close.py::test_done_on_done_is_a_noop` |
| 29 | done refuses targets not workable here | `test_close.py::test_done_refuses_target_not_workable_here`<br>`test_needs_ready.py::test_restricted_targets_never_widen_any_works_everywhere` |
| 30 | done rejects unknown flags | `test_close.py::test_done_rejects_unknown_flags` |
| 31 | skip/reopen need --reason; no-op writes nothing | `test_close.py::test_skip_and_reopen_require_reason_and_refuse_noops` |
| 32 | --superseded-by must resolve | `test_close.py::test_superseded_by_must_resolve` |
| 33 | skip/edit-step reach archived suffixed files | `test_close.py::test_skip_and_edit_step_reach_archived_editorial_filenames` |
| 34 | reopen keeps retracted stamps | `test_close.py::test_reopen_preserves_retracted_stamps` |
| 35 | resolution by id field; ambiguity refused; queue wins | `test_ids_and_guard.py::test_resolution_by_id_field_ambiguity_and_queue_wins` |
| 36 | new refuses an existing file | `test_ids_and_guard.py::test_new_refuses_an_existing_destination` |
| 37 | task mint sees unlanded siblings and suffixed names | `test_ids_and_guard.py::test_task_mint_sees_unlanded_siblings_and_suffixed_names` |
| 38 | task mint fetches; no-remote silent, unreachable warns | `test_ids_and_guard.py::test_task_mint_fetches_first_and_distinguishes_no_remote` |
| 39 | step mint refuses sibling gaps; unreachable = 2 | `test_ids_and_guard.py::test_step_mint_refuses_when_origin_holds_unknown_ids`<br>`test_ids_and_guard.py::test_step_mint_unreachable_origin_is_not_evaluable` |
| 40 | guard uses the minter's ref list | `test_ids_and_guard.py::test_guard_reads_the_minters_ref_list_including_slot_branches` |
| 41 | batch-add runs the guard once | `test_ids_and_guard.py::test_batch_add_runs_guard_once` |
| 42 | renumber never overwrites an occupied id | `test_ids_and_guard.py::test_renumber_never_overwrites_an_occupied_id` |
| 43 | renumber rewrites refs boundary-safely | `test_ids_and_guard.py::test_renumber_rewrites_refs_boundary_safely` |
| 44 | renumber keeps external refs when another claimant remains | `test_ids_and_guard.py::test_renumber_leaves_external_refs_when_another_claimant_remains` |
| 45 | renumber --next is sibling-aware | `test_ids_and_guard.py::test_renumber_next_is_sibling_aware_and_uses_git_mv` |
| 46 | same title = modified, not collision | `test_archive_and_checks.py::test_check_collisions_same_title_is_modified_not_collision` |
| 47 | duplicate ids: same tier blocks, cross tier info | `test_archive_and_checks.py::test_duplicate_ids_same_tier_blocks_cross_tier_is_info`<br>`test_archive_and_checks.py::test_duplicate_ids_baseline_demotes_known_and_fails_closed` |
| 48 | archive never overwrites a different task | `test_archive_and_checks.py::test_archive_never_overwrites_a_different_task` |
| 49 | unparseable/stepless never archivable; skipped-only is | `test_archive_and_checks.py::test_unparseable_or_stepless_never_archivable_skipped_only_is` |
| 50 | unarchive refuses divergent copies and unparseable files | `test_archive_and_checks.py::test_unarchive_refuses_divergent_copies_and_unparseable` |
| 51 | a ref subject binds; a mention does not | `test_claims.py::test_subject_that_is_a_ref_binds_a_mention_does_not` |
| 52 | --step repeated: one claim per step | `test_claims.py::test_repeated_step_flag_mints_one_claim_per_step` |
| 53 | slots and machines never share a counter | `test_claims.py::test_slots_and_machines_never_share_a_counter`<br>`test_claims.py::test_release_all_mine_after_a_detected_slot_claim` |
| 54 | primary / non-git trees use the machine namespace | `test_claims.py::test_primary_and_non_git_trees_use_the_machine_namespace` |
| 55 | --all-mine only this slot; --all-box refuses peers | `test_claims.py::test_release_all_mine_touches_only_this_slot`<br>`test_claims.py::test_release_all_mine_after_a_detected_slot_claim` |
| 56 | claims states what it cannot see | `test_claims.py::test_claims_output_states_what_it_cannot_see` |
| 57 | union: monotonic release, no double count | `test_claims.py::test_sibling_union_release_is_monotonic_and_not_double_counted` |
| 58 | unparseable sibling reported; enumeration failure = NE | `test_claims.py::test_unparseable_sibling_reported_and_enumeration_failure_not_evaluable` |
| 59 | claim-check non-zero when siblings unreadable | `test_claims.py::test_claim_check_unreadable_sibling_view_is_nonzero`<br>`test_claims.py::test_claim_check_excludes_own_claims` |
| 60 | staleness > 3 days, advisory | `test_claims.py::test_staleness_rule` |
| 61 | collision tie-break: first to upstream | `test_claims.py::test_collision_tie_break_is_first_to_reach_upstream` |
| 62 | damaged claims.yaml: readiness/summary refuse | `test_claims.py::test_damaged_claims_make_readiness_and_summary_refuse`<br>`test_cli_notes_status.py::test_status_summary_absent_queue_is_not_evaluable` |
| 63 | merge driver rules | `test_claims.py::test_merge_driver_rules`<br>`test_claims.py::test_merge_driver_installed_unions_claims_across_branches`<br>`test_claims.py::test_without_the_merge_driver_the_same_merge_conflicts` |
| 64 | rate gate: pace, flood, no budget on trip, expiry | `test_rategate.py::test_paced_use_never_trips_flood_trips_window_expiry_reopens`<br>`test_rategate.py::test_reads_are_never_counted` |
| 65 | rate gate fails open | `test_rategate.py::test_gate_fails_open_on_corrupt_or_unwritable_state`<br>`test_rategate.py::test_cap_zero_disables` |
| 66 | exemption only for this slot's active claim | `test_rategate.py::test_claimed_close_exemption_only_for_this_slots_active_claim` |
| 67 | metered by verb identity, every writer | `test_rategate.py::test_every_mutating_verb_is_metered_whatever_its_spelling` |
| 68 | probe with no answer = NE, not cached | `test_needs_ready.py::test_probe_without_answer_is_not_evaluable_and_not_cached`<br>`test_needs_ready.py::test_operator_opens_operator_clearable_gates_only`<br>`test_needs_ready.py::test_date_reached_gate` |
| 69 | probes run at the repo root | `test_needs_ready.py::test_probes_run_with_cwd_at_repo_root` |
| 70 | malformed needs resolve unknown without crashing | `test_needs_ready.py::test_malformed_needs_resolve_unknown_without_crashing`<br>`test_needs_ready.py::test_capability_tokens_never_gate` |
| 71 | restricted targets never widen; any works everywhere | `test_needs_ready.py::test_restricted_targets_never_widen_any_works_everywhere` |
| 72 | malformed excluded; counts a floor | `test_needs_ready.py::test_malformed_steps_excluded_from_every_count_and_flagged_as_floor`<br>`test_needs_ready.py::test_ready_reads_sibling_claims` |
| 73 | unparseable files named, not skipped | `test_doctor.py::test_check_names_unparseable_files_and_classifies` |
| 74 | task-level status / notes text never flagged | `test_doctor.py::test_task_level_status_and_notes_text_never_flagged` |
| 75 | doctor refuses fixes that change ids or key shape | `test_doctor.py::test_fix_refused_when_ids_or_key_shape_would_change`<br>`test_doctor.py::test_fix_repairs_each_class_with_backup`<br>`test_doctor.py::test_selftest_passes` |
| 76 | absorbed-step needs a step-shaped id and a status line | `test_doctor.py::test_absorbed_step_needs_step_shaped_id_and_status`<br>`test_doctor.py::test_fixtures_reproduce_their_failure_classes` |
| 77 | note --help / unknown flag writes nothing; own/unknown slot refused | `test_cli_notes_status.py::test_note_help_and_unknown_flags_write_nothing`<br>`test_cli_notes_status.py::test_note_refuses_unknown_or_own_slot` |
| 78 | --json clean from byte 0 | `test_cli_notes_status.py::test_summary_json_is_clean_from_byte_zero`<br>`test_cli_notes_status.py::test_every_count_prints_its_denominator` |
| 79 | show truncates; --full shows all | `test_cli_notes_status.py::test_show_truncates_but_full_shows_everything` |
| 80 | every count carries its denominator | `test_cli_notes_status.py::test_every_count_prints_its_denominator` |

## Features added after the contract (0.2 expansion)

New verbs are pinned the same way, outside the 80 numbered invariants.

| Feature | Test(s) |
|---|---|
| `closures`: a done step with no `commits:` is unevidenced, counted over done steps, informational (exit 0) | `test_closures.py::test_a_done_step_without_commits_is_unevidenced_with_its_denominator`<br>`test_closures.py::test_unevidenced_alone_is_informational_exit_0` |
| `closures`: a recorded commit outside the baseline, or gone, is unreachable; a merged one is not | `test_closures.py::test_a_recorded_commit_not_in_main_is_unreachable_and_a_merged_one_is_not`<br>`test_closures.py::test_a_recorded_commit_that_no_longer_exists_is_unreachable`<br>`test_closures.py::test_all_reachable_is_clean_exit_0` |
| `closures`: a closing verb followed by the ref (or trailer plus verb) flags an open step; a mere mention does not | `test_closures.py::test_a_subject_that_closes_an_open_step_is_flagged`<br>`test_closures.py::test_a_subject_that_only_mentions_the_step_is_not_flagged`<br>`test_closures.py::test_a_trailer_with_a_closing_verb_is_flagged_and_a_trailer_alone_is_not`<br>`test_closures.py::test_a_closing_commit_for_a_done_step_is_not_flagged`<br>`test_closures.py::test_days_window_and_other_branches` |
| `closures`: a path cited in a done step's notes that is absent at HEAD is flagged; open steps are not checked | `test_closures.py::test_a_cited_path_missing_at_head_is_flagged_and_an_existing_one_is_not`<br>`test_closures.py::test_open_steps_notes_are_not_checked_for_artifacts` |
| `closures`: unmerged remote branches are listed only when they name a step, with a total; merged ones and HEAD are not | `test_closures.py::test_unmerged_remote_branches_list_only_those_naming_a_step_plus_a_total`<br>`test_closures.py::test_a_merged_remote_branch_and_head_are_not_unmerged` |
| `closures`: what cannot run is NOT EVALUABLE (exit 2) with its cause, the other classes still report; read only | `test_closures.py::test_no_baseline_names_the_classes_that_could_not_run_and_reports_the_rest`<br>`test_closures.py::test_outside_git_only_the_queue_class_runs`<br>`test_closures.py::test_a_missing_queue_is_not_evaluable`<br>`test_closures.py::test_closures_is_read_only`<br>`test_closures.py::test_text_output_prints_counts_beside_denominators_and_the_commit` |
| MCP `crossbox_closures` is read only and takes no arguments | `test_plugin.py::test_tools_list_is_the_fixed_allowlist`<br>`test_plugin.py::test_each_tool_runs_exactly_the_expected_argv` |
| `lint`: a gate named right after its phrase and absent from depends_on is found; a mere mention, an action target or a gate already carried is not | `test_prose_lint.py::test_a_gate_named_in_prose_that_depends_on_omits_is_found`<br>`test_prose_lint.py::test_a_mere_mention_is_not_a_gate`<br>`test_prose_lint.py::test_a_step_named_away_from_the_gating_phrase_is_not_a_gate`<br>`test_prose_lint.py::test_only_the_step_right_after_the_gating_phrase_is_the_gate` |
| `lint`: an opening decision marker on an open step is found; one inside a sentence, a moved-here note or a candidate marker is not | `test_prose_lint.py::test_a_decision_marker_on_an_open_step_is_found`<br>`test_prose_lint.py::test_a_decision_word_inside_a_sentence_is_not_a_marker`<br>`test_prose_lint.py::test_moved_here_and_candidate_markers_are_not_decisions` |
| `why` agrees with `ready` on every open step | `test_why.py::test_why_agrees_with_ready_on_every_open_step` |
| `why` walks unmet dependencies to the roots (READY, CLAIMED, ELSEWHERE named) | `test_why.py::test_chain_reaches_the_workable_root`<br>`test_why.py::test_a_claimed_dependency_names_the_holder`<br>`test_why.py::test_a_step_for_another_machine_names_who_provides_its_target`<br>`test_why.py::test_task_level_dependency_expands_to_that_tasks_open_steps` |
| `why` explains a step in a malformed task file (and its dependants) as MALFORMED; deep chains do not crash | `test_why.py::test_a_step_in_a_malformed_file_is_a_malformed_node_not_an_abort`<br>`test_why.py::test_an_unparseable_file_and_a_twice_defined_id_are_malformed_nodes`<br>`test_why.py::test_a_very_deep_dependency_chain_is_explained_not_a_recursion_error` |
| `why` without a ref uses `ready`'s denominator and names unreadable files apart | `test_why.py::test_no_ref_denominator_is_readys_and_unreadable_files_are_named_apart` |
| `why` never loops, never reads an unanswered gate as an answer | `test_why.py::test_cycle_is_reported_not_followed_forever`<br>`test_why.py::test_gates_report_state_and_cause_and_unanswered_probe_is_not_evaluable` |
| `why` on closed and unknown refs; read only | `test_why.py::test_closed_and_unknown_refs`<br>`test_why.py::test_why_is_read_only` |
| `why` without a ref prints its denominator | `test_why.py::test_no_ref_prints_the_queue_summary_with_its_denominator` |
| MCP `crossbox_why` passes only a step ref or task id | `test_plugin.py::test_each_tool_runs_exactly_the_expected_argv`<br>`test_plugin.py::test_why_refuses_a_ref_that_is_not_a_step_or_task` |
| `activity`: a commit subject or `Crossbox-Step` trailer naming the step makes its claim HOT; a longer ref never matches | `test_activity.py::test_a_commit_naming_the_step_makes_its_claim_hot`<br>`test_activity.py::test_a_trailer_counts_and_a_longer_ref_does_not`<br>`test_activity.py::test_punctuation_after_the_ref_still_matches` |
| `activity`: QUIET vs COLD by age and evidence; evidence older than the claim does not count | `test_activity.py::test_no_commit_on_an_old_claim_is_cold_and_says_what_was_not_seen`<br>`test_activity.py::test_a_fresh_claim_without_commits_is_quiet_not_cold`<br>`test_activity.py::test_old_evidence_inside_the_stale_window_is_quiet`<br>`test_activity.py::test_a_commit_before_the_claim_was_made_does_not_count` |
| `activity`: branches and unpushed sibling-worktree commits are evidence | `test_activity.py::test_a_branch_named_for_the_step_counts`<br>`test_activity.py::test_unpushed_commits_in_a_sibling_worktree_are_seen` |
| `activity`: branch names match as whole tokens; a branch is dated by its reflog, else its tip | `test_activity.py::test_a_branch_for_a_longer_step_id_is_not_evidence_for_the_shorter`<br>`test_activity.py::test_branch_names_match_as_whole_tokens_case_insensitively`<br>`test_activity.py::test_a_branch_is_dated_by_its_own_creation_not_its_tip_commit`<br>`test_activity.py::test_a_branch_without_a_reflog_falls_back_to_its_tip_date` |
| `activity`: a repository with no commits is UNKNOWN with that cause, exit 2 | `test_activity.py::test_a_repo_with_no_commits_says_so_and_is_not_evaluable` |
| `activity`: what cannot be judged is UNKNOWN, never COLD; read only | `test_activity.py::test_a_subject_only_claim_is_unknown_not_cold`<br>`test_activity.py::test_outside_git_every_claim_is_unknown_and_the_run_is_not_evaluable`<br>`test_activity.py::test_activity_is_read_only` |
| `activity`: open steps with commits but no claim are listed; closed ones are not | `test_activity.py::test_unclaimed_work_is_listed_and_closed_steps_are_not` |
| `done --commit`: full sha recorded after it is checked; unknown commits refused with nothing written; outside git NOT EVALUABLE | `test_done_commit.py::test_records_the_full_sha_and_grades_it_reachable`<br>`test_done_commit.py::test_an_unknown_commit_is_refused_and_nothing_is_written`<br>`test_done_commit.py::test_outside_git_a_commit_cannot_be_checked` |
| `done --commit`: REACHABLE vs UNMERGED; a commit that does not name the step warns | `test_done_commit.py::test_a_commit_only_on_a_side_branch_is_unmerged`<br>`test_done_commit.py::test_a_commit_that_does_not_name_the_step_is_recorded_with_a_warning` |
| `done --commit`: evidence added once to a closed step, retracted by reopen, shown by show | `test_done_commit.py::test_evidence_can_be_added_to_a_closed_step_once`<br>`test_done_commit.py::test_reopen_retracts_the_recorded_commits`<br>`test_done_commit.py::test_show_lists_the_commits` |
| MCP `crossbox_done` passes only `HEAD` or 7-40 hex digits as a commit | `test_plugin.py::test_done_refuses_a_commit_that_is_not_a_sha_or_head` |

## Items that were open in the original design

Each is fixed in v1 and has a test:

| Item | Test |
|---|---|
| rate gate did not meter alias spellings and several writers | `test_rategate.py::test_every_mutating_verb_is_metered_whatever_its_spelling` |
| `renumber --next` was not sibling-aware | `test_ids_and_guard.py::test_renumber_next_is_sibling_aware_and_uses_git_mv` |
| `add` folded newlines in notes | `test_model_io.py::test_multiline_notes_survive_add_and_edit` |
| `done` ate unknown flags | `test_close.py::test_done_rejects_unknown_flags` |
| `done` flipped skipped to done | `test_close.py::test_done_refuses_to_flip_skipped` |
| no-domain `new` ignored the archive and siblings | `test_ids_and_guard.py::test_legacy_mint_is_archive_and_sibling_aware` |
| `claim-check` used one exit code for a hit and for not evaluable | `test_claims.py::test_claim_check_unreadable_sibling_view_is_nonzero` |
| readiness read only local claims | `test_needs_ready.py::test_ready_reads_sibling_claims` |
| pre-commit installer failed in a worktree (`.git` is a file) | `test_doctor.py::test_install_hook_works_in_a_linked_worktree_and_blocks_bad_commits` |
| detected-slot claims were not "mine" to `release --all-mine` | `test_claims.py::test_release_all_mine_after_a_detected_slot_claim` |
| `add --depends` targets were not existence-checked | `test_add_edit.py::test_whole_task_deps_writable_only_when_task_exists` |
| move/swap/reorder ignored cross-task refs into the task | `test_add_edit.py::test_swap_and_reorder_rewrite_deps_and_cross_task_refs` |
| `status` swallowed unknown flags | `test_cli_notes_status.py::test_status_rejects_unknown_flags` |
| argparse usage errors exited 2 | `test_cli_notes_status.py::test_usage_errors_exit_64_not_2` |

## Release-review findings (2026-09-24 review)

Each blocker the public-release review raised, with the tests that pin the fix:

| Finding | Test(s) |
|---|---|
| duplicate YAML keys kept the last value, so a write could drop a whole `steps:` block; the same held for claims, config and the needs registry | `test_strict_yaml.py::test_no_mutating_verb_rewrites_a_task_with_a_duplicate_key`<br>`test_strict_yaml.py::test_duplicate_key_in_claims_is_not_evaluable`<br>`test_strict_yaml.py::test_duplicate_key_in_config_is_not_evaluable`<br>`test_strict_yaml.py::test_duplicate_key_in_needs_registry_is_not_evaluable` |
| no input budget before parsing (alias expansion on the next canonical write; oversized files) | `test_strict_yaml.py::test_an_alias_bomb_is_refused_before_it_expands`<br>`test_strict_yaml.py::test_an_oversized_document_is_refused_unread` |
| configured data paths could point outside the repository or into `.git` (absolute, `..`, symlink) | `test_config_boundary.py::test_a_data_path_outside_the_repo_or_inside_git_is_refused`<br>`test_config_boundary.py::test_a_symlinked_data_dir_that_leaves_the_repo_is_refused`<br>`test_config_boundary.py::test_an_escaping_path_writes_nothing_outside` |
| a wrong-shaped config field raised a Python exception | `test_config_boundary.py::test_a_wrong_shaped_field_is_not_evaluable_and_named`<br>`test_config_boundary.py::test_a_wrong_shaped_machine_is_not_evaluable_and_named` |
| an unparseable sibling claims file was listed by `claims` but ignored by `ready` and `status --summary`, which exited 0 | `test_view_completeness.py::test_an_unparseable_sibling_claims_file_makes_every_consumer_not_evaluable`<br>`test_view_completeness.py::test_ready_json_names_the_incomplete_view`<br>`test_view_completeness.py::test_a_complete_view_stays_evaluable` |
| `git show` read every failure as "absent" and `ls-tree` failures as an empty list | `test_view_completeness.py::test_show_distinguishes_absent_from_failure`<br>`test_view_completeness.py::test_ls_names_raises_instead_of_returning_an_empty_list`<br>`test_view_completeness.py::test_an_unreadable_sibling_ref_is_not_an_empty_claims_file`<br>`test_view_completeness.py::test_origin_guard_is_not_evaluable_when_a_sibling_read_fails`<br>`test_view_completeness.py::test_check_collisions_is_not_evaluable_when_the_ref_cannot_be_read` |
| a gate probe that timed out or exited outside 0/1 left `ready` at exit 0 | `test_needs_ready.py::test_a_not_evaluable_probe_makes_ready_exit_2_and_keeps_the_step_detail`<br>`test_needs_ready.py::test_a_closed_probe_is_an_answer_not_a_failure`<br>`test_needs_ready.py::test_summary_with_ready_is_not_evaluable_when_a_probe_is`<br>`test_needs_ready.py::test_summary_text_names_the_probe_not_malformed_steps` |
| machine and slot names reached note file names unchecked (`--to workstation/../../x` wrote outside the notes directory) | `test_identity_grammar.py::test_a_remote_recipient_slot_outside_the_grammar_is_refused`<br>`test_identity_grammar.py::test_an_environment_identity_outside_the_grammar_is_not_evaluable`<br>`test_identity_grammar.py::test_a_configured_machine_name_outside_the_grammar_is_not_evaluable`<br>`test_identity_grammar.py::test_the_grammar_refuses_separators_traversal_and_control_characters` |
| `needs audit` skipped task files it could not read and exited 0 | `test_needs_ready.py::test_needs_audit_names_unparseable_tasks_and_is_not_evaluable`<br>`test_needs_ready.py::test_needs_audit_on_a_clean_queue_still_exits_0` |
| an atomic replace did not sync the directory entry | `test_model_io.py::test_an_atomic_write_syncs_the_directory_entry` |

## Third-review findings

Each finding of the third adversarial review, with the tests that pin the fix:

| Finding | Test(s) |
|---|---|
| a scalar token, an unknown or wrong-case kind, a token list, a non-string probe, or an alias to nothing silently erased a gate (or crashed with a traceback) | `test_absent_and_malformed_inputs.py::test_a_malformed_registry_is_not_evaluable_never_ready`<br>`test_absent_and_malformed_inputs.py::test_a_malformed_registry_never_crashes_a_writer`<br>`test_absent_and_malformed_inputs.py::test_a_registry_that_is_a_directory_is_not_evaluable` |
| a scalar, mapping, nested-list or unknown `needs` value was READY | `test_absent_and_malformed_inputs.py::test_a_needs_value_of_the_wrong_shape_is_malformed_never_ready`<br>`test_absent_and_malformed_inputs.py::test_writers_refuse_an_unknown_need_when_a_registry_exists` |
| an absent or mis-pathed registry opened every gate | `test_absent_and_malformed_inputs.py::test_absent_registry_with_needs_is_not_evaluable`<br>`test_absent_and_malformed_inputs.py::test_absent_registry_without_needs_stays_clean`<br>`test_absent_and_malformed_inputs.py::test_writers_warn_on_needs_when_no_registry` |
| duplicate, missing or non-letter step ids were READY while every writer refused the file | `test_absent_and_malformed_inputs.py::test_duplicate_or_invalid_step_ids_are_malformed_not_ready`<br>`test_absent_and_malformed_inputs.py::test_status_summary_ready_agrees_with_ready_on_malformed_steps` |
| a missing queue or a nonexistent `--root` read as clean in every reader but `status` | `test_absent_and_malformed_inputs.py::test_every_reader_exits_2_when_the_queue_is_missing`<br>`test_absent_and_malformed_inputs.py::test_every_reader_exits_2_for_a_root_that_does_not_exist`<br>`test_absent_and_malformed_inputs.py::test_a_root_that_does_not_exist_is_never_created_or_written` |
| unparseable or mis-shaped task files left `check-duplicate-ids`, `check-empty-tasks` and `needs audit` at exit 0 | `test_absent_and_malformed_inputs.py::test_check_duplicate_ids_with_an_unparseable_file_is_not_evaluable`<br>`test_absent_and_malformed_inputs.py::test_check_empty_tasks_with_an_unparseable_file_is_not_evaluable`<br>`test_absent_and_malformed_inputs.py::test_needs_audit_with_steps_not_a_list_is_not_evaluable` |
| parallel verbs in one tree lost writes and exited 0; a failed writer's restore erased another's success | `test_concurrency.py::test_parallel_add_loses_nothing`<br>`test_concurrency.py::test_parallel_claims_lose_nothing`<br>`test_concurrency.py::test_restore_never_overwrites_a_foreign_write`<br>`test_concurrency.py::test_writer_waits_for_the_lock_and_times_out_not_evaluable` |
| the rate gate was bypassed by parallel callers | `test_concurrency.py::test_rate_gate_holds_under_parallel_callers` |
| `reorder`, `swap`, `move-step` and `renumber` moved a claim onto other work or detached it | `test_claim_identity.py::test_reorder_carries_the_local_claim_to_the_same_step`<br>`test_claim_identity.py::test_swap_carries_the_local_claim`<br>`test_claim_identity.py::test_move_step_carries_the_local_claim`<br>`test_claim_identity.py::test_renumber_carries_the_local_claim`<br>`test_claim_identity.py::test_rename_refuses_when_another_sessions_claim_binds_the_step`<br>`test_claim_identity.py::test_ready_reports_an_orphaned_claim` |
| the claims view unioned by id, so a same-id sibling claim was dropped and releasing it released yours | `test_claim_identity.py::test_view_keeps_distinct_claims_that_share_an_id`<br>`test_claim_identity.py::test_release_does_not_cross_identities`<br>`test_claim_identity.py::test_the_same_claim_seen_at_two_refs_counts_once` |
| the claims merge driver was neither idempotent nor commutative | `test_claim_identity.py::test_merge_is_idempotent_on_colliding_claims`<br>`test_claim_identity.py::test_merge_is_commutative_on_colliding_claims`<br>`test_claim_identity.py::test_criss_cross_merges_converge_on_one_id_per_claim`<br>`test_claim_identity.py::test_release_follows_the_claim_through_a_renumber` |
| the origin guard and id minter could not see a sibling worktree's uncommitted mint | `test_ids_and_guard.py::test_mint_sees_an_uncommitted_task_in_a_sibling_worktree`<br>`test_ids_and_guard.py::test_guard_sees_an_uncommitted_step_in_a_sibling_worktree` |
| the example PreToolUse guard allowed registry/config writes, `sed -i`, `cp`, `mv`, NotebookEdit and case variants | `test_queue_guard.py` (table of deny and allow events) |
| selftest covered three of four repair classes; installers wrote an unquoted command; the first add in a clone of an empty remote was blocked | `test_doctor.py::test_selftest_covers_every_repair_class`<br>`test_doctor.py::test_installed_command_is_absolute_and_quoted`<br>`test_ids_and_guard.py::test_first_add_in_a_clone_of_an_empty_remote_is_not_blocked` |

## Fourth-review findings

| Finding | Test(s) |
|---|---|
| deleting a claimed step and adding another reused the id, so the old claim bound different work | `test_claim_identity.py::test_removing_my_claimed_step_is_refused_without_force`<br>`test_claim_identity.py::test_claim_delete_add_never_claims_the_replacement`<br>`test_claim_identity.py::test_force_removal_releases_my_claim_and_says_so`<br>`test_claim_identity.py::test_removing_a_step_another_session_claimed_is_refused_then_add_cannot_inherit`<br>`test_claim_identity.py::test_add_that_would_reuse_a_claimed_id_is_refused` |
| a swap/move/reorder/remove refused by a dependent's validation had already rewritten the primary task | `test_whole_or_nothing_writes.py::test_rejected_swap_changes_nothing`<br>`test_whole_or_nothing_writes.py::test_rejected_move_step_changes_nothing`<br>`test_whole_or_nothing_writes.py::test_rejected_reorder_changes_nothing`<br>`test_whole_or_nothing_writes.py::test_rejected_remove_step_force_changes_nothing`<br>`test_whole_or_nothing_writes.py::test_a_write_failure_mid_commit_restores_every_file` |

## Fifth-review findings

| Finding | Test(s) |
|---|---|
| an unknown config key was silently defaulted (a typo'd key read as the default) | `test_config_boundary.py::test_an_unknown_config_key_is_reported_not_silently_defaulted` |
| `--root <subdir>`: the origin guard, task-id minting, the claims view, `check-collisions` and the staged check read the OUTER repository's queue (S1-S4, S6) | all of `test_root_subdirectory.py`: `test_root_subdirectory.py::test_claims_at_a_subdirectory_root_never_read_the_outer_queue`<br>`test_root_subdirectory.py::test_origin_guard_at_a_subdirectory_root_compares_the_same_file`<br>`test_root_subdirectory.py::test_origin_guard_at_a_subdirectory_root_reads_the_sibling_worktree_copy`<br>`test_root_subdirectory.py::test_staged_check_at_a_subdirectory_root_sees_its_staged_tasks`<br>`test_root_subdirectory.py::test_staged_check_with_an_unreadable_index_is_not_evaluable` |
| S5: `doctor install-hook` under a subdirectory `--root` installed a hook with no `--root`, which exited 2 and blocked every commit in the repository | `test_root_subdirectory.py::test_hook_installed_under_a_subdirectory_root_names_that_root`<br>`test_root_subdirectory.py::test_hook_at_the_toplevel_keeps_the_command_unchanged` |
| F2: a mistyped `remote:` made the origin guard local-only and answer OK while origin held the id | `test_ids_and_guard.py::test_a_mistyped_remote_makes_the_guard_and_minter_not_evaluable`<br>`test_ids_and_guard.py::test_a_lone_repo_without_remote_setting_stays_local_only`<br>`test_ids_and_guard.py::test_an_explicit_remote_that_exists_is_still_used` |
| F3: renumber / move-step / swap / reorder / remove-step skipped an unparseable dependent file and left a dangling reference at exit 0 | `test_absent_and_malformed_inputs.py::test_renaming_verbs_refuse_when_an_unparseable_file_may_hold_the_reference`<br>`test_absent_and_malformed_inputs.py::test_an_unparseable_file_that_cannot_name_the_task_does_not_block` |
| F4: argparse abbreviations ran `--for` as `--force` | `test_cli_notes_status.py::test_a_flag_abbreviation_is_a_usage_error_not_the_long_flag`<br>`test_cli_notes_status.py::test_an_abbreviated_top_level_flag_is_a_usage_error` |
| F5: an invalid `CROSSBOX_RATE_CAP` / `CROSSBOX_RATE_WINDOW` silently disabled or ignored the gate | `test_rategate.py::test_an_invalid_rate_env_is_not_evaluable_and_names_the_variable`<br>`test_rategate.py::test_a_bad_cap_does_not_hide_behind_a_good_window_and_reads_are_unmetered`<br>`test_rategate.py::test_documented_rate_env_values_still_work` |
| F6: plain `status` exited 0 having skipped an unparseable task, while `status --summary` and `ready` exit 1 and the docs said 2. Resolved on the floor design: all three listings exit 1, name the file and mark the counts as a floor; the docs now say so | `test_cli_notes_status.py::test_plain_status_with_an_unparseable_file_exits_1_like_summary`<br>`test_cli_notes_status.py::test_plain_status_on_a_clean_queue_exits_0`<br>`test_cli_notes_status.py::test_every_count_prints_its_denominator` |
| F7: at the cap `done` was exempt but `release --all-mine` was not; the docs said every mutating verb is metered | `test_rategate.py::test_release_all_mine_is_exempt_at_the_cap_like_done`<br>`test_rategate.py::test_release_that_touches_another_slots_claim_is_still_metered` |
| F8: `examples/demo.sh` inherited the user's git config; `commit.gpgsign=true` killed it at the first commit | `test_cli_notes_status.py::test_demo_ignores_the_users_global_git_config` |
| F9: `done` on a step another session actively claims said nothing about that claim | `test_close.py::test_done_on_a_step_another_session_claims_closes_but_warns`<br>`test_close.py::test_done_on_my_own_claim_or_an_unclaimed_step_does_not_warn_about_a_holder`<br>`test_close.py::test_a_released_foreign_claim_is_not_mentioned` |
| `doctor`'s swallowed-step scan ran one YAML parse per step-shaped notes line and a multiline `^\s*` regex per tail: a 40 KB task file stalled `doctor check` (and the pre-commit hook) for about 90 s | `test_doctor.py::test_doctor_absorbed_scan_is_not_quadratic_on_hostile_notes`<br>`test_doctor.py::test_doctor_still_finds_a_swallowed_step_after_decoy_lines` |
| the example guard's writer pattern was quadratic on a long command of repeated `sed`, long enough for a hook timeout to let the command through unchecked | `test_queue_guard.py::test_writer_pattern_is_not_quadratic_on_repeated_words` |

## `plan` invariants (docs/plan.md, CBX-CORE-001.c)

| # | Rule | Test(s) |
|---|---|---|
| P1 | ranking: unblocks (distinct descendants) descending, then priority, then ref; ties deterministic | `test_plan.py::test_ranking_unblocks_then_priority_then_ref`<br>`test_plan.py::test_unblocks_counts_distinct_descendants_once` |
| P2 | a task-ref dependency fans out to every open step of that task; a met or archived dependency adds no edge | `test_plan.py::test_task_ref_dependency_fans_out_to_every_open_step`<br>`test_plan.py::test_met_and_archived_dependencies_add_no_edge` |
| P3 | the critical path is the longest chain; ties go to the smaller ref sequence from the first step | `test_plan.py::test_critical_path_length_and_tiebreak`<br>`test_plan.py::test_critical_path_tiebreak_inside_a_fork` |
| P4 | waves: count equals the path length, no step depends on one in its own wave, `order` respects dependencies; rows grouped by target then priority then ref | `test_plan.py::test_waves_match_path_length_and_never_depend_inside`<br>`test_plan.py::test_wave_rows_are_grouped_by_target_then_priority_then_ref` |
| P5 | one readiness: `next` refs equal `ready`'s READY refs on the same machine (claimed, gated and other-machine steps out); `--machine` plans as that machine; path states name READY / GATED / CLAIMED / WAITING / elsewhere | `test_plan.py::test_next_is_exactly_what_ready_calls_ready`<br>`test_plan.py::test_machine_flag_uses_that_machines_capabilities`<br>`test_plan.py::test_path_states_name_every_kind` |
| P6 | a malformed step (or a step waiting on one, or in an unwritable file) appears only under `malformed`, exit 1, counts a floor | `test_plan.py::test_malformed_steps_appear_only_under_malformed`<br>`test_plan.py::test_a_file_no_writer_will_touch_is_malformed_and_so_are_its_dependents` |
| P7 | a cross-task cycle is named once from its smallest ref, its members are in no ranking, wave, order or path, its dependents are `blocked_by_cycle`, exit 1 | `test_plan.py::test_cross_task_cycle_is_named_once_and_excluded`<br>`test_plan.py::test_self_dependency_and_longer_cycles_start_at_the_smallest_ref` |
| P8 | NOT EVALUABLE (damaged claims file, gate probe with no answer, missing queue) exits 2 and wins over 1; an orphaned claim exits 1 | `test_plan.py::test_not_evaluable_wins_over_malformed`<br>`test_plan.py::test_a_gate_probe_with_no_answer_is_not_evaluable`<br>`test_plan.py::test_missing_queue_is_not_evaluable`<br>`test_plan.py::test_orphaned_claim_exits_1_and_is_listed` |
| P9 | byte-identical text and JSON across runs and across file creation order | `test_plan.py::test_output_is_byte_identical_and_independent_of_file_creation_order` |
| P10 | `--json` keys are exactly the contract; `--top` limits text sections, says what it left out, and does not touch JSON; an empty queue is an empty plan | `test_plan.py::test_json_schema_keys_are_exactly_the_contract`<br>`test_plan.py::test_top_limits_text_sections_and_says_what_it_left_out`<br>`test_plan.py::test_empty_queue_is_an_empty_plan_not_an_error` |
| P11 | `plan` writes nothing, is not metered by the rate gate, and does not take the writer lock | `test_plan.py::test_plan_is_read_only_and_not_metered`<br>`test_plan.py::test_plan_does_not_take_the_writer_lock`<br>`test_rategate.py::test_every_mutating_verb_is_metered_whatever_its_spelling` |
| P12 | 3,000 steps (chain and fan) plan in seconds, no recursion limit | `test_plan.py::test_3000_step_chain_and_fan_graphs_are_fast` |

## Views invariants (docs/views.md: `progress`, `near-done`, `graph`)

| # | Rule | Test(s) |
|---|---|---|
| V1 | every count prints beside its denominator; `closed` is done + skipped; percent rounds down, so only a finished task reads 100% | `test_views.py::test_progress_counts_sit_beside_their_denominators`<br>`test_views.py::test_percent_rounds_down_so_only_a_finished_task_reads_100`<br>`test_views.py::test_by_domain_and_priority_group_and_still_total` |
| V2 | a task with no steps is `0/0` and `—`, never 100% | `test_views.py::test_zero_step_task_is_zero_over_zero_never_a_hundred_percent` |
| V3 | a task file that does not parse, fails the shape check or shares its id is listed with its cause, left out of every total, makes the counts a floor and exits 1; a clean queue exits 0 and is not a floor | `test_views.py::test_unreadable_files_are_listed_and_make_the_counts_a_floor`<br>`test_views.py::test_a_clean_queue_exits_0_and_is_not_a_floor`<br>`test_views.py::test_a_task_id_held_by_two_files_is_excluded_not_double_counted`<br>`test_views.py::test_near_done_unreadable_file_is_listed_and_exits_1`<br>`test_graph.py::test_an_unreadable_task_file_is_named_and_exits_1_whatever_the_scope` |
| V4 | the scope is stated (queue only, or with `--archive`); `--archive` with no archive directory is NOT EVALUABLE, not zero archived tasks | `test_views.py::test_archive_flag_adds_archived_tasks_and_says_what_it_counted`<br>`test_views.py::test_archive_flag_with_a_missing_archive_dir_is_not_evaluable` |
| V5 | a missing queue is NOT EVALUABLE (exit 2), with `{"not_evaluable": ...}` under `--json` | `test_views.py::test_progress_missing_queue_is_not_evaluable`<br>`test_views.py::test_near_done_missing_queue_is_not_evaluable`<br>`test_graph.py::test_missing_queue_is_not_evaluable_in_every_format` |
| V6 | `--json` keys of `progress` and `near-done` are exactly the contract | `test_views.py::test_progress_json_keys_are_exactly_the_contract`<br>`test_views.py::test_near_done_json_keys_are_exactly_the_contract` |
| V7 | `near-done`: both thresholds inclusive and both must hold; complete and step-less tasks are not listed; order is open steps, percent descending, id; every state reads in words; a step no configured machine provides says `workable on: none` and is counted; target `any` is every machine | `test_views.py::test_near_done_lists_a_task_with_its_remaining_step_state_and_machines`<br>`test_views.py::test_near_done_every_state_reads_in_words`<br>`test_views.py::test_thresholds_are_inclusive_and_both_must_hold`<br>`test_views.py::test_order_is_open_steps_then_percent_descending_then_id`<br>`test_views.py::test_complete_and_stepless_tasks_are_not_near_done`<br>`test_views.py::test_a_step_no_configured_machine_provides_says_none_and_is_counted`<br>`test_views.py::test_target_any_is_workable_on_every_machine` |
| V8 | `near-done` with a damaged claims file or a gate probe with no answer is NOT EVALUABLE and its steps say so; an unparseable sibling claims file does the same for `near-done` and `graph` | `test_views.py::test_near_done_with_a_damaged_claims_file_is_not_evaluable`<br>`test_views.py::test_near_done_gate_probe_with_no_answer_is_not_evaluable`<br>`test_view_completeness.py::test_an_unparseable_sibling_claims_file_makes_every_consumer_not_evaluable` |
| V9 | byte-identical text and JSON across file creation order | `test_views.py::test_output_is_byte_identical_whatever_the_file_creation_order` |
| V10 | the three views write nothing, are not metered by the rate gate, take no lock; a bad argument is usage (64), never NOT EVALUABLE | `test_views.py::test_views_are_read_only_unmetered_and_take_no_lock`<br>`test_views.py::test_bad_arguments_are_usage_errors_not_not_evaluable`<br>`test_rategate.py::test_every_mutating_verb_is_metered_whatever_its_spelling` |
| V11 | the text views print no control, escape or bidirectional character from a title | `test_views.py::test_hostile_titles_cannot_inject_terminal_escapes` |
| V12 | `graph` names every state (done, skipped, ready, gated, claimed, waiting, elsewhere, malformed, cycle, not_evaluable) and `--machine` changes states, not shape | `test_graph.py::test_every_node_state_is_named_and_classed`<br>`test_graph.py::test_machine_flag_changes_the_states_not_the_shape` |
| V13 | edges run dependency to dependent and a task-ref dependency fans out; with `--open` they are exactly the edges `plan` resolves | `test_graph.py::test_edges_run_from_dependency_to_dependent_and_fan_out_for_task_refs`<br>`test_graph.py::test_open_flag_draws_exactly_the_edges_plan_resolves` |
| V14 | the critical path is `plan`'s, and only its edges are thick | `test_graph.py::test_critical_path_edges_are_thick_and_match_plan` |
| V15 | a title or task id is cleaned, cut to 60 characters and escaped; every output line matches a fixed shape, so nothing from a file opens a directive, a click, a tag or a new line (hostile list: quote and bracket breakouts, `</script>`, `%%{init}`, backticks, `\|`, CR/LF/NUL/control characters, 10 KB, U+202E) | `test_graph.py::test_hostile_titles_stay_inside_their_label_in_every_format`<br>`test_graph.py::test_mermaid_escapes_the_characters_that_mean_something`<br>`test_graph.py::test_a_hostile_task_id_and_task_title_get_the_same_escaping`<br>`test_graph.py::test_a_title_is_cut_to_60_characters_with_an_ellipsis` |
| V16 | node ids are generated in sorted ref order, never taken from a file | `test_graph.py::test_node_ids_are_generated_in_sorted_ref_order` |
| V17 | over `--max-nodes` the export refuses (exit 1), names the count, prints no graph, drops nothing | `test_graph.py::test_over_max_nodes_refuses_names_the_count_and_prints_no_graph` |
| V18 | `--task` draws the task plus its direct dependencies as context; an unknown or archived task is usage; an empty selection says so | `test_graph.py::test_task_flag_draws_the_task_and_its_dependencies_as_context`<br>`test_graph.py::test_unknown_or_archived_task_is_a_usage_error`<br>`test_graph.py::test_an_empty_selection_is_an_empty_graph_that_says_so` |
| V19 | malformed and cyclic steps are drawn as such and exit 1, only when drawn (unreadable files always); a clean queue exits 0 with nothing on stderr | `test_graph.py::test_malformed_steps_are_drawn_malformed_and_exit_1`<br>`test_graph.py::test_cycle_steps_are_drawn_with_the_cycle_class_and_exit_1`<br>`test_graph.py::test_a_malformed_step_outside_the_drawn_task_does_not_fail_the_drawing`<br>`test_graph.py::test_a_clean_queue_exits_0_with_nothing_on_stderr` |
| V20 | `graph` NOT EVALUABLE: a damaged claims file makes `ready`-derived states `not_evaluable` (structural ones stay), a gate probe with no answer exits 2, and 2 wins over a size refusal | `test_graph.py::test_damaged_claims_is_not_evaluable_and_ready_derived_states_say_so`<br>`test_graph.py::test_a_gate_probe_with_no_answer_is_not_evaluable`<br>`test_graph.py::test_graph_not_evaluable_wins_over_a_size_refusal` |
| V21 | `graph --format json` keys are exactly the contract | `test_graph.py::test_json_keys_are_exactly_the_contract` |
| V22 | byte-identical across file creation order; 3,000 steps render in seconds in all three formats | `test_graph.py::test_output_is_byte_identical_whatever_the_file_creation_order`<br>`test_graph.py::test_3000_steps_render_in_seconds_with_a_raised_cap` |
| V23 | the Mermaid graph in the walkthrough is what `crossbox graph` prints for `examples/sample-queue/` as the laptop; the README links it and stays within 124 lines | `test_graph.py::test_the_walkthroughs_graph_is_what_crossbox_graph_prints`<br>`test_graph.py::test_the_readme_points_at_the_graph_and_the_views` |

## Sixth-review findings

| Finding | Test(s) |
|---|---|
| F1 (0.1 part): the example SessionStart hook ran gate probes in every repository it was installed in; it now passes `--no-probes` | `test_needs_ready.py::test_ready_no_probes_does_not_run_the_probe`<br>`test_plugin.py::test_session_start_passes_no_probes_and_the_root_unless_probes_are_allowed` |
| F3: writers accepted text with a lone surrogate (YAML escapes it, so the file and `doctor check` looked fine) and every reader then died with a traceback and exit 1; writers refuse it, `doctor check` and `ready` flag a hand-edited file, and no printer can raise on it | `test_absent_and_malformed_inputs.py::test_add_refuses_text_that_cannot_be_encoded`<br>`test_absent_and_malformed_inputs.py::test_new_and_note_refuse_text_that_cannot_be_encoded`<br>`test_absent_and_malformed_inputs.py::test_edit_step_refuses_text_that_cannot_be_encoded`<br>`test_absent_and_malformed_inputs.py::test_a_hand_edited_unencodable_title_is_found_not_clean`<br>`test_absent_and_malformed_inputs.py::test_every_printer_survives_an_unencodable_title` |
| F6 (0.1 part): a task id defined in two queue files listed both files' steps READY and `ready` exited 0 | `test_needs_ready.py::test_ready_flags_a_task_id_defined_in_two_queue_files`<br>`test_needs_ready.py::test_distinct_task_ids_are_not_flagged` |
| F7: a task-ref dependency on an archived task whose file does not parse read MET | `test_close.py::test_a_task_ref_to_an_unparseable_archived_task_is_not_met`<br>`test_close.py::test_a_task_ref_to_a_good_archived_task_is_still_met` |
| F8: `$`-anchored id patterns accepted a trailing newline (a step id `"a\n"` passed every check and could never be closed); every id shape is matched with `fullmatch` | `test_ids_and_guard.py::test_a_step_id_with_a_trailing_newline_is_malformed`<br>`test_ids_and_guard.py::test_id_grammar_rejects_a_trailing_newline`<br>`test_ids_and_guard.py::test_doctor_check_flags_a_step_id_with_a_trailing_newline` |
| F9 (0.1 part): `ready` and `status` printed titles and ids raw (a newline forged a row, ESC reached the terminal) | `test_absent_and_malformed_inputs.py::test_ready_and_status_do_not_let_a_title_forge_a_row`<br>`test_absent_and_malformed_inputs.py::test_ready_and_status_do_not_pass_escape_bytes`<br>`test_absent_and_malformed_inputs.py::test_clean_keeps_ordinary_text_and_drops_controls` |
| F1 (plugin): the session hook, status line and MCP read tools ran probes; `plan`, `near-done` and `graph` had no `--no-probes` | `test_views.py::test_the_views_run_the_probe_unless_told_not_to`<br>`test_views.py::test_no_probes_leaves_an_unprobed_queue_alone`<br>`test_plugin.py::test_session_start_passes_no_probes_and_the_root_unless_probes_are_allowed`<br>`test_plugin.py::test_status_line_passes_no_probes_unless_allowed`<br>`test_plugin.py::test_mcp_read_tools_pass_no_probes_unless_allowed` |
| F2: a lone surrogate killed the MCP server | `test_plugin.py::test_a_lone_surrogate_is_refused_and_the_server_keeps_serving`<br>`test_plugin.py::test_an_exception_in_handling_is_an_internal_error_not_a_dead_server` |
| F4: `graph` counted nodes only (3,600 edges exited 0) | `test_graph.py::test_graph_refuses_more_edges_than_mermaid_accepts`<br>`test_graph.py::test_graph_edge_and_char_limits_are_flags_and_other_formats_have_none`<br>`test_graph.py::test_graph_refuses_more_characters_than_mermaid_accepts`<br>`test_graph.py::test_a_size_refusal_does_not_hide_not_evaluable` |
| F5: the session hook read the outer repository's queue | `test_plugin.py::test_session_start_reads_the_inner_queue_of_a_subdirectory_project` |
| F6 (plan): a duplicated or malformed ref was ranked NEXT, twice | `test_plan.py::test_plan_never_ranks_a_task_id_defined_in_two_files`<br>`test_plan.py::test_plan_lists_a_step_once` |
| F9 (plan, near-done): raw titles, ids and machine names | `test_plan.py::test_plan_cleans_titles_and_ids`<br>`test_views.py::test_near_done_cleans_machine_names` |
| F10, F11: queue-guard missed 9 writes and acted outside a `.crossbox/` project | `test_queue_guard.py::test_guard_denies_writes_under_the_crossbox_dir`<br>`test_queue_guard.py::test_guard_never_blocks_reads_or_unrelated_writes`<br>`test_queue_guard.py::test_the_guard_denies_relative_writes_when_the_session_is_inside_the_directory`<br>`test_queue_guard.py::test_the_guard_is_silent_in_a_project_without_the_directory`<br>`test_queue_guard.py::test_the_added_patterns_are_not_quadratic` |
| F12: status cache pinned by a future mtime; keyed by git dir only | `test_plugin.py::test_a_cache_dated_in_the_future_is_not_used`<br>`test_plugin.py::test_the_cache_is_keyed_by_root_and_machine` |
| F13: exit 64/3, signals and tracebacks read as answers | `test_plugin.py::test_other_exit_codes_are_tool_errors_with_the_output`<br>`test_plugin.py::test_a_signal_death_and_a_traceback_are_tool_errors` |
| F14: a timeout left probe children running | `test_plugin.py::test_mcp_timeout_kills_the_grandchildren_too`<br>`test_plugin.py::test_status_timeout_kills_the_grandchildren_too` |
| F15: MCP id regex narrower than the grammar | `test_plugin.py::test_ids_the_cli_accepts_are_accepted`<br>`test_plugin.py::test_ids_that_are_not_ids_are_still_refused` |
| F16: `--machine ""` ignored; wrong source named | `test_views.py::test_an_empty_machine_flag_is_a_usage_error`<br>`test_views.py::test_a_bad_machine_flag_names_the_flag_not_the_environment`<br>`test_views.py::test_a_bad_environment_machine_names_the_environment`<br>`test_plan.py::test_a_good_machine_flag_still_plans_as_that_machine` |
| F17: `plan` was quadratic (a search per READY step) | `test_plan.py::test_descendant_counts_match_a_search_on_random_graphs_with_cycles`<br>`test_plan.py::test_plan_counts_unblocks_once_not_per_ready_step`<br>`test_plan.py::test_6000_steps_of_ready_roots_feeding_a_chain_plan_in_seconds` |
| F18: `progress --json` domain/priority groups lacked `title`/`archived` | `test_views.py::test_progress_json_groups_all_carry_the_documented_keys` |
| F19: status line blamed a skipped task file for any exit 1 | `test_plugin.py::test_exit_1_wording_comes_from_the_json` |
| F20: `plan` ranked READY over an incomplete claims view | `test_plan.py::test_plan_with_an_incomplete_claims_view_ranks_nothing` |

## Findings of the seventh review

The seventh review checked the 0.2 verbs `closures` and `lint` for false results, in
both directions. Each finding is pinned by a test; the tests carry the tag `R7`.

| Finding | Test(s) |
|---|---|
| a commit with a skewed date (a wrong clock, a rebase) must not hide recent closing commits from `closures` | `test_closures.py::test_a_commit_with_an_older_date_does_not_hide_recent_closers` |
| in a shallow clone a recorded commit that was not fetched cannot be judged: the class is NOT EVALUABLE (exit 2) and names the shallow clone as the cause | `test_closures.py::test_a_shallow_clone_cannot_judge_a_recorded_commit_it_does_not_have` |
| an unreadable task file is named by `lint` and `closures`, and the run exits 2, never clean | `test_closures.py::test_an_unreadable_task_file_is_named_and_the_run_is_not_clean` |
| a bare file name cited in a done step's notes is present when any tracked file has that name | `test_closures.py::test_a_bare_file_name_is_present_when_any_tracked_file_has_that_name` |
| `lint` does not flag things that are not gates or decisions: a gate on a step already closed, a sentence saying a gate is stale, "deferred" used as an adverb, a decision word inside a sentence | `test_prose_lint.py::test_a_gate_on_a_step_already_closed_is_not_drift`<br>`test_prose_lint.py::test_a_sentence_saying_a_gate_is_stale_is_not_a_gate`<br>`test_prose_lint.py::test_deferred_with_an_adverb_from_elsewhere_is_moved_work`<br>`test_prose_lint.py::test_a_decision_marker_counts_only_at_the_start_of_a_line` |
| `lint` reads the decision-marker forms a real queue uses (`DEFER until`, `BLOCKED ON OPERATOR`) and leaves near-misses alone | `test_prose_lint.py::test_the_marker_forms_a_real_queue_uses_are_read`<br>`test_prose_lint.py::test_their_near_misses_are_not` |
| the README's MCP tool count matches the tools the server has | `test_plugin.py::test_the_readme_counts_the_mcp_tools_it_has` |
