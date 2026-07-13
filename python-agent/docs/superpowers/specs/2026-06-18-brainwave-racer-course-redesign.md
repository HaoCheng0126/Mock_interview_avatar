# Brainwave Racer Course Redesign

## Goal

Redesign the course `小AI的脑波赛车大冒险_7-8` so its interactive content matches the story: a child-friendly brainwave racing adventure. The course should no longer rely on generic primitives such as bar charts, sorting, state-machine river logic, or animal dragging.

## Assumptions

- Keep the existing course title, audience, teacher persona, and three-chapter story arc.
- Keep the content understandable for 7-8 year old children.
- Use one dedicated primitive, `brainwave_racer`, with multiple modes instead of several unrelated primitives.
- Avoid changing behavior for other courses.

## Scope

Implement a new frontend primitive:

- `brainwave_racer`
- File: `frontend/teaching/whiteboard/brainwave-racer.js`
- Registered in `frontend/teaching/whiteboard/index.js`
- Styled in `frontend/teaching/whiteboard/styles.css`

Update the course file:

- `python-agent/config/courses/小AI的脑波赛车大冒险_7-8.yaml`

Add static checks in the teaching frontend tests to verify:

- the new primitive is registered
- the course uses `brainwave_racer`
- the old mismatched primitives are removed from this course

## Primitive Design

`brainwave_racer` renders a compact sci-fi classroom scene. It uses the existing whiteboard runtime contract:

- input: `scene.props`
- output: DOM nodes returned by the renderer
- no backend protocol changes

The primitive supports three modes.

### `scan`

Purpose: show how the brainwave helmet reads focus.

Visuals:

- helmet panel
- animated or stepped brainwave line
- focus meter
- signal quality chips

Interactions:

- "提升专注" raises focus and stabilizes the wave
- "切换状态" cycles between distracted, normal, and focused readings

### `signal`

Purpose: show how brainwave data becomes a race command.

Visuals:

- pipeline from brainwave input to AI recognition to command output to car response
- highlighted current pipeline stage
- status readout such as "识别中", "加速指令", "保持直线"

Interactions:

- "发送信号" advances the pipeline
- "查看指令" highlights the final command

### `race`

Purpose: show focus controlling the racer during the final challenge.

Visuals:

- neon-style race track
- racer position
- brainwave energy bar
- distraction indicators

Interactions:

- "提升专注" increases energy and moves the car forward
- "排除干扰" clears distraction state
- "冲刺" completes the race when focus is high enough

## Course Content Changes

Chapter 1 keeps the "发现魔法头盔" story, but each step maps to helmet scanning:

- discover the helmet
- compare distracted and focused waves
- connect stable focus to racer readiness

Chapter 2 keeps "解密专注信号", but replaces generic state-machine language with a brainwave control pipeline:

- brainwave is collected
- AI recognizes the pattern
- the racer receives commands

Chapter 3 keeps "冲过终点线", but becomes the final race scene:

- maintain focus to accelerate
- remove distractions
- sprint across the finish line

The quiz should be corrected so "focused" means a stable, clear signal rather than a mismatched description.

## Non-Goals

- Do not redesign the full teaching UI.
- Do not modify unrelated primitives.
- Do not add new backend component types.
- Do not introduce a new build system or dependency.

## Verification

Run focused tests:

- `pytest tests/teaching/test_teaching_frontend.py`

If a local browser check is practical, open the teaching page and confirm the scene is visually non-generic:

- brainwave curve is visible
- racer track is visible
- signal pipeline is visible
- interactions update the scene

## Risks

- The frontend directory is outside `python-agent`, so edits may require filesystem approval in this environment.
- Static tests can verify registration and course references, but visual quality still needs a human/browser check.
