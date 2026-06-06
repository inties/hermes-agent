# Background Review Changelog

## 2026-06-01 - Skill review conservative write policy

### Background

Hermes runs a background review fork after normal assistant responses. The fork can review the current conversation snapshot and update long-term state through memory and skill tools.

Relevant write targets:

- `memories/USER.md`: user profile, preferences, communication style.
- `memories/MEMORY.md`: durable environment facts, project conventions, tool experience.
- `skills/**/SKILL.md` and `skills/**/references/*`: reusable procedures for a recurring class of tasks.

### Reason For Change

The previous skill review prompt was too aggressive. It encouraged the review agent to update skills in most sessions and treated doing nothing as a missed learning opportunity.

Risky parts of the old behavior:

- It explicitly asked the review agent to `Be ACTIVE`.
- It said most sessions should produce at least one skill update.
- It allowed `references/` to hold `session-specific detail`.
- It made user preferences, private scenes, detailed interaction history, and one-off project state too easy to write into skills.

This caused some skills to drift from reusable operating procedure into session archive. `intimacy-talk` was the clearest symptom: detailed scenarios and cases accumulated in `SKILL.md`, increasing prompt size and making future behavior overfit old conversations.

### What Was Worth Preserving

The old prompt had useful design ideas that should not be thrown away:

- It treated user corrections as important learning signals, especially corrections about style, tone, format, workflow, and repeated frustration.
- It tried to update the skill that was actually used in the session before creating a new one.
- It preferred existing umbrella skills over narrow one-session skills.
- It had a useful support-file taxonomy: `references/`, `templates/`, and `scripts/`.
- It understood that reusable debugging paths, workarounds, and provider quirks can be valuable future context.
- It protected bundled, hub-installed, and pinned skills from automatic edits.
- It warned against preserving transient setup failures as permanent tool limitations.
- It surfaced overlapping skills instead of silently merging or duplicating them.

The new policy keeps these strengths, but changes the default from active capture to conservative capture.

### Design Intent

The new boundary is:

- `SKILL.md` should contain general rules, workflows, pitfalls, and decision criteria.
- `references/` may contain compact reusable support material, such as reproduction steps, version matrices, provider quirks, or API excerpts.
- `references/` must not become a chat transcript, private-scene archive, full task narrative, or one-off project status log.
- `USER.md` is for user profile, personal preferences, relationship preferences, and communication style.
- `MEMORY.md` is for durable facts, project conventions, and tool or environment experience.
- Full process logs, round-by-round narratives, and stage records belong in session logs or project docs such as debug/progress documents, not in skills.

### Evidence Migration Policy

Old detailed conversations may still contain evidence that is worth abstracting. The rule is to migrate evidence in two steps instead of preserving it directly:

1. Extract the reusable rule, pitfall, workflow step, or decision criterion.
2. Keep only the smallest supporting evidence needed to make that rule credible and recoverable.

Allowed destinations:

- Put the abstract rule in `SKILL.md`.
- Put compact reusable evidence in `references/`, such as a recurring repro recipe, provider quirk, version matrix, API excerpt, or short anonymized before/after example.
- Put user identity, taste, relationship preference, and communication preference in `USER.md`.
- Put project or environment facts in `MEMORY.md` or project docs.
- Leave full dialogue, private scenes, and round-by-round narratives in session logs only.

For old content that seems valuable but is not ready to generalize, create a manual review note or issue instead of adding it to skill context. It should not be auto-loaded into future prompts until it has been compressed into a reusable rule.

This means future automatic reviews will be less detailed by design. The intended replacement for lost detail is not larger skills, but better manual migration of rare high-value evidence.

### Code Changes

Changed files:

- `agent/background_review.py`
- `tools/skill_manager_tool.py`
- `agent/curator.py`
- `tests/run_agent/test_review_prompt_class_first.py`

Behavioral changes:

- Skill review changed from `Be ACTIVE` to `Be CONSERVATIVE`.
- Ordinary smooth sessions should usually end with `Nothing to save.`
- A low-value or non-reusable skill update is now treated as worse than no update.
- Skill writes are limited to durable, reusable lessons that apply beyond the exact current conversation.
- Chat logs, private scenes, round-by-round narratives, and one-off project state are explicitly forbidden in skill/reference writes.
- User preference routing is clarified:
  - personal, relationship, and current-life facts go to memory/user profile or nowhere;
  - only generalized task-behavior preferences may become skill guidance.
- `references/` is now described as reusable support material, not a place for session-specific detail.
- `skill_manage` tool descriptions were tightened so that "complex task succeeded" or "errors were overcome" does not automatically imply that a skill should be created.
- Curator prompts were tightened so narrow skills are not preserved as large scenario archives under `references/`.

### Verification

Commands run:

```text
python -m py_compile agent\background_review.py tools\skill_manager_tool.py agent\curator.py
python -m pytest --timeout-method=thread tests\run_agent\test_review_prompt_class_first.py
```

Result:

- Syntax checks passed.
- `tests/run_agent/test_review_prompt_class_first.py` passed with 19 tests.

### Follow-Up Guardrails

If background review still writes scene content into skills, add code-level guardrails:

- Limit the maximum write size for background-review `skill_manage` calls.
- Add stricter protections for high-risk personal/relationship skills.
- Require each new `references/*.md` file to be linked from `SKILL.md` with a short "when to read this" pointer.
- Reject writes that contain obvious transcript structure, private scene narration, or one-off project status.
