# Teaching Child Classroom Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 7-10 year old friendly teaching classroom where the teacher and whiteboard are dual focus, quizzes take over the right workspace, Xiao Ming participates predictably, and microphone capture is explicit.

**Architecture:** Keep the existing `teaching/` package boundaries. Extend `TeachingController` to expose a proper component stream and visible quiz/result/interaction states, tighten `TeachingListener` ASR gating for explicit microphone flows, make `ManagerAgent` and `ClassmateEngine` predictable enough for children, and replace `frontend/teaching.html` with a state-driven classroom UI.

**Tech Stack:** Python 3.11+, aiohttp, pytest, existing Live Avatar SDK, vanilla HTML/CSS/JS frontend.

---

## File Structure

- Modify: `teaching/course_component.py`
  - Add structured classmate/microphone-oriented component types if needed.
- Modify: `teaching/teaching_controller.py`
  - Own classroom state, component stream, quiz result visibility, interaction wait, Xiao Ming component emission.
- Modify: `teaching/manager_agent.py`
  - Replace mostly-random decisions with deterministic guardrails plus bounded randomness.
- Modify: `teaching/classmate_engine.py`
  - Return structured classroom messages for interjection, interaction answer, and quiz guess.
- Modify: `teaching/teaching_agent.py`
  - Preserve interaction state during ASR start, add explicit microphone/session intent handling where needed.
- Modify: `tests/teaching/test_teaching_controller.py`
  - Add tests for component stream, quiz result, interaction wait, and classmate messages.
- Create or modify: `tests/teaching/test_manager_agent.py`
  - Test deterministic decision guardrails.
- Create or modify: `tests/teaching/test_classmate_engine.py`
  - Test structured classmate message behavior using fake persona/LLM/TTS.
- Modify: `../frontend/teaching.html`
  - Redesign classroom UI. This file is outside the current writable root and will require permission during execution.

## Task 1: Component Stream and Visible Quiz Result

**Files:**
- Modify: `teaching/teaching_controller.py`
- Modify: `tests/teaching/test_teaching_controller.py`

- [ ] **Step 1: Add failing tests for generic component delivery**

Append these tests to `tests/teaching/test_teaching_controller.py`:

```python
@pytest.mark.asyncio
async def test_status_exposes_recent_components(controller):
    ctrl, _, _, _ = controller
    await ctrl._send_component("interaction_prompt", "show", {
        "text": "你会怎么做？",
        "chapter_id": "ch1",
    })
    await ctrl._send_component("encouragement", "show", {
        "text": "太棒了！",
        "style": "star",
    })

    status = ctrl.get_status()

    assert "components" in status
    assert status["components"][-2]["type"] == "interaction_prompt"
    assert status["components"][-1]["type"] == "encouragement"
    assert status["componentSeq"] == status["components"][-1]["seq"]


@pytest.mark.asyncio
async def test_quiz_result_state_is_visible_before_lecturing(controller):
    ctrl, agent, cm, _ = controller
    chapter = cm.get_chapter("ch2")
    ctrl._state = TeachingState.QUIZZING
    ctrl._current_chapter_id = "ch2"

    task = asyncio.create_task(ctrl._handle_quiz(chapter))
    await asyncio.sleep(0.01)
    ctrl.answer_quiz("ch2", "A")
    await asyncio.sleep(0.05)

    assert any(e["event"] == "quiz_result" for e in agent.custom_events)
    assert any(e["event"] == "encouragement" for e in agent.custom_events)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/teaching/test_teaching_controller.py::test_status_exposes_recent_components tests/teaching/test_teaching_controller.py::test_quiz_result_state_is_visible_before_lecturing -v
```

Expected:

- First test fails because `get_status()` does not expose `components`.
- Second test may fail because `QUIZ_RESULT` state is not set visibly before returning to `LECTURING`.

- [ ] **Step 3: Expose recent components in status**

In `teaching/teaching_controller.py`, update `get_status()` so the returned dict includes recent components:

```python
recent_components = self._component_queue[-20:]
```

Then include it in the return value:

```python
"components": recent_components,
```

Keep the existing `whiteboard` and `quiz` fields for backward compatibility.

- [ ] **Step 4: Make quiz result a visible state**

In `_handle_quiz()`, after computing `is_correct` and before sending result events, set:

```python
self._state = TeachingState.QUIZ_RESULT
```

After the readable delay and `raise_hand` re-enable, keep the existing:

```python
self._state = TeachingState.LECTURING
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
python -m pytest tests/teaching/test_teaching_controller.py::test_status_exposes_recent_components tests/teaching/test_teaching_controller.py::test_quiz_result_state_is_visible_before_lecturing -v
```

Expected: both tests pass.

- [ ] **Step 6: Run teaching controller tests**

Run:

```bash
python -m pytest tests/teaching/test_teaching_controller.py -v
```

Expected: all tests pass.

## Task 2: Interaction Wait and Explicit Microphone Flow

**Files:**
- Modify: `teaching/teaching_controller.py`
- Modify: `teaching/teaching_agent.py`
- Modify: `tests/teaching/test_teaching_controller.py`

- [ ] **Step 1: Add failing test for interaction not immediately advancing**

Append this test to `tests/teaching/test_teaching_controller.py`:

```python
@pytest.mark.asyncio
async def test_interaction_state_waits_for_response_or_timeout(controller):
    ctrl, agent, cm, _ = controller
    idle_task = asyncio.create_task(_keep_tts_idle(ctrl))
    try:
        chapter = cm.get_chapter("ch1")
        await ctrl._broadcast_chapter(chapter)

        assert ctrl.state == TeachingState.WAITING_INTERACT
        status = ctrl.get_status()
        assert status["interaction"] == {
            "text": "你觉得呢？",
            "chapter_id": "ch1",
        }
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python -m pytest tests/teaching/test_teaching_controller.py::test_interaction_state_waits_for_response_or_timeout -v
```

Expected: fails because `get_status()` does not expose `interaction`.

- [ ] **Step 3: Add interaction status**

In `TeachingController.get_status()`, derive `interaction_info`:

```python
interaction_info = None
if chapter and chapter.get("interaction") and self._state == TeachingState.WAITING_INTERACT:
    interaction_info = {
        "text": chapter["interaction"]["prompt"],
        "chapter_id": chapter["id"],
    }
```

Add it to the status return:

```python
"interaction": interaction_info,
```

- [ ] **Step 4: Stop main loop from immediately leaving WAITING_INTERACT**

In `_run_lecture_loop()` and `_resume_lecture()`, replace this pattern:

```python
if self._state == TeachingState.WAITING_INTERACT:
    self._state = TeachingState.LECTURING
```

with:

```python
if self._state == TeachingState.WAITING_INTERACT:
    return
```

This makes the controller wait for listener-driven response handling instead of silently moving on.

- [ ] **Step 5: Preserve WAITING_INTERACT during speech start**

In `TeachingListener._on_speech_started()` in `teaching/teaching_agent.py`, replace:

```python
ctrl._state = TeachingState.ANSWERING
```

with:

```python
if state != TeachingState.WAITING_INTERACT:
    ctrl._state = TeachingState.ANSWERING
```

This ensures ASR final in `WAITING_INTERACT` reaches `_handle_interaction_response()` instead of the general QA path.

- [ ] **Step 6: Run targeted test**

Run:

```bash
python -m pytest tests/teaching/test_teaching_controller.py::test_interaction_state_waits_for_response_or_timeout -v
```

Expected: pass.

## Task 3: Predictable Manager and Xiao Ming Participation

**Files:**
- Modify: `teaching/manager_agent.py`
- Modify: `teaching/classmate_engine.py`
- Modify: `teaching/teaching_controller.py`
- Create: `tests/teaching/test_manager_agent.py`

- [ ] **Step 1: Create failing ManagerAgent tests**

Create `tests/teaching/test_manager_agent.py`:

```python
import pytest

from teaching.manager_agent import ManagerAgent


class FakeLlm:
    pass


@pytest.mark.asyncio
async def test_manager_asks_question_after_silent_mid_chapter():
    manager = ManagerAgent(FakeLlm())
    manager.update_state(
        chapter_id="ch1",
        knowledge_index=1,
        knowledge_total=3,
        student_questions_in_chapter=0,
        elapsed_seconds=60,
        classmate_recently_spoke=True,
    )

    decision = await manager.decide()

    assert decision.action == "ASK_QUESTION"


@pytest.mark.asyncio
async def test_manager_forces_classmate_if_not_spoken_by_last_point():
    manager = ManagerAgent(FakeLlm())
    manager.update_state(
        chapter_id="ch1",
        knowledge_index=2,
        knowledge_total=3,
        student_questions_in_chapter=0,
        elapsed_seconds=90,
        classmate_recently_spoke=False,
    )

    decision = await manager.decide()

    assert decision.action == "CLASSMATE_SPEAK"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/teaching/test_manager_agent.py -v
```

Expected: at least `ASK_QUESTION` test fails because the manager currently never returns `ASK_QUESTION`.

- [ ] **Step 3: Update ManagerAgent deterministic guardrails**

In `teaching/manager_agent.py`, update `decide()` after the long-chapter check and before random classmate logic:

```python
last_point_index = max(0, state.knowledge_total - 1)

if (
    not state.classmate_recently_spoke
    and state.knowledge_index >= last_point_index
    and state.knowledge_total > 1
):
    return ManagerDecision("CLASSMATE_SPEAK", "ensure classmate participates")

if (
    state.student_questions_in_chapter == 0
    and state.knowledge_index > 0
    and state.knowledge_index < last_point_index
):
    return ManagerDecision("ASK_QUESTION", "check understanding after silence")
```

Keep the existing re-explain and skip guardrails above this block.

- [ ] **Step 4: Run ManagerAgent tests**

Run:

```bash
python -m pytest tests/teaching/test_manager_agent.py -v
```

Expected: pass.

- [ ] **Step 5: Handle manager actions in TeachingController**

In `teaching/teaching_controller.py`, expand the manager decision block after `decision = await self._manager.decide()`:

```python
if decision.action == "RE_EXPLAIN":
    alt = await self._polish_skeleton(f"换个方式再讲一遍：{point}")
    self.log_message("agent", alt)
    await self._agent.send_prompt(alt)
elif decision.action == "ASK_QUESTION":
    question = f"小朋友，你觉得刚才这个点是什么意思？可以用自己的话说一说。"
    await self._send_component("interaction_prompt", "show", {
        "text": question,
        "chapter_id": chapter["id"],
        "source": "manager",
    })
    self.log_message("agent", question)
    await self._agent.send_prompt(question)
    self._state = TeachingState.WAITING_INTERACT
    return
elif decision.action == "CLASSMATE_SPEAK":
    if self._classmates and self._classmates.enabled:
        result = await self._classmates.generate_interjection(
            next(iter(self._classmates._llm_clients.keys())),
            f"老师刚讲了「{chapter['title']}」里的一个知识点：{point}",
        )
        if result:
            await self._send_component("classmate_message", "show", result)
            self.log_message("agent", result["text"])
elif decision.action == "SKIP":
    break
```

If direct access to `_llm_clients` feels too leaky during implementation, add a small `first_available_name()` helper on `ClassmateEngine` and use that instead.

- [ ] **Step 6: Run controller and manager tests**

Run:

```bash
python -m pytest tests/teaching/test_manager_agent.py tests/teaching/test_teaching_controller.py -v
```

Expected: pass.

## Task 4: Structured Xiao Ming Quiz and Interaction Messages

**Files:**
- Modify: `teaching/course_component.py`
- Modify: `teaching/classmate_engine.py`
- Modify: `teaching/teaching_controller.py`
- Modify: `tests/teaching/test_course_component.py`
- Create: `tests/teaching/test_classmate_engine.py`

- [ ] **Step 1: Add component type test**

In `tests/teaching/test_course_component.py`, update `test_all_component_types_defined()` expected set to include:

```python
"classmate_message",
"microphone",
```

- [ ] **Step 2: Run component test to verify failure**

Run:

```bash
python -m pytest tests/teaching/test_course_component.py::TestComponentMessage::test_all_component_types_defined -v
```

Expected: fails because the new component types are missing.

- [ ] **Step 3: Add component types**

In `teaching/course_component.py`, append to `COMPONENT_TYPES`:

```python
"classmate_message",
"microphone",
```

- [ ] **Step 4: Create classmate structure tests**

Create `tests/teaching/test_classmate_engine.py`:

```python
import pytest

from teaching.classmate_engine import ClassmateEngine


class FakePersona:
    classmates = [{"name": "小明", "voice": "boy"}]

    def build_classmate_prompt(self, name):
        return f"{name} prompt"

    def build_classmate_interjection_prompt(self, name, context):
        return f"{name} interject {context}"

    def build_classmate_quiz_answer_prompt(self, name, question):
        return f"{name} quiz {question}"


class FakeLlm:
    def __init__(self, response):
        self.response = response
        self._system_prompt = ""
        self._messages = []

    def reset_context(self):
        pass

    async def generate(self, prompt, max_tokens=80):
        return self.response


def llm_factory(name, prompt):
    return FakeLlm("我猜是大家都这样。")


@pytest.mark.asyncio
async def test_interjection_returns_structured_classmate_message():
    engine = ClassmateEngine(FakePersona(), llm_factory)

    result = await engine.generate_interjection("小明", "刚讲了小陷阱")

    assert result["speaker"] == "小明"
    assert result["kind"] == "interjection"
    assert result["text"].startswith("小明：")
    assert "audio_url" in result


@pytest.mark.asyncio
async def test_quiz_answer_returns_structured_classmate_message():
    engine = ClassmateEngine(FakePersona(), llm_factory)

    result = await engine.generate_quiz_answer("小明", "选哪个？")

    assert result["speaker"] == "小明"
    assert result["kind"] == "quiz_guess"
    assert result["text"].startswith("小明：")
```

- [ ] **Step 5: Run classmate tests to verify failure**

Run:

```bash
python -m pytest tests/teaching/test_classmate_engine.py -v
```

Expected: fails because methods return strings or dicts without `speaker` / `kind`.

- [ ] **Step 6: Return structured messages from ClassmateEngine**

In `teaching/classmate_engine.py`, update `generate_interjection()` return:

```python
return {
    "speaker": name,
    "kind": "interjection",
    "text": text,
    "audio_url": audio_url,
}
```

Update `generate_quiz_answer()` return:

```python
return {
    "speaker": name,
    "kind": "quiz_guess",
    "text": f"{name}：{answer.strip()}",
    "audio_url": None,
}
```

Update `generate_interaction_answer()` similarly:

```python
return {
    "speaker": name,
    "kind": "interaction_answer",
    "text": f"{name}：{answer.strip()}",
    "audio_url": None,
}
```

- [ ] **Step 7: Emit Xiao Ming quiz guess in `_handle_quiz()`**

In `TeachingController._handle_quiz()`, after sending the quiz component and before waiting for the child answer, add:

```python
if self._classmates and self._classmates.enabled:
    name = self._classmates.should_answer_interaction()
    if name:
        guess = await self._classmates.generate_quiz_answer(name, quiz["question"])
        if guess:
            await self._send_component("classmate_message", "show", guess)
            self.log_message("agent", guess["text"])
```

- [ ] **Step 8: Run related tests**

Run:

```bash
python -m pytest tests/teaching/test_course_component.py tests/teaching/test_classmate_engine.py tests/teaching/test_teaching_controller.py -v
```

Expected: pass.

## Task 5: Frontend Classroom Redesign

**Files:**
- Modify: `../frontend/teaching.html`

**Permission note:** This file is outside `/Users/luffer/workspace/liveavatar-ws-integration-demo/python-agent`. Execution will require approved write access.

- [ ] **Step 1: Preserve existing API calls**

Before editing, confirm these existing frontend calls remain supported:

```javascript
fetch('/api/start-session', {method:'POST'})
fetch('/api/teaching/raise-hand', {method:'POST'})
fetch('/api/teaching/cancel-hand', {method:'POST'})
fetch('/api/teaching/quiz-answer', ...)
fetch('/api/teaching/status')
client.startAudioCapture()
client.stopAudioCapture()
```

- [ ] **Step 2: Replace layout structure**

Rewrite the body structure of `../frontend/teaching.html` to these stable regions:

```html
<div id="app">
  <header id="classbar">
    <div>
      <div class="course-kicker">思维小达人</div>
      <h1 id="course-title">火眼金睛辨对错</h1>
    </div>
    <div id="status" class="status-idle">未连接</div>
  </header>

  <main id="classroom">
    <section id="teacher-panel">
      <div id="avatar-container"></div>
      <div id="avatar-placeholder">连接老师后开始课堂</div>
      <div id="teacher-controls">
        <button id="btn-connect">连接老师</button>
        <button id="btn-disconnect" disabled>断开</button>
      </div>
    </section>

    <section id="workspace">
      <div id="task-steps">
        <div class="task-step active">1 听故事</div>
        <div class="task-step">2 找陷阱</div>
        <div class="task-step">3 解释原因</div>
      </div>
      <div id="workspace-surface"></div>
      <div id="interaction-strip">
        <div id="strip-speaker">小思老师</div>
        <div id="strip-text">准备上课...</div>
        <button id="btn-primary-action" disabled>举手提问</button>
      </div>
    </section>
  </main>

  <div id="encouragement"></div>
</div>
```

- [ ] **Step 3: Add state render functions**

Add render functions with these signatures:

```javascript
function renderWhiteboard(s) {}
function renderInteraction(interaction) {}
function renderQuiz(quiz, classmateMessage) {}
function renderQuizResult(component) {}
function renderClassmateMessage(component) {}
function renderEncouragement(component) {}
function setMicState(nextState) {}
```

Use `#workspace-surface` as the only main right-side surface.

- [ ] **Step 4: Process generic components from status**

In `pollStatus()`, track component sequence separately:

```javascript
let lastComponentSeq = 0;
let latestClassmateMessage = null;

function processComponents(components) {
  if (!components) return;
  for (const c of components) {
    if (c.seq <= lastComponentSeq) continue;
    lastComponentSeq = c.seq;
    if (c.type === 'classmate_message') {
      latestClassmateMessage = c.data;
      renderClassmateMessage(c.data);
    } else if (c.type === 'quiz_result') {
      renderQuizResult(c.data);
    } else if (c.type === 'encouragement') {
      renderEncouragement(c.data);
    } else if (c.type === 'interaction_prompt') {
      renderInteraction(c.data);
    }
  }
}
```

- [ ] **Step 5: Make teacher questions explicit microphone actions**

When `s.interaction` exists, render `我来回答` as the primary action:

```javascript
async function startInteractionAnswer() {
  if (!connected || !client) return;
  try {
    setMicState('opening');
    await client.startAudioCapture();
    micOn = true;
    setMicState('listening');
  } catch (e) {
    setMicState('closed');
  }
}
```

Do not call `startAudioCapture()` automatically from polling.

- [ ] **Step 6: Keep raise-hand as explicit microphone action**

Keep the existing raise-hand behavior, but route it through the same mic state helper:

```javascript
async function raiseHandQuestion() {
  if (!connected || !client) return;
  try {
    setMicState('opening');
    await client.startAudioCapture();
    micOn = true;
    await fetch('/api/teaching/raise-hand', {method:'POST'});
    setMicState('listening');
  } catch (e) {
    setMicState('closed');
  }
}
```

- [ ] **Step 7: Disable mic during quiz selection**

When `s.quiz && s.state === 'quizzing'`, set the primary action to quiz submission and ensure mic is off:

```javascript
if (micOn && client) {
  try { await client.stopAudioCapture(); } catch (e) {}
  micOn = false;
}
setMicState('closed');
```

- [ ] **Step 8: Manual frontend verification**

Run the teaching agent, open `http://localhost:8082`, and verify:

- Teacher video stays left.
- Whiteboard appears in right workspace.
- Quiz appears in right workspace, not as a side card.
- Xiao Ming message appears in the interaction strip.
- Clicking `我来回答` or `举手提问` opens mic.
- Teacher question does not auto-open mic.
- Selecting a quiz option shows result in the workspace.

## Task 6: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run Python teaching tests**

Run:

```bash
python -m pytest tests/teaching -v
```

Expected: pass.

- [ ] **Step 2: Run all Python tests**

Run:

```bash
python -m pytest -v
```

Expected: pass or document unrelated failures.

- [ ] **Step 3: Start local teaching server**

Run:

```bash
TEACHING_HTTP_PORT=8082 python teaching/teaching_agent.py
```

Expected:

- Server starts on port 8082.
- Course loads.
- No syntax or import errors.

- [ ] **Step 4: Browser smoke test**

Open:

```text
http://localhost:8082
```

Verify:

- Page renders without console syntax errors.
- Connect button is visible.
- Classroom layout matches the approved mockup direction.

- [ ] **Step 5: Record final manual risks**

If real Live Avatar / ASR keys are unavailable, record:

- Which flows were verified by unit tests.
- Which flows were verified visually.
- Which flows still need a real session.

## Self-Review Notes

- Spec coverage:
  - Teacher + whiteboard dual focus: Task 5.
  - Quiz takes right workspace: Task 5.
  - Quiz result visible: Task 1 and Task 5.
  - Xiao Ming predictable participation: Task 3 and Task 4.
  - Explicit microphone policy: Task 2 and Task 5.
  - Interaction wait: Task 2.
- No placeholder steps remain.
- Type names used consistently:
  - `classmate_message`
  - `microphone`
  - `components`
  - `interaction`
  - `QUIZ_RESULT`
