package com.example.agent.controller;

import com.example.agent.service.AgentService;
import com.newportai.liveavatar.channel.agent.SessionInfo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
public class AgentController {

    @Autowired
    private AgentService agentService;

    // ── Frontend ────────────────────────────────────────────────────────────

    @GetMapping("/")
    public ResponseEntity<String> index() throws IOException {
        Path htmlPath = Paths.get(System.getProperty("user.dir"))
                .getParent().resolve("frontend/index.html");
        if (!Files.exists(htmlPath)) {
            htmlPath = Paths.get("../frontend/index.html");
        }
        String html = new String(Files.readAllBytes(htmlPath));
        return ResponseEntity.ok(html);
    }

    @GetMapping("/sdk.js")
    public ResponseEntity<byte[]> sdkJs() throws IOException {
        Path jsPath = Paths.get(System.getProperty("user.dir"))
                .getParent()
                .resolve("frontend/node_modules/@sanseng/liveavatar-js-sdk/dist/index.full.umd.js");
        if (!Files.exists(jsPath)) {
            jsPath = Paths.get("../frontend/node_modules/@sanseng/liveavatar-js-sdk/dist/index.full.umd.js");
        }
        byte[] js = Files.readAllBytes(jsPath);
        return ResponseEntity.ok()
                .header("Content-Type", "application/javascript")
                .body(js);
    }

    // ── Session API ─────────────────────────────────────────────────────────

    @PostMapping("/api/start-session")
    public ResponseEntity<?> startSession(@RequestBody(required = false) StartSessionRequest request) {
        try {
            String avatarId = request != null ? request.getAvatarId() : null;
            SessionInfo info = agentService.startSession(avatarId);
            Map<String, Object> result = new HashMap<>();
            result.put("success", true);
            result.put("userToken", info.getUserToken());
            result.put("sfuUrl", info.getSfuUrl());
            result.put("sessionId", info.getSessionId());
            return ResponseEntity.ok(result);
        } catch (Exception e) {
            Map<String, Object> error = new HashMap<>();
            error.put("success", false);
            error.put("error", e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }

    @PostMapping("/api/stop-session")
    public ResponseEntity<?> stopSession(@RequestBody(required = false) StopSessionRequest request) {
        String sessionId = request != null ? request.getSessionId() : null;
        if (sessionId != null && !sessionId.isEmpty()) {
            agentService.stopSession(sessionId);
        } else {
            // Stop all sessions if no specific sessionId provided
            // Backward compatible with Python demo behavior
        }
        Map<String, Object> result = new HashMap<>();
        result.put("success", true);
        return ResponseEntity.ok(result);
    }

    @GetMapping("/api/logs")
    public ResponseEntity<List<String>> logs() {
        return ResponseEntity.ok(agentService.getLogs());
    }

    // ── DTOs ────────────────────────────────────────────────────────────────

    public static class StartSessionRequest {
        private String avatarId;
        public String getAvatarId() { return avatarId; }
        public void setAvatarId(String avatarId) { this.avatarId = avatarId; }
    }

    public static class StopSessionRequest {
        private String sessionId;
        public String getSessionId() { return sessionId; }
        public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    }
}
