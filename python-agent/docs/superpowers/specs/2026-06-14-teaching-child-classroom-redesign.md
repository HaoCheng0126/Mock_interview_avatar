# Teaching Digital Human — Child Classroom Redesign

**Date:** 2026-06-14
**Status:** Approved direction
**Scope:** Teaching digital human redesign for a 7-10 year old child

## Goal

Build a version that a 7-10 year old child can actually use comfortably:

- The child always knows the current task.
- The teacher remains present as a companion and guide.
- The whiteboard, quiz, and feedback are large enough to read and act on.
- AI classmate Xiao Ming participates as a peer who asks, guesses, and can be corrected.
- Microphone capture is explicit, controlled, and predictable.

This is a classroom experience, not a video call with a side panel.

## Product Direction

The selected UI direction is **Teacher + Whiteboard Dual Focus**.

The left side keeps Xiao Si Teacher visible at all times. The right side is the classroom workspace. The workspace switches by lesson state:

- Whiteboard explanation
- Teacher interaction question
- Quiz
- Quiz result and explanation

Quiz content takes over the right-side whiteboard area. It does not appear as a small sidebar card and does not become a full-screen takeover. This keeps the teacher present while making the task visually dominant.

## Audience

Target age is **7-10**. The experience should not feel like preschool UI.

Design implications:

- Use large, readable type, but avoid overly babyish visuals.
- Use clear task framing: listen, find, explain.
- Let the child reason and correct Xiao Ming.
- Keep choices focused and visible.
- Use encouragement, but make explanations meaningful.

## Core Layout

### Left: Teacher Area

The teacher area contains:

- Live Avatar video
- Connection state
- Primary microphone-related action when relevant

The teacher area should feel stable. It should not resize or disappear when quiz or interaction states change.

### Right: Classroom Workspace

The workspace is the main learning surface.

In lecture state, it shows:

- Current chapter title
- Task step indicator, for example: `1 听故事`, `2 找陷阱`, `3 解释原因`
- Whiteboard steps
- Current knowledge point

In quiz state, it shows:

- Quiz title
- Question
- Large answer choices
- Optional Xiao Ming guess or reasoning
- Submit state

In quiz result state, it shows:

- Correct / almost-correct feedback
- Correct answer when needed
- Short explanation of why
- Continue affordance or automatic continuation after a readable delay

### Bottom Interaction Strip

The bottom strip is for live classroom participation, not a full chat transcript.

It shows:

- Xiao Ming's latest question, guess, or answer
- Teacher prompt when waiting for the child
- The current main action: `我来回答`, `说完了`, `举手提问`, `提交答案`

A transcript can remain available as a secondary log, but it should not be the main teaching surface.

## Microphone Policy

The app must explicitly control microphone state.

### Rules

- Do not automatically open the microphone when the teacher asks a question.
- Open the microphone only after the child clicks a clear action such as `我来回答` or `举手提问`.
- Close the microphone after ASR final, after `说完了`, after timeout, or when the child cancels.
- Disable or hide microphone actions during quiz answer selection unless a voice answer mode is explicitly added later.
- Reflect microphone state clearly in the UI.

### Rationale

Automatic microphone capture is not appropriate for this use case:

- Browser permission policies often require a user gesture.
- It can capture background speech or parent-child side conversation.
- It increases ASR echo and false-trigger risk.
- It makes the child feel less in control.

The intended flow is:

```
Teacher asks question
  -> UI shows question and "我来回答"
  -> Child clicks
  -> microphone opens
  -> child speaks
  -> ASR final or timeout
  -> microphone closes
  -> teacher feedback
```

If the child does not click within a short window, Xiao Ming can answer first as a demonstration. The child can still click `我来回答` afterward.

## Xiao Ming Behavior

Xiao Ming should no longer rely on an uncoordinated random interjection.

He should participate as a peer:

- Each chapter should include at least one Xiao Ming moment.
- After a knowledge point, he may ask a short question or express confusion.
- During interaction timeout, he should offer a sample answer.
- During quiz, he should make a visible guess before or alongside the child.
- He should sometimes be wrong, so the child can correct him.
- His speech should be 1-2 short sentences.

Xiao Ming's role is to create peer reasoning, not just atmosphere.

## Teaching Flow

### Lecture

1. Teacher introduces a knowledge point.
2. Right workspace shows the matching whiteboard step.
3. Xiao Ming may ask or guess when useful.
4. Manager decides whether to continue, ask a check question, let Xiao Ming speak, or re-explain.

### Teacher Interaction Question

1. Teacher asks a question.
2. Workspace switches to an interaction prompt.
3. UI shows `我来回答`.
4. If the child clicks, microphone opens and captures one response.
5. If the child does not respond in time, Xiao Ming answers first.
6. Teacher gives feedback and returns to lecture.

### Quiz

1. Workspace switches to quiz board.
2. Xiao Ming may show a guess or thought.
3. Child selects an option.
4. UI submits the answer.
5. Workspace switches to quiz result.
6. Teacher explains why.
7. Continue to next chapter after a readable delay.

## Backend Changes

### TeachingController

Needed changes:

- Keep and expose a recent component stream, not only latest `whiteboard` and `play_audio`.
- Represent `interaction_prompt`, `quiz`, `quiz_result`, `encouragement`, `raise_hand`, and Xiao Ming messages consistently.
- Do not immediately leave `WAITING_INTERACT`; wait for a child response, Xiao Ming demonstration, or timeout.
- Enter a visible `QUIZ_RESULT` state before returning to lecture.
- Track microphone intent separately from teaching state.

### ManagerAgent

Needed changes:

- Actually handle all decision actions in the controller.
- Add deterministic guardrails so Xiao Ming is not silent for an entire chapter.
- Prefer predictable pacing over pure randomness.

### ClassmateEngine

Needed changes:

- Provide methods for:
  - chapter/knowledge-point interjection
  - interaction timeout answer
  - quiz guess
  - post-QA follow-up
- Return structured messages with `speaker`, `text`, `kind`, and optional audio URL.

### TeachingListener / ASR

Needed changes:

- Accept speech only when microphone capture was explicitly requested.
- Close capture after ASR final or timeout.
- Keep raise-hand Q&A separate from teacher-initiated interaction answer.

## Frontend Changes

`frontend/teaching.html` should be redesigned around:

- Stable two-column classroom layout.
- Large right-side workspace.
- State-based rendering for whiteboard, interaction, quiz, and result.
- Bottom interaction strip for Xiao Ming and current action.
- Explicit microphone states:
  - closed
  - opening
  - listening
  - processing
  - timed out
- Large, readable quiz options.
- Visible quiz result and explanation.
- Encouragement animation that does not block reading.

The existing narrow right panel and chat-first layout should be replaced.

## Non-Goals

- Long-term learning record.
- Parent dashboard.
- Multi-child classroom.
- Full course authoring UI.
- Auto-opening microphone.
- Full-screen quiz takeover.

## Success Criteria

- A 7-10 year old can identify the current task without reading a transcript.
- Quiz appears as the primary right-side workspace, not a small sidebar block.
- Quiz result and explanation are visibly rendered.
- Xiao Ming participates at least once per chapter in normal flow.
- Teacher interaction questions do not auto-open microphone.
- Microphone opens only after explicit child action and closes after response or timeout.
- The app remains usable if Xiao Ming TTS fails; his text still appears in the interaction strip.
