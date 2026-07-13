# Interview Digital Human Design

Date: 2026-07-09
Status: Draft for review

## Goal

Add an `interview` digital human mode alongside the existing `chat`,
`teaching`, `broadcast`, and `talkshow` modes.

The interview avatar should simulate a real interviewer. Unlike `chat`, the
avatar initiates the questions and the user answers. Unlike `broadcast` and
`talkshow`, playback cannot be a continuous `system.prompt` queue; each prompt
must wait for the candidate's spoken answer before the controller decides
whether to probe, move on, or close the interview.

## Assumptions

- This is a new mode, not a prompt-only variant of `chat`.
- Phase one focuses on one interviewer and one candidate.
- The candidate answers by voice.
- Developer ASR is allowed, using the same `input.voice.*` and
  `input.asr.*` SDK methods already used by `chat` and `teaching`.
- The platform remains responsible for TTS when the avatar speaks.
- Runtime coaching is limited. Detailed feedback is generated at the end so the
  interview still feels like an interview, not a lesson.
- Resume upload, ATS workflows, multiple interviewers, and visual candidate
  analysis are out of scope for phase one.

## Recommended Approach

Build a structured interview agent with an explicit state machine.

The agent should borrow interaction gating from `teaching` and configuration
patterns from `talkshow`, but it should keep interview-specific concepts in its
own modules:

- `InterviewController` owns session state and the question-answer loop.
- `InterviewManager` loads the YAML interview profile.
- `QuestionPlanner` selects the next main question or follow-up.
- `AnswerEvaluator` evaluates each answer and recommends whether to probe.
- `InterviewReportGenerator` produces the final structured report.
- `InterviewListener` bridges LiveAvatar audio/session events into the
  controller.

This approach is more controlled than a single system prompt and more
interview-like than a fixed question bank.

## Product Shape

The first version is a real-time mock interview:

1. The operator starts an interview session.
2. The avatar introduces the interview format.
3. The avatar asks one question at a time.
4. The candidate answers by voice.
5. The agent transcribes and evaluates the answer.
6. The avatar asks a follow-up if needed and allowed.
7. The controller moves to the next question when the topic is closed.
8. The avatar closes the interview.
9. The app shows a structured report.

The product should support these configuration inputs:

- Interview type: technical, behavioral, project deep dive, English, sales, or
  another configured category.
- Target role.
- Difficulty.
- Language.
- Duration.
- Interviewer persona and boundaries.
- Scoring rubric.
- Required and optional question sections.
- Candidate background text.

## Scene Readiness and Opening

The first spoken interview prompt must be gated by `scene.ready`. Starting the
backend session is not enough: the avatar may have a LiveAvatar room and token
before the frontend scene has rendered and is ready to play speech naturally.

`/api/interview/start` therefore prepares the interview and moves the runtime to
`waiting_scene_ready`. It must not ask the first question. The JS SDK sends
`scene.ready` over the LiveKit DataChannel once the scene is ready. The
coordinator bridges that protocol event to the agent WebSocket. The interview
agent handles that forwarded `scene.ready` event and only then begins the spoken
interview.

`scene.ready` should first trigger a short opening prompt that introduces the
format and leads naturally into the first question. The opening prompt is not a
question and does not use a `question_id`, but it is recorded in the transcript:

```json
{
  "role": "interviewer",
  "type": "opening",
  "text": "你好，欢迎参加这次模拟面试。我会围绕项目经历、技术深度和问题解决方式提问。你可以结合真实经历回答。我们先从第一个问题开始。",
  "metadata": {
    "interviewId": "iv_001",
    "promptType": "opening"
  }
}
```

After the opening prompt, the controller may automatically move into the first
question after a short configured delay. It should not require the candidate to
speak before the first question.

## States

Use explicit states so ASR is only accepted when the candidate is expected to
speak:

- `idle`: no active interview.
- `starting`: backend session and interview runtime are preparing.
- `waiting_scene_ready`: interview is prepared, but no spoken prompt has been
  sent because the avatar scene is not ready.
- `opening`: avatar explains the format.
- `asking`: avatar asks the main question or follow-up.
- `listening`: candidate speech is accepted.
- `thinking_check`: avatar checks whether the candidate is still thinking.
- `skipping_question`: current exchange timed out and the controller is moving
  to the next question.
- `analyzing`: answer is being evaluated.
- `deciding_followup`: answer is being judged for whether a probe is useful.
- `planning_followup`: follow-up text is being produced.
- `probing`: follow-up question is being asked.
- `transitioning`: avatar bridges to the next topic.
- `closing`: avatar ends the interview.
- `completed`: final report is ready.
- `terminated`: interview ended early because there were not enough valid
  answers or repeated no-answer timeouts.
- `error`: unrecoverable runtime error.

Only `listening` should accept candidate speech as an answer. Other states
should ignore or suppress speech unless the product later adds an explicit
candidate interruption feature.

## Event Model

Avatar speech uses the SDK's prompt helper:

```python
await agent.send_prompt(text)
```

The underlying event is:

```json
{
  "event": "system.prompt",
  "data": {
    "text": "请介绍一个你最近主导的后端项目。"
  }
}
```

For interview mode, all events belonging to the same business exchange should
carry the same business IDs in `data`. The first and subsequent avatar prompts
therefore include IDs such as:

```json
{
  "event": "system.prompt",
  "data": {
    "text": "请介绍一个你最近主导的后端项目。",
    "interviewId": "iv_001",
    "sectionId": "project_deep_dive",
    "questionId": "q_project_deep_dive_001",
    "exchangeId": "ex_001",
    "promptId": "prompt_001",
    "promptType": "main_question"
  }
}
```

The SDK should support optional metadata on every standard event that can
belong to a business exchange, instead of forcing application code to rely on
private websocket access or `send_custom_event(...)` wrappers.

Recommended SDK shape:

```python
await agent.send_prompt(
    text,
    metadata={
        "interviewId": "iv_001",
        "sectionId": "project_deep_dive",
        "questionId": "q_project_deep_dive_001",
        "exchangeId": "ex_001",
        "promptId": "prompt_001",
        "promptType": "main_question",
    },
)

await agent.send_voice_start(answer_request_id, metadata=metadata)
await agent.send_asr_partial(answer_request_id, text, seq, metadata=metadata)
await agent.send_voice_finish(answer_request_id, metadata=metadata)
await agent.send_asr_final(answer_request_id, final_text, metadata=metadata)
```

`MessageBuilder` should merge optional metadata into `data`, preserving
backward compatibility:

```python
def system_prompt(text: str, metadata: Optional[dict] = None) -> dict:
    data = {"text": text}
    if metadata:
        data.update(metadata)
    return {"event": EventType.SYSTEM_PROMPT, "data": data}
```

Metadata should not override reserved protocol data fields such as `text`,
`final`, or `audioConfig`.

Candidate speech in Developer ASR mode uses one SDK `requestId` per spoken
answer:

```python
answer_request_id = str(uuid.uuid4())

await agent.send_voice_start(answer_request_id)
await agent.send_asr_partial(answer_request_id, text, seq)
await agent.send_voice_finish(answer_request_id)
await agent.send_asr_final(answer_request_id, final_text)
```

`input.asr.partial` is optional. Sending only `input.asr.final` is acceptable
when the ASR provider does not expose useful interim text.

Do not send `control.interrupt` for normal candidate answers. The SDK protocol
states that the platform clears the RTC playback buffer when it receives
`input.text` or `input.voice.start`. Use `control.interrupt` only for proactive
business actions such as stopping the avatar, skipping the current spoken
prompt, or force-ending the interview.

Application UI state should use custom events, not spoken prompt text:

- `interview.state`
- `interview.question`
- `interview.exchange`
- `interview.transcript`
- `interview.evaluation`
- `interview.report`

These events are for frontend rendering and debugging. They should not be
treated as the source of avatar speech.

## ID Model

The SDK `requestId` should not be used as the business-level interview turn ID.
In interview mode, the avatar often initiates the question, but the SDK
`requestId` identifies a candidate input turn.

Use separate IDs:

- `interview_id`: one interview session.
- `section_id`: a grouped interview area, such as project deep dive or backend
  fundamentals.
- `question_id`: one assessment topic or main question.
- `exchange_id`: one interviewer prompt plus one candidate answer.
- `prompt_id`: one avatar-spoken prompt.
- `answer_request_id`: the SDK `requestId` for one candidate voice answer.

A single `question_id` may contain several exchanges:

```json
{
  "questionId": "q_project_deep_dive_001",
  "status": "probing",
  "exchanges": [
    {
      "exchangeId": "ex_001",
      "type": "main_question",
      "promptId": "prompt_001",
      "promptText": "请介绍一个你最近主导的后端项目。",
      "answerRequestId": "req_a1",
      "answerText": "我最近做了订单系统...",
      "evaluation": {
        "depth": 3,
        "clarity": 4,
        "followUpNeeded": true
      }
    },
    {
      "exchangeId": "ex_002",
      "type": "follow_up",
      "parentExchangeId": "ex_001",
      "probeIndex": 1,
      "promptText": "你提到异步任务，失败重试时如何保证幂等？",
      "answerRequestId": "req_a2",
      "answerText": "我们用了业务唯一键...",
      "evaluation": {
        "depth": 4,
        "clarity": 3,
        "followUpNeeded": false
      }
    }
  ]
}
```

In short:

- `question_id` is the assessment topic.
- `exchange_id` is the question-answer exchange.
- SDK `requestId` is the candidate voice input ID.

## Question Loop

The controller loop should follow this shape:

```text
WAIT_SCENE_READY
OPENING
ASK_MAIN_QUESTION
WAIT_ANSWER_WITH_TIMEOUTS

if hard_timeout and no effective answer:
    record question_skipped
    if no_answer_policy says terminate:
        TERMINATE_INTERVIEW
    else:
        NEXT_QUESTION
else:
    EVALUATE_ANSWER
    DECIDE_FOLLOW_UP
    if follow_up_needed and probe_index < max_probe:
        PLAN_FOLLOW_UP
        ASK_FOLLOW_UP
        WAIT_ANSWER_WITH_TIMEOUTS
    else:
        CLOSE_QUESTION
        NEXT_QUESTION
```

Every interviewer prompt that expects an answer, including follow-ups, has its
own timeout lifecycle. A default policy can use two thinking checks before a
hard timeout:

```yaml
timeouts:
  thinking_checks:
    - after_seconds: 20
      prompt: "我看到你还在思考。你可以先从一个具体经历或关键决策讲起。"
    - after_seconds: 45
      prompt: "这个问题可以再给你一点时间。如果暂时没有思路，也可以简单说明。"
  hard_timeout_seconds: 75
```

At hard timeout the controller should not immediately terminate the whole
interview. It should skip the question when there are remaining questions and
record the skip:

```json
{
  "role": "system",
  "type": "question_skipped",
  "questionId": "q_technical_depth_01",
  "exchangeId": "ex_003",
  "reason": "hard_timeout_no_answer",
  "timeoutSeconds": 75
}
```

Termination is reserved for stronger no-answer signals:

```yaml
no_answer_policy:
  max_skipped_questions: 3
  max_consecutive_skipped_questions: 2
  min_effective_answers: 2
  terminate_when_no_questions_left_and_insufficient_answers: true
```

If enough useful answers have already been collected and there are no more
questions, the interview should close normally rather than describe the outcome
as a termination. If there are too few valid answers, the avatar should use an
early termination prompt and the final report should mark the termination
reason.

The evaluator should return structured data rather than only prose:

```json
{
  "score": 3,
  "dimensions": {
    "relevance": 4,
    "depth": 3,
    "clarity": 3,
    "evidence": 2
  },
  "strengths": ["能说明项目背景"],
  "weaknesses": ["缺少量化结果", "技术决策解释不够"],
  "followUpNeeded": true,
  "followUpQuestion": "你为什么选择这个方案，而不是消息队列？"
}
```

The avatar should not read this full evaluation during the interview. It should
use short transition text and reserve detailed feedback for the final report.

Follow-up handling should be split into two explicit steps:

1. `FollowUpDecision`: decide whether a probe is needed, why, and what signal is
   missing from the answer.
2. `FollowUpPlanner`: generate the actual follow-up prompt for the same
   `question_id` and a new `exchange_id`.

This prevents the evaluator from mixing scoring, decisioning, and prompt
writing in one opaque output.

## Transcript

The transcript is a first-class interview artifact. It should record every
avatar prompt, candidate answer, timeout check, skip, closing prompt, and
termination prompt. Evaluation and report generation must use the complete
transcript rather than only the latest answer.

Recommended turn shape:

```json
{
  "turnId": "turn_001",
  "interviewId": "iv_001",
  "questionId": "q_project_deep_dive_001",
  "exchangeId": "ex_001",
  "role": "interviewer",
  "type": "main_question",
  "text": "请介绍一个你最近主导的后端项目。",
  "timestamp": "2026-07-10T10:00:00Z",
  "metadata": {
    "sectionId": "project_deep_dive",
    "promptId": "prompt_001"
  }
}
```

The frontend status payload should expose transcript turns for debugging and
review, but it should not show live scores during the interview.

## Scoring and Question Coverage

Scoring should be dimension-first, with evidence pulled from transcript turns.
The first production rubric should split at least these dimensions:

- technical_depth
- problem_solving
- project_ownership
- communication_clarity
- collaboration
- learning_ability
- reliability_awareness
- role_fit

Each dimension should produce:

```json
{
  "score": 1,
  "evidence": ["候选人在 ex_002 中说明了幂等键设计。"],
  "concerns": ["没有说明异常补偿策略。"],
  "recommendations": ["后续可继续追问数据一致性。"],
  "confidence": "medium"
}
```

Questions should be organized by competency rather than as a short flat list.
Each question should declare the competency it probes, expected positive
signals, red flags, difficulty, maximum follow-ups, and timeout policy.

## Configuration

Create `config/interview.yaml`:

```yaml
interview:
  title: "Python 后端工程师模拟面试"
  lang: zh
  duration_minutes: 20
  difficulty: mid
  max_probe_per_question: 2

interviewer:
  name: "林面试官"
  style: "专业、克制、适度追问，不打断候选人"
  rules:
    - "一次只问一个问题"
    - "追问最多连续两次"
    - "不直接给标准答案"
    - "结束后再集中反馈"

candidate:
  target_role: "Python 后端工程师"
  background: "有 3 年后端经验，熟悉 FastAPI、MySQL、Redis"

rubric:
  dimensions:
    - technical_depth
    - problem_solving
    - communication
    - project_ownership
    - reliability_awareness

question_sets:
  - id: project_deep_dive
    title: "项目深挖"
    required: true
  - id: python_backend
    title: "后端技术"
    required: true
  - id: behavior
    title: "行为面试"
    required: false
```

## Minimal Frontend

The first frontend should be operational rather than decorative:

- Connect and disconnect session controls.
- Start, stop, and skip controls.
- Avatar video area.
- Current interview state.
- Current question.
- Live or final transcript.
- Section/question progress.
- Final report panel.

The UI should not expose per-answer scores during the live interview unless a
later product decision turns the mode into coaching instead of realistic mock
interviewing.

## Error Handling

- If ASR produces an empty or very short transcript, ask the candidate to repeat
  once.
- If no answer is heard before timeout, ask a short reminder, then either retry
  the same exchange or move on after the configured retry limit.
- If evaluation fails, record the raw transcript and continue with a simple
  transition instead of blocking the interview.
- If TTS idle is not received, use the existing timeout pattern from
  `teaching` and `talkshow` to avoid hanging the controller.

## Testing

Add focused tests around the new mode:

- Controller state transitions for main question, answer, follow-up, and close.
- One `question_id` containing multiple `exchange_id` records.
- One SDK `answer_request_id` per candidate answer.
- ASR gating: answers are accepted only in `listening`.
- Evaluator parsing and fallback behavior.
- Report generation from recorded exchanges.
- HTTP route status shape.

## Out of Scope

- Resume file upload and parsing.
- Enterprise candidate pipelines.
- Multiple simultaneous interviewers.
- Real-time facial expression or posture analysis.
- Live score display during the interview.
- Fully autonomous open-ended interview with no configured sections.
