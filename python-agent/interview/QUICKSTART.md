# Interview Avatar Quickstart

Run a structured mock interview with a LiveAvatar interviewer.

## Configure

Edit `config/interview.yaml`:

- `candidate.target_role`: target role for the interview
- `interviewer`: interviewer name, style, and rules
- `question_sets`: ordered main questions
- `rubric.dimensions`: scoring dimensions

## Run

```bash
export LIVEAVATAR_API_KEY="lk_live_xxx"
export LIVEAVATAR_AVATAR_ID="avatar_xxx"
export DEEPSEEK_API_KEY="sk-xxx"

python interview/agent.py
```

Open `http://localhost:8083` and click Connect. The page joins the LiveAvatar
room and starts the interview automatically.

## Flow

```text
system.prompt + metadata -> avatar asks
candidate voice -> input.voice.* / input.asr.* + same metadata
AnswerEvaluator -> follow-up or next question
ReportGenerator -> final report
```

Each main question can contain multiple exchanges. The business IDs are:

- `questionId`: assessment topic
- `exchangeId`: one prompt and one answer
- `promptId`: one avatar prompt
- `answerId`: one candidate answer
