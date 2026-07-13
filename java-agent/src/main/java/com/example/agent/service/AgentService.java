package com.example.agent.service;

import com.example.agent.llm.MockLlmService;
import com.newportai.liveavatar.channel.agent.AgentListener;
import com.newportai.liveavatar.channel.agent.AvatarAgent;
import com.newportai.liveavatar.channel.agent.AvatarAgentConfig;
import com.newportai.liveavatar.channel.agent.SessionInfo;
import com.newportai.liveavatar.channel.model.AudioFrame;
import com.newportai.liveavatar.channel.model.SessionState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.annotation.PreDestroy;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class AgentService implements AgentListener {

    private static final Logger log = LoggerFactory.getLogger(AgentService.class);
    private static final int MAX_LOG_LINES = 1000;

    @Value("${avatar.api.key}")
    private String apiKey;

    @Value("${avatar.api.base-url:https://liveavatar.aimiai.com/vih/dispatcher}")
    private String baseUrl;

    @Value("${avatar.id}")
    private String defaultAvatarId;

    @Value("${avatar.voice-id:}")
    private String voiceId;

    @Value("${avatar.sandbox.enabled:false}")
    private boolean sandbox;

    @Value("${avatar.asr.developer-enabled:false}")
    private boolean developerAsr;

    @Value("${avatar.tts.developer-enabled:false}")
    private boolean developerTts;

    private final MockLlmService mockLlm;
    private final Map<String, AvatarAgent> sessions = new ConcurrentHashMap<>();
    private final List<String> sharedLogs = new CopyOnWriteArrayList<>();

    public AgentService(MockLlmService mockLlm) {
        this.mockLlm = mockLlm;
    }

    public List<String> getLogs() {
        return sharedLogs;
    }

    public SessionInfo startSession(String avatarId) throws Exception {
        String effectiveAvatarId = avatarId != null ? avatarId : defaultAvatarId;

        AvatarAgent agent = AvatarAgent.builder()
                .config(AvatarAgentConfig.builder()
                        .apiKey(apiKey)
                        .avatarId(effectiveAvatarId)
                        .baseUrl(baseUrl)
                        .sandbox(sandbox)
                        .developerAsr(developerAsr)
                        .developerTts(developerTts)
                        .voiceId(voiceId != null && !voiceId.isEmpty() ? voiceId : null)
                        .reconnectEnabled(true)
                        .build())
                .listener(this)
                .build();

        log("🚀 POST /v1/session/start avatarId=" + effectiveAvatarId);
        SessionInfo info = agent.start();
        sessions.put(info.getSessionId(), agent);
        log("📋 sessionId: " + info.getSessionId());
        log("📋 sfuUrl: " + info.getSfuUrl());
        log("📋 agentWsUrl: " + info.getAgentWsUrl());
        log("📋 userToken prefix: " + info.getUserToken().substring(0, Math.min(30, info.getUserToken().length())) + "...");
        return info;
    }

    public void stopSession(String sessionId) {
        AvatarAgent agent = sessions.remove(sessionId);
        if (agent != null) {
            log("🛑 Stopping agent...");
            agent.stop();
            log("✅ Agent stopped");
        }
    }

    @PreDestroy
    public void shutdown() {
        sessions.values().forEach(AvatarAgent::stop);
        sessions.clear();
    }

    // ── AgentListener ───────────────────────────────────────────────────────

    @Override
    public void onSessionInit() {
        log("⬇️  session.init — avatar is active");
    }

    @Override
    public void onTextInput(String text, String requestId) {
        log("⬇️  input.text | requestId=" + requestId + " text=" + text);

        String reply = mockLlm.reply(text);
        log("🤖 Mock LLM reply: " + reply);

        AvatarAgent agent = findAgent();
        if (agent == null) return;

        try {
            agent.sendResponseChunk(requestId, reply, 0);
            agent.sendResponseDone(requestId);
            log("⬆️  response.chunk + response.done | requestId=" + requestId);
            log("✅ Response complete");
        } catch (Exception e) {
            log("❌ Failed to send response: " + e.getMessage());
        }
    }

    @Override
    public void onAudioFrame(AudioFrame frame) {
        log("⬇️  [binary audio] | seq=" + frame.getHeader().getSequence()
                + " size=" + frame.getPayload().length + "B");
    }

    @Override
    public void onSessionState(SessionState state) {
        log("⬇️  session.state | state=" + state.getValue());
    }

    @Override
    public void onIdleTrigger(String reason, long idleMs) {
        log("⬇️  system.idleTrigger | reason=" + reason + " idleMs=" + idleMs);
        AvatarAgent agent = findAgent();
        if (agent != null) {
            try {
                agent.sendPrompt("你好，有什么想聊的吗？");
            } catch (Exception e) {
                log("❌ Failed to send prompt: " + e.getMessage());
            }
        }
    }

    @Override
    public void onSessionClosing(String reason) {
        log("⬇️  session.closing | reason=" + reason);
    }

    @Override
    public void onError(String message) {
        log("⬇️  error | " + message);
    }

    @Override
    public void onClosed(int code, String reason) {
        log("🔌 WebSocket closed | code=" + code + " reason=" + reason);
    }

    // ── Internal ────────────────────────────────────────────────────────────

    private AvatarAgent findAgent() {
        if (sessions.size() == 1) {
            return sessions.values().iterator().next();
        }
        // For demo, return the most recently created
        AvatarAgent result = null;
        for (AvatarAgent a : sessions.values()) {
            result = a;
        }
        return result;
    }

    private void log(String msg) {
        String line = "[" + Instant.now().toString().substring(11, 19) + "] " + msg;
        sharedLogs.add(line);
        if (sharedLogs.size() > MAX_LOG_LINES) {
            sharedLogs.remove(0);
        }
        log.info(msg);
    }
}
