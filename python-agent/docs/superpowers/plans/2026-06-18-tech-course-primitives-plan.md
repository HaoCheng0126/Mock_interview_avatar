# Tech Course Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace generic interactions in two 7-8 courses with dedicated technology-style primitives.

**Architecture:** Add two focused whiteboard renderers: `bio_builder_lab` for a gene-engineering lab scene and `sky_signal_rescue` for a satellite communication command scene. Each renderer uses `scene.props.mode` for three course moments and keeps all styles scoped under unique class prefixes.

**Tech Stack:** Plain browser JavaScript modules, DOM APIs, CSS, YAML, pytest static checks.

## Global Constraints

- Keep each course title, persona, classmates, and three-chapter arc.
- Make the visual language clearly technological: HUD panels, scanning lines, glowing chips, satellite links, command-console UI.
- Keep interactions understandable for 7-8 year old children.
- Avoid changing existing generic primitives.
- Do not add backend component types or new dependencies.

---

### Task 1: Add Failing Static Tests

**Files:**
- Modify: `tests/teaching/test_teaching_frontend.py`

**Interfaces:**
- Produces tests requiring `bio_builder_lab`, `sky_signal_rescue`, their JS modules, CSS classes, and course YAML usage.

- [ ] Add course path constants for the two YAML files.
- [ ] Add helper readers for the two course files.
- [ ] Add `test_bio_builder_lab_primitive_is_registered_and_course_uses_it`.
- [ ] Add `test_sky_signal_rescue_primitive_is_registered_and_course_uses_it`.
- [ ] Run the two tests and confirm they fail before implementation.

### Task 2: Implement `bio_builder_lab`

**Files:**
- Create: `../frontend/teaching/whiteboard/bio-builder-lab.js`
- Modify: `../frontend/teaching/whiteboard/index.js`
- Modify: `../frontend/teaching/whiteboard/styles.css`

**Interfaces:**
- Exports `renderBioBuilderLab(scene)`.
- Supports modes `module`, `combine`, `rescue`.

- [ ] Create renderer with a kid scientist, gene chips A/B, scanner pod, and elephant pod.
- [ ] Register `bio_builder_lab`.
- [ ] Add `.bio-lab-*` styles.
- [ ] Run the bio static test.

### Task 3: Implement `sky_signal_rescue`

**Files:**
- Create: `../frontend/teaching/whiteboard/sky-signal-rescue.js`
- Modify: `../frontend/teaching/whiteboard/index.js`
- Modify: `../frontend/teaching/whiteboard/styles.css`

**Interfaces:**
- Exports `renderSkySignalRescue(scene)`.
- Supports modes `blocked`, `relay`, `mesh`.

- [ ] Create renderer with detective console, mountains, ground stations, satellites, and signal beams.
- [ ] Register `sky_signal_rescue`.
- [ ] Add `.sky-signal-*` styles.
- [ ] Run the sky static test.

### Task 4: Rewrite Course YAML Interactions

**Files:**
- Modify: `config/courses/疯狂的合成生物拼图小象_7-8.yaml`
- Modify: `config/courses/小侦探和天空桥梁信号大救援_7-8.yaml`

**Interfaces:**
- All experiences in the bio course use `bio_builder_lab`.
- All experiences in the sky course use `sky_signal_rescue`.

- [ ] Replace bio course experiences with modes `module`, `combine`, `rescue`.
- [ ] Replace sky course experiences with modes `blocked`, `relay`, `mesh`.
- [ ] Keep quiz and interaction structures valid, correcting obviously mismatched quiz copy where needed.
- [ ] Run static tests and YAML-loading tests.

### Task 5: Verify

**Files:**
- Verify all changed files.

- [ ] Run `pytest tests/teaching/test_teaching_frontend.py -v`.
- [ ] Run `PYTHONPATH=. pytest tests/teaching/test_course_manager.py -v`.
- [ ] Run targeted `CourseManager` load checks for both edited YAMLs.
