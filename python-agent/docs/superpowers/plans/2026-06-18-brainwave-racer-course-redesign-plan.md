# Brainwave Racer Course Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic interactions in `小AI的脑波赛车大冒险_7-8` with a dedicated, technology-themed `brainwave_racer` primitive and matching course content.

**Architecture:** Add one frontend whiteboard renderer registered as `brainwave_racer`. The renderer reads `scene.props.mode` and draws one of three compact DOM scenes: helmet scan, signal pipeline, or race track. The YAML course uses only this primitive for its interactive scenes, with no backend protocol changes.

**Tech Stack:** Plain browser JavaScript modules, DOM APIs, CSS, YAML, pytest static frontend checks.

## Global Constraints

- Keep the existing course title, audience, teacher persona, and three-chapter story arc.
- Keep the content understandable for 7-8 year old children.
- Use one dedicated primitive, `brainwave_racer`, with multiple modes instead of several unrelated primitives.
- Avoid changing behavior for other courses.
- Do not redesign the full teaching UI.
- Do not modify unrelated primitives.
- Do not add new backend component types.
- Do not introduce a new build system or dependency.

---

## File Structure

- Create `../frontend/teaching/whiteboard/brainwave-racer.js`: the only new renderer. It exports `renderBrainwaveRacer(scene)` and local helpers for `scan`, `signal`, and `race` modes.
- Modify `../frontend/teaching/whiteboard/index.js`: import and register `brainwave_racer`.
- Modify `../frontend/teaching/whiteboard/styles.css`: add namespaced `.brainwave-*` styles only.
- Modify `config/courses/小AI的脑波赛车大冒险_7-8.yaml`: replace all interactive scene primitives with `brainwave_racer` and update copy/props to match.
- Modify `tests/teaching/test_teaching_frontend.py`: add static checks for registration and course usage.

---

### Task 1: Add Static Tests For The New Primitive And Course Contract

**Files:**
- Modify: `tests/teaching/test_teaching_frontend.py`

**Interfaces:**
- Consumes: existing `_whiteboard_file(name: str) -> str` helper.
- Produces: failing tests that require `renderBrainwaveRacer`, `brainwave_racer`, `.brainwave-stage`, and course-only use of the new primitive.

- [ ] **Step 1: Add the course path constant**

Add this near the existing `CLOTHING_COURSE` constant:

```python
BRAINWAVE_COURSE = (
    Path(__file__).parents[2]
    / "config"
    / "courses"
    / "小AI的脑波赛车大冒险_7-8.yaml"
)
```

- [ ] **Step 2: Add the course helper**

Add this near `_clothing_course()`:

```python
def _brainwave_course() -> str:
    return BRAINWAVE_COURSE.read_text(encoding="utf-8")
```

- [ ] **Step 3: Add the static tests**

Add these tests after `test_frontend_has_interactive_scene_runtime_primitives`:

```python
def test_brainwave_racer_primitive_is_registered():
    index_js = _whiteboard_file("index.js")
    module = _whiteboard_file("brainwave-racer.js")
    styles = _whiteboard_file("styles.css")

    assert "renderBrainwaveRacer" in index_js
    assert "brainwave_racer: renderBrainwaveRacer" in index_js
    assert "export function renderBrainwaveRacer(scene)" in module
    assert "props.mode || 'scan'" in module
    assert "renderScanMode" in module
    assert "renderSignalMode" in module
    assert "renderRaceMode" in module
    assert ".brainwave-stage" in styles
    assert ".brainwave-track" in styles


def test_brainwave_racer_course_uses_dedicated_primitive_only():
    course = _brainwave_course()

    assert "primitive: brainwave_racer" in course
    assert "mode: scan" in course
    assert "mode: signal" in course
    assert "mode: race" in course
    assert "primitive: bar_chart" not in course
    assert "primitive: sort_order" not in course
    assert "primitive: state_machine" not in course
    assert "primitive: drag_to_animal" not in course
    assert "平静清楚的脑波信号" in course
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
pytest tests/teaching/test_teaching_frontend.py::test_brainwave_racer_primitive_is_registered tests/teaching/test_teaching_frontend.py::test_brainwave_racer_course_uses_dedicated_primitive_only -v
```

Expected: FAIL because `brainwave-racer.js` does not exist and the course still uses old primitives.

---

### Task 2: Implement The `brainwave_racer` Renderer And Styles

**Files:**
- Create: `../frontend/teaching/whiteboard/brainwave-racer.js`
- Modify: `../frontend/teaching/whiteboard/index.js`
- Modify: `../frontend/teaching/whiteboard/styles.css`
- Test: `tests/teaching/test_teaching_frontend.py`

**Interfaces:**
- Consumes: `sceneButton(text, onClick)` from `./shared.js`.
- Produces: `export function renderBrainwaveRacer(scene)` for registry use.

- [ ] **Step 1: Create the renderer file**

Create `../frontend/teaching/whiteboard/brainwave-racer.js`:

```javascript
import { sceneButton } from './shared.js';

export function renderBrainwaveRacer(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'scan';

  if (mode === 'signal') return renderSignalMode(props);
  if (mode === 'race') return renderRaceMode(props);
  return renderScanMode(props);
}

function renderScanMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-scan';

  const states = props.states || [
    { label: '分心', focus: 35, quality: '信号有点乱' },
    { label: '正常', focus: 62, quality: '信号正在变稳' },
    { label: '专注', focus: 88, quality: '平静清楚的脑波信号' },
  ];
  let index = Math.max(0, states.findIndex(item => item.label === (props.active || '专注')));
  if (index < 0) index = 0;

  const helmet = document.createElement('div');
  helmet.className = 'brainwave-helmet';
  helmet.innerHTML = '<div class="brainwave-helmet-core">AI</div><div class="brainwave-helmet-band"></div>';

  const wave = document.createElement('div');
  wave.className = 'brainwave-wave';

  const meter = document.createElement('div');
  meter.className = 'brainwave-meter';

  const readout = document.createElement('div');
  readout.className = 'brainwave-readout';

  function draw() {
    const state = states[index];
    const focus = Math.max(0, Math.min(100, Number(state.focus) || 0));
    wave.innerHTML = '';
    for (let i = 0; i < 18; i++) {
      const dot = document.createElement('span');
      dot.style.height = (12 + ((i * 7 + focus) % 34)) + 'px';
      dot.style.opacity = String(0.45 + focus / 180);
      wave.appendChild(dot);
    }
    meter.innerHTML = '<div class="brainwave-meter-fill"></div>';
    meter.firstChild.style.width = focus + '%';
    readout.textContent = state.label + '：' + focus + '% · ' + state.quality;
  }

  draw();
  stage.append(helmet, wave, meter, readout);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('提升专注', () => {
      index = states.length - 1;
      draw();
    }),
    sceneButton('切换状态', () => {
      index = (index + 1) % states.length;
      draw();
    })
  );

  return [stage, actions];
}

function renderSignalMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-signal';

  const steps = props.steps || ['脑波输入', 'AI识别', '生成指令', '赛车响应'];
  const commands = props.commands || ['保持直线', '轻轻加速', '全速前进'];
  let active = 0;

  const pipeline = document.createElement('div');
  pipeline.className = 'brainwave-pipeline';

  const command = document.createElement('div');
  command.className = 'brainwave-command';

  function draw() {
    pipeline.innerHTML = '';
    steps.forEach((step, idx) => {
      const node = document.createElement('div');
      node.className = 'brainwave-node';
      if (idx <= active) node.classList.add('active');
      node.textContent = step;
      pipeline.appendChild(node);
    });
    command.textContent = '当前指令：' + commands[Math.min(active, commands.length - 1)];
  }

  draw();
  stage.append(pipeline, command);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('发送信号', () => {
      active = Math.min(active + 1, steps.length - 1);
      draw();
    }),
    sceneButton('查看指令', () => {
      active = steps.length - 1;
      draw();
    })
  );

  return [stage, actions];
}

function renderRaceMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-race';

  let focus = Number(props.focus || 55);
  let distracted = Boolean(props.distraction);

  const track = document.createElement('div');
  track.className = 'brainwave-track';
  const car = document.createElement('div');
  car.className = 'brainwave-car';
  car.textContent = props.car || 'AI';
  track.appendChild(car);

  const energy = document.createElement('div');
  energy.className = 'brainwave-energy';

  const status = document.createElement('div');
  status.className = 'brainwave-status';

  function draw() {
    focus = Math.max(0, Math.min(100, focus));
    car.style.left = Math.min(88, 8 + focus * 0.8) + '%';
    track.classList.toggle('has-distraction', distracted);
    energy.innerHTML = '<div class="brainwave-energy-fill"></div>';
    energy.firstChild.style.width = focus + '%';
    status.textContent = distracted
      ? '干扰出现：先稳住脑波'
      : focus >= 90
        ? '能量满格：可以冲过终点'
        : '专注能量：' + focus + '%';
  }

  draw();
  stage.append(track, energy, status);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('提升专注', () => {
      focus += distracted ? 8 : 16;
      draw();
    }),
    sceneButton('排除干扰', () => {
      distracted = false;
      draw();
    }),
    sceneButton('冲刺', () => {
      if (!distracted && focus >= 80) focus = 100;
      draw();
    })
  );

  return [stage, actions];
}
```

- [ ] **Step 2: Register the renderer**

Modify `../frontend/teaching/whiteboard/index.js`:

```javascript
import { renderBrainwaveRacer } from './brainwave-racer.js';
```

Add this entry to `interactiveSceneRegistry`:

```javascript
  brainwave_racer: renderBrainwaveRacer,
```

- [ ] **Step 3: Add scoped styles**

Append this CSS to `../frontend/teaching/whiteboard/styles.css`:

```css
/* ===== brainwave-racer ===== */
.brainwave-stage{position:relative;display:flex;flex-direction:column;gap:12px;min-height:210px;padding:14px;border:1px solid #18395f;border-radius:var(--radius);background:linear-gradient(135deg,#071827,#0d2741 58%,#123a5b);color:#eaf7ff;overflow:hidden}
.brainwave-stage::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(91,214,255,.08) 1px,transparent 1px),linear-gradient(rgba(91,214,255,.06) 1px,transparent 1px);background-size:22px 22px;pointer-events:none}
.brainwave-stage>*{position:relative;z-index:1}
.brainwave-helmet{height:74px;display:flex;align-items:center;justify-content:center;gap:12px}
.brainwave-helmet-core{width:62px;height:48px;border:2px solid #5bd6ff;border-radius:24px 24px 16px 16px;display:flex;align-items:center;justify-content:center;font-weight:900;color:#5bd6ff;box-shadow:0 0 18px rgba(91,214,255,.45)}
.brainwave-helmet-band{width:120px;height:12px;border-radius:999px;background:linear-gradient(90deg,#5bd6ff,#72f0a8);box-shadow:0 0 14px rgba(114,240,168,.4)}
.brainwave-wave{height:58px;display:flex;align-items:center;justify-content:center;gap:5px}
.brainwave-wave span{width:5px;border-radius:999px;background:#72f0a8;box-shadow:0 0 10px rgba(114,240,168,.55);transition:height .22s ease,opacity .22s ease}
.brainwave-meter,.brainwave-energy{height:14px;border:1px solid rgba(91,214,255,.55);border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden}
.brainwave-meter-fill,.brainwave-energy-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#5bd6ff,#72f0a8,#f5d76e);box-shadow:0 0 14px rgba(114,240,168,.55);transition:width .25s ease}
.brainwave-readout,.brainwave-command,.brainwave-status{font-size:14px;font-weight:900;text-align:center;color:#eaf7ff}
.brainwave-pipeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:24px}
.brainwave-node{min-height:66px;border:1px solid rgba(91,214,255,.35);border-radius:var(--radius);background:rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;text-align:center;font-size:13px;font-weight:900;color:#9fb3c8;transition:all .2s ease}
.brainwave-node.active{border-color:#72f0a8;color:#fff;background:rgba(114,240,168,.16);box-shadow:0 0 16px rgba(114,240,168,.25)}
.brainwave-track{height:110px;border:1px solid rgba(91,214,255,.45);border-radius:999px;background:linear-gradient(180deg,rgba(255,255,255,.1),rgba(255,255,255,.03));position:relative;overflow:hidden}
.brainwave-track::before{content:"";position:absolute;left:8%;right:8%;top:50%;border-top:2px dashed rgba(234,247,255,.45)}
.brainwave-track::after{content:"终点";position:absolute;right:8px;top:12px;font-size:12px;font-weight:900;color:#f5d76e}
.brainwave-track.has-distraction{box-shadow:inset 0 0 0 2px rgba(217,91,89,.55)}
.brainwave-track.has-distraction::after{content:"干扰"}
.brainwave-car{position:absolute;top:50%;left:8%;transform:translate(-50%,-50%);width:54px;height:34px;border-radius:16px 18px 10px 10px;background:linear-gradient(135deg,#f5d76e,#ff9f43);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;color:#102235;box-shadow:0 0 18px rgba(245,215,110,.45);transition:left .3s ease}
@media (max-width:640px){.brainwave-pipeline{grid-template-columns:repeat(2,minmax(0,1fr))}.brainwave-stage{min-height:220px}.brainwave-helmet-band{width:86px}}
```

- [ ] **Step 4: Run the primitive registration test**

Run:

```bash
pytest tests/teaching/test_teaching_frontend.py::test_brainwave_racer_primitive_is_registered -v
```

Expected: PASS.

---

### Task 3: Rewrite The Brainwave Racer Course Interactions

**Files:**
- Modify: `config/courses/小AI的脑波赛车大冒险_7-8.yaml`
- Test: `tests/teaching/test_teaching_frontend.py`

**Interfaces:**
- Consumes: `brainwave_racer` modes `scan`, `signal`, and `race`.
- Produces: course steps whose `experience.primitive` is always `brainwave_racer`.

- [ ] **Step 1: Replace the existing chapter skeletons**

Update each `experience` block in `config/courses/小AI的脑波赛车大冒险_7-8.yaml`:

Chapter 1 should use `mode: scan` for all three steps with `states` and `active` props.

Chapter 2 should use `mode: signal` for its two interactive steps with `steps` and `commands` props.

Chapter 3 should use `mode: race` for all three steps with `focus`, `distraction`, and `car` props.

- [ ] **Step 2: Correct the quiz**

In chapter 2, update the quiz so the correct option is:

```yaml
    - key: A
      text: 平静清楚的脑波信号
      correct: true
```

Use wrong options that describe noisy or scattered signals:

```yaml
    - key: B
      text: 忽高忽低的乱跳信号
      correct: false
    - key: C
      text: 被干扰打断的红色信号
      correct: false
```

- [ ] **Step 3: Run the course contract test**

Run:

```bash
pytest tests/teaching/test_teaching_frontend.py::test_brainwave_racer_course_uses_dedicated_primitive_only -v
```

Expected: PASS.

---

### Task 4: Run Focused Verification And Inspect The Diff

**Files:**
- Verify: `tests/teaching/test_teaching_frontend.py`
- Inspect: all files changed by Tasks 1-3

**Interfaces:**
- Consumes: completed renderer, styles, registry, course, and tests.
- Produces: verified local change set.

- [ ] **Step 1: Run focused frontend static tests**

Run:

```bash
pytest tests/teaching/test_teaching_frontend.py -v
```

Expected: PASS.

- [ ] **Step 2: Run course manager tests for YAML loading safety**

Run:

```bash
pytest tests/teaching/test_course_manager.py -v
```

Expected: PASS.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git diff -- ../frontend/teaching/whiteboard/index.js ../frontend/teaching/whiteboard/brainwave-racer.js ../frontend/teaching/whiteboard/styles.css config/courses/小AI的脑波赛车大冒险_7-8.yaml tests/teaching/test_teaching_frontend.py
```

Expected: diff only contains the planned primitive, course, and test changes. If `git` is unavailable because this workspace is not a repository, use:

```bash
sed -n '1,240p' ../frontend/teaching/whiteboard/brainwave-racer.js
sed -n '1,80p' ../frontend/teaching/whiteboard/index.js
rg -n "brainwave" ../frontend/teaching/whiteboard/styles.css config/courses/小AI的脑波赛车大冒险_7-8.yaml tests/teaching/test_teaching_frontend.py
```

Expected: all references are scoped to `brainwave_racer` and `.brainwave-*`.
