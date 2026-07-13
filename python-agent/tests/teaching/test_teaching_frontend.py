"""Static checks for teaching.html interaction details."""

from pathlib import Path


FRONTEND_HTML = Path(__file__).parents[3] / "frontend" / "teaching.html"
WHITEBOARD_DIR = Path(__file__).parents[3] / "frontend" / "teaching" / "whiteboard"
CLOTHING_COURSE = (
    Path(__file__).parents[2]
    / "config"
    / "courses"
    / "神秘服装店的订单侦探小队的颜色与口袋大冒险_7-8.yaml"
)
BRAINWAVE_COURSE = (
    Path(__file__).parents[2]
    / "config"
    / "courses"
    / "小AI的脑波赛车大冒险_7-8.yaml"
)
BIO_BUILDER_COURSE = (
    Path(__file__).parents[2]
    / "config"
    / "courses"
    / "疯狂的合成生物拼图小象_7-8.yaml"
)
SKY_SIGNAL_COURSE = (
    Path(__file__).parents[2]
    / "config"
    / "courses"
    / "小侦探和天空桥梁信号大救援_7-8.yaml"
)


def _html() -> str:
    return FRONTEND_HTML.read_text(encoding="utf-8")


def _whiteboard_file(name: str) -> str:
    return (WHITEBOARD_DIR / name).read_text(encoding="utf-8")


def _clothing_course() -> str:
    return CLOTHING_COURSE.read_text(encoding="utf-8")


def _brainwave_course() -> str:
    return BRAINWAVE_COURSE.read_text(encoding="utf-8")


def _bio_builder_course() -> str:
    return BIO_BUILDER_COURSE.read_text(encoding="utf-8")


def _sky_signal_course() -> str:
    return SKY_SIGNAL_COURSE.read_text(encoding="utf-8")


def test_frontend_audio_sample_rate_matches_dashscope_asr():
    assert "sampleRate:16000" in _html()


def test_voice_input_waits_until_capture_is_ready_before_prompting_speech():
    html = _html()

    assert "正在打开麦克风" in html
    assert "可以开始说了" in html
    assert html.index("await client.startAudioCapture();") < html.index("可以开始说了")


def test_live_caption_wraps_to_two_lines_with_ellipsis():
    html = _html()

    assert ".live-caption{display:flex;align-items:flex-start" in html
    assert "live-caption-wrap" in html
    assert "-webkit-line-clamp:2" in html


def test_voice_input_raises_hand_before_starting_capture_to_interrupt_lecture():
    html = _html()

    start = html.index("async function startMic")
    end = html.index("async function stopMic", start)
    block = html[start:end]

    assert "await fetch('/api/teaching/raise-hand', {method:'POST'});" in block
    assert block.index("await fetch('/api/teaching/raise-hand'") < block.index(
        "await client.startAudioCapture();"
    )


def test_sdk_disconnected_stops_open_microphone_capture():
    html = _html()

    start = html.index("client.events.on('sdk:disconnected'")
    end = html.index("});", start)
    block = html[start:end]

    assert "await stopMic(false);" in block
    assert block.index("await stopMic(false);") < block.index("setMic(false, null);")


def test_frontend_has_interactive_scene_runtime_primitives():
    html = _html()
    index_js = _whiteboard_file("index.js")

    assert '<script type="module" src="./teaching/whiteboard/index.js"></script>' in html
    assert "interactiveSceneRegistry" not in html
    assert "mirror_transform" in index_js
    assert "cut_fold_unfold" in index_js
    assert "attribute_sort" in index_js
    assert "renderAttributeSort" not in html
    assert "renderInteractiveScene" in html
    assert "c.type === 'interactive_scene'" in html
    assert "window.renderInteractiveScene = renderInteractiveScene;" in index_js


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
    assert "brainwave-kid" in module
    assert "brainwave-car-body" in module
    assert ".brainwave-stage" in styles
    assert ".brainwave-kid" in styles
    assert ".brainwave-kid-helmet" in styles
    assert ".brainwave-track" in styles
    assert ".brainwave-car-body" in styles
    assert ".brainwave-track::after{content:\"终点\";position:absolute;right:28px" in styles
    assert "white-space:nowrap" in styles


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


def test_bio_builder_lab_primitive_is_registered_and_course_uses_it():
    index_js = _whiteboard_file("index.js")
    module = _whiteboard_file("bio-builder-lab.js")
    styles = _whiteboard_file("styles.css")
    course = _bio_builder_course()

    assert "renderBioBuilderLab" in index_js
    assert "bio_builder_lab: renderBioBuilderLab" in index_js
    assert "export function renderBioBuilderLab(scene)" in module
    assert "props.mode || 'module'" in module
    assert "renderModuleMode" in module
    assert "renderCombineMode" in module
    assert "renderRescueMode" in module
    assert "bio-lab-scientist" in module
    assert "bio-lab-elephant" in module
    assert ".bio-lab-stage" in styles
    assert ".bio-lab-gene-chip" in styles
    assert ".bio-lab-pod" in styles
    assert "primitive: bio_builder_lab" in course
    assert "mode: module" in course
    assert "mode: combine" in course
    assert "mode: rescue" in course
    assert "primitive: drag_to_animal" not in course
    assert "primitive: logic_grid" not in course
    assert "primitive: match_pairs" not in course
    assert "primitive: pattern_sequence" not in course
    assert "primitive: bar_chart" not in course


def test_sky_signal_rescue_primitive_is_registered_and_course_uses_it():
    index_js = _whiteboard_file("index.js")
    module = _whiteboard_file("sky-signal-rescue.js")
    styles = _whiteboard_file("styles.css")
    course = _sky_signal_course()

    assert "renderSkySignalRescue" in index_js
    assert "sky_signal_rescue: renderSkySignalRescue" in index_js
    assert "export function renderSkySignalRescue(scene)" in module
    assert "props.mode || 'blocked'" in module
    assert "renderBlockedMode" in module
    assert "renderRelayMode" in module
    assert "renderMeshMode" in module
    assert "sky-signal-detective" in module
    assert "sky-signal-satellite" in module
    assert "sky-signal-node-icon" in module
    assert "sky-signal-beam segment-a" in module
    assert "sky-signal-beam segment-b" in module
    assert "sky-signal-relay-a" in module
    assert "sky-signal-relay-b" in module
    assert ".sky-signal-stage" in styles
    assert ".sky-signal-beam" in styles
    assert ".sky-signal-relay-a .segment-a" in styles
    assert ".sky-signal-relay-b .segment-b" in styles
    assert ".sky-signal-node-icon" in styles
    assert ".sky-signal-satellite" in styles
    assert "primitive: sky_signal_rescue" in course
    assert "mode: blocked" in course
    assert "mode: relay" in course
    assert "mode: mesh" in course
    assert "primitive: bar_chart" not in course
    assert "primitive: grid_explorer" not in course
    assert "primitive: drag_to_animal" not in course
    assert "primitive: logic_grid" not in course
    assert "primitive: sort_order" not in course


def test_mirror_transform_supports_letter_mode_for_letter_a_step():
    module = _whiteboard_file("mirror-transform.js")
    course = (Path(__file__).parents[2] / "config" / "courses" / "怪兽镜子大冒险_7-8.yaml").read_text(encoding="utf-8")

    assert "function letterElement" in module
    assert "renderMirrorLetters" in module
    assert "props.mode === 'letter'" in module
    assert ".letter-symbol.mirror" in _whiteboard_file("styles.css")
    assert "mode: letter" in course
    assert "letter: A" in course


def test_cut_fold_scene_draws_shapes_instead_of_chinese_text():
    module = _whiteboard_file("cut-fold-unfold.js")
    styles = _whiteboard_file("styles.css")

    assert "shapeMap = {heart: '心', star: '星'" not in module
    assert "cut.classList.add('cut-' + shape);" in module
    assert ".cut-shape.cut-heart" in styles
    assert ".cut-shape.cut-star" in styles


def test_cut_fold_scene_shows_half_cut_before_unfolding():
    module = _whiteboard_file("cut-fold-unfold.js")
    styles = _whiteboard_file("styles.css")

    assert "cut.classList.add(isOpen ? 'cut-open' : 'cut-half')" in module
    assert ".paper.folded .cut-half{left:100%" in styles
    assert ".paper.folded .cut-half{left:100%;clip-path:inset" not in styles
    assert ".cut-half.cut-heart::after" in styles
    assert ".cut-half.cut-star{clip-path:polygon" in styles
    assert ".paper:not(.folded) .cut-open" in styles


def test_clothing_order_course_uses_attribute_sort_interactions():
    course = _clothing_course()

    assert "primitive: attribute_sort" in course
    assert "红色" in course
    assert "口袋" in course
    assert "target:" in course


def test_classmate_messages_do_not_render_inside_whiteboard():
    html = _html()

    board_markup = html[html.index('<div id="board-card"'):html.index('<div id="quiz-options"')]
    render_start = html.index("function renderClassmate")
    render_end = html.index("// Audio queue", render_start)
    render_classmate = html[render_start:render_end]

    assert "id=\"xiao-card\"" not in board_markup
    assert "xiaoCard" not in render_classmate
    assert "appendTranscript('classmate'" in render_classmate


def test_transcript_is_slide_over_drawer_and_voice_button_is_in_teacher_panel():
    html = _html()

    teacher_controls = html[html.index('<div class="teacher-controls"'):html.index('</main>')]
    drawer_start = html.index('<aside id="transcript-drawer"')
    drawer = html[drawer_start:html.index('</aside>', drawer_start)]

    assert "id=\"btn-voice\"" in teacher_controls
    assert "id=\"transcript-drawer\"" in html
    assert "transcript-toggle" in html
    assert ".transcript-drawer.open" in html
    assert "transform:translateX(100%)" in html
    assert "id=\"btn-voice\"" not in drawer


def test_classmate_audio_stops_when_voice_input_starts():
    html = _html()

    assert "let _currentClassmateAudio" in html
    assert "function stopClassmateAudio()" in html
    assert html.index("stopClassmateAudio();") < html.index("await client.startAudioCapture();")


def test_classmate_audio_stop_does_not_leave_queue_worker_stuck():
    html = _html()

    assert "let _audioGeneration" in html
    assert "audio.onpause = resolve" in html
    assert "_audioGeneration += 1" in html


def test_play_audio_stop_component_stops_instead_of_playing():
    html = _html()

    assert "if (c.action === 'stop') stopClassmateAudio();" in html
    assert "else playAudio(c.data);" in html


def test_chapter_indicator_component_updates_title_and_step_dots():
    html = _html()

    assert html.count("function handleComponent") == 1

    start = html.rindex("function handleComponent")
    end = html.index("btnConnect.onclick", start)
    block = html[start:end]

    assert "c.type === 'chapter_indicator'" in block
    assert "chapterTitleEl.textContent = c.data.title" in block
    assert "currentChapterData = {id:c.data.chapter_id, title:c.data.title || ''}" in block
    assert "renderChapterSteps(c.data.chapter_index, c.data.total_chapters)" in block


def test_classmate_message_log_uses_preserved_speaker_name():
    html = _html()

    assert "m.name || '小明'" in html
    assert "setLive(" in html
    assert "appendTranscript('classmate', m.text" in html
    assert "appendTranscript('classmate', m.text, '小明')" not in html


def test_classmate_transcript_dedupe_ignores_speaker_for_same_text():
    html = _html()

    assert "function transcriptKey(role, text, name)" in html
    assert "if (role === 'classmate')" in html
    assert "return role + ':' + normalizeTranscriptText(text);" in html
    assert "role + ':' + name + ':' + text" not in html


def test_whiteboard_highlight_uses_displayed_step_not_backend_cursor():
    html = _html()

    assert "let displayedWhiteboardIndex = null;" in html
    assert "displayedWhiteboardIndex = data.step_num - 1;" in html
    assert "displayedWhiteboardIndex ?? 0" in html or "displayedWhiteboardIndex ?? (s.currentSkeletonIndex || 0)" in html


def test_ws_state_sync_renders_chapter_content():
    html = _html()

    # state_sync handler renders chapter content before processing components
    assert 'if (m.type === \'state_sync\')' in html
    assert "state_sync" in html
    ws_start = html.index("function connectWs()")
    ws_end = html.index("function scheduleWsReconnect", ws_start)
    ws_block = html[ws_start:ws_end]
    assert ws_block.index("renderChapterContent(") < ws_block.index("} else if (m.type === 'component')")


def test_frontend_normalizes_object_skeleton_items():
    html = _html()

    assert "function skeletonText(item)" in html
    assert "body.textContent = skeletonText(text);" in html
    assert "item.content ||" not in html


def test_voice_input_is_compact_icon_with_muted_and_wave_states():
    html = _html()

    assert "class=\"btn voice-btn mic-icon muted\"" in html
    assert "aria-label=\"语音输入\"" in html
    assert "mic-wave" in html
    assert ".voice-btn{width:48px" in html
    assert ".voice-btn.recording .mic-wave" in html
    assert "btnVoice.textContent" not in html


def test_disconnect_stops_classmate_audio():
    html = _html()

    disconnect_start = html.index("btnDisconnect.onclick")
    disconnect_end = html.index("};", disconnect_start)
    disconnect_block = html[disconnect_start:disconnect_end]

    assert "stopClassmateAudio();" in disconnect_block


def test_new_connection_clears_transcript_state():
    html = _html()

    assert "function resetSessionState()" in html
    connect_start = html.index("btnConnect.onclick")
    connect_end = html.index("client = LivekitSDK.createClient", connect_start)
    connect_block = html[connect_start:connect_end]

    assert "resetSessionState();" in connect_block
    assert "transcriptSeen.clear()" in html


def test_board_reset_clears_cached_chapter_dataset():
    html = _html()

    reset_start = html.index("function resetBoard()")
    reset_end = html.index("function setBoard", reset_start)
    reset_block = html[reset_start:reset_end]

    assert "delete whiteSteps.dataset.chapter" in reset_block
    assert "delete whiteSteps.dataset.total" in reset_block


def test_interaction_prompt_preserves_animation_and_does_not_highlight_lesson_step():
    html = _html()

    interaction_start = html.index("function renderInteraction")
    interaction_end = html.index("function renderQuiz", interaction_start)
    interaction_block = html[interaction_start:interaction_end]

    assert "resetBoard();" not in interaction_block
    assert "renderChapterContent(currentChapterData, -1)" in interaction_block
    assert "interactiveSceneEl.hidden = true" not in interaction_block
    assert "boardCard.classList.add('interaction-turn')" in interaction_block
    assert "setBoard('轮到你回答啦'" in interaction_block
    assert "点蓝色麦克风" in interaction_block
    assert "等待你的答案" in interaction_block


def test_interaction_state_highlights_voice_button():
    html = _html()

    assert ".board-card.interaction-turn" in html
    assert ".voice-btn.interaction-ready" in html
    assert "btnVoice.classList.toggle('interaction-ready', Boolean(currentInteraction) && !micOn);" in html


def test_stopping_interaction_mic_clears_interaction_ready_state():
    html = _html()

    stop_start = html.index("async function stopMic")
    stop_end = html.index("btnVoice.onclick", stop_start)
    stop_block = html[stop_start:stop_end]

    assert "const wasInteraction = micMode === 'interaction';" in stop_block
    assert "if (wasInteraction) currentInteraction = null;" in stop_block
    assert stop_block.index("if (wasInteraction) currentInteraction = null;") < stop_block.index("setMic(false, null);")


def test_state_sync_renders_skeleton_only_when_no_interaction():
    html = _html()

    # In state_sync handler, chapter skeleton renders only when no interaction is active
    ws_start = html.index("function connectWs()")
    ws_end = html.index("function scheduleWsReconnect", ws_start)
    ws_block = html[ws_start:ws_end]
    assert "renderChapterContent(" in ws_block


def test_course_end_status_enables_reconnect_controls():
    html = _html()

    assert "function markCourseEnded()" in html
    assert "if (m.courseEnded)" in html or "if (s.courseEnded) {" in html
    assert "markCourseEnded();" in html


def test_course_picker_disabled_during_active_lesson():
    html = _html()

    assert "function setCoursePickerEnabled(enabled)" in html
    assert "btnCourses.disabled = !enabled" in html
    assert "setCoursePickerEnabled(false);" in html
    assert "setCoursePickerEnabled(true);" in html

    connected_start = html.index("client.events.on('sdk:connected'")
    connected_end = html.index("});", connected_start)
    connected_block = html[connected_start:connected_end]
    assert "setCoursePickerEnabled(false);" in connected_block

    ended_start = html.index("async function markCourseEnded")
    ended_end = html.index("function resetBoard", ended_start)
    ended_block = html[ended_start:ended_end]
    assert "setCoursePickerEnabled(true);" in ended_block
