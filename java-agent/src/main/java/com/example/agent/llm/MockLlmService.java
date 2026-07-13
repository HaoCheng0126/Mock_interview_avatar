package com.example.agent.llm;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

@Component
public class MockLlmService {

    private static final List<String> REPLIES = java.util.Arrays.asList(
            "你好！我是你的数字人助手，有什么可以帮助你的吗？",
            "这是一个很好的问题！让我来为你解答。",
            "当然可以，我很乐意帮你处理这个问题。",
            "感谢你的提问。根据我的理解，这个问题的关键在于理解核心概念。",
            "让我想想...这个问题可以从几个方面来看。首先，我们需要明确需求；其次，选择合适的技术方案；最后，持续迭代优化。",
            "你说得对！我完全同意你的观点。",
            "有意思的角度！我之前没有这样想过，但这确实值得深入探讨。",
            "从技术角度来看，这个方案是可行的。不过我们需要考虑一些边界情况。",
            "根据最佳实践，我建议采用渐进式的方案，先做出最小可行版本，然后逐步完善。",
            "这个问题比较复杂，让我分几个方面来回答：第一...第二...第三..."
    );

    private final AtomicInteger index = new AtomicInteger(0);

    public String reply(String userText) {
        return REPLIES.get(index.getAndIncrement() % REPLIES.size());
    }
}
