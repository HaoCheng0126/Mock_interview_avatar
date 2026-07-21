# Debug Session: speech-input-lag
- **Status**: [OPEN]
- **Issue**: 用户说话后，语音输入文本出现在界面上的延迟严重，无法做到接近实时上屏。
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-speech-input-lag.ndjson

## Reproduction Steps
1. 进入模拟面试页面并开始一场面试。
2. 开启语音输入，对着麦克风连续说一段话。
3. 观察说话开始时刻、ASR 中间文本出现时刻、最终文本提交时刻。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | 前端没有及时消费 ASR interim chunk，只有在最终结果或整段结束后才上屏。 | High | Low | Pending |
| B | 浏览器端麦克风/音量门限过高，导致“开始说话”识别被延后。 | High | Med | Pending |
| C | 前端把音频分片或文本提交做了节流/等待，造成从采集到发送的排队延迟。 | Med | Med | Pending |
| D | 后端 ASR 回调本身到得晚，延迟主要发生在浏览器之外。 | Med | Med | Pending |
| E | UI 渲染草稿气泡/候选人气泡的时机过晚，ASR 已返回但前端没立即显示。 | Med | Low | Pending |

## Log Evidence
- Pending

## Verification Conclusion
- Pending
