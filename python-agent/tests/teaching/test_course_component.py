"""Tests for course_component.py — component message dataclass."""

import json
import time
import pytest
from teaching.course_component import ComponentMessage, COMPONENT_TYPES


class TestComponentMessage:
    def test_create_visual_card_message(self):
        msg = ComponentMessage(
            type="visual_card",
            action="show",
            data={"id": "what_is_logic", "title": "火眼金睛", "content": "学会判断对错"},
            timestamp=1718352000000,
        )
        assert msg.type == "visual_card"
        assert msg.action == "show"
        assert msg.data["id"] == "what_is_logic"
        assert msg.timestamp == 1718352000000

    def test_create_quiz_message(self):
        msg = ComponentMessage(
            type="quiz",
            action="show",
            data={
                "question": "小明掉进了哪个小陷阱？",
                "options": [
                    {"key": "A", "text": "「大家都这样」陷阱 🐑"},
                    {"key": "B", "text": "「大人说的都对」陷阱 👨‍🏫"},
                ],
                "chapter_id": "fallacy_types",
            },
            timestamp=1718352000000,
        )
        assert msg.type == "quiz"
        assert len(msg.data["options"]) == 2

    def test_create_encouragement_message(self):
        msg = ComponentMessage(
            type="encouragement",
            action="show",
            data={"text": "太棒了！🌟", "style": "star"},
            timestamp=1718352000000,
        )
        assert msg.type == "encouragement"
        assert msg.data["style"] == "star"

    def test_create_raise_hand_message(self):
        msg = ComponentMessage(
            type="raise_hand",
            action="update",
            data={"enabled": False},
            timestamp=1718352000000,
        )
        assert msg.type == "raise_hand"
        assert msg.data["enabled"] is False

    def test_all_component_types_defined(self):
        expected = {
            "visual_card", "mindmap", "quiz", "quiz_result",
            "raise_hand", "interaction_prompt", "chapter_indicator",
            "lecture_progress", "encouragement",
            "whiteboard_step", "whiteboard_compare",
            "classmate_message", "microphone",
            "interactive_scene",
        }
        assert expected.issubset(set(COMPONENT_TYPES))

    def test_interactive_scene_component(self):
        msg = ComponentMessage(
            type="interactive_scene",
            action="show",
            data={
                "primitive": "mirror_transform",
                "title": "镜子实验",
                "prompt": "点一点右手",
                "props": {"rule": "horizontal_flip"},
            },
            timestamp=1718352000000,
        )
        assert msg.data["primitive"] == "mirror_transform"
        assert msg.data["props"]["rule"] == "horizontal_flip"

    def test_message_serializable_to_json(self):
        msg = ComponentMessage(
            type="quiz_result",
            action="show",
            data={"correct": True, "explanation": "答对了！"},
            timestamp=1718352000000,
        )
        d = {"type": msg.type, "action": msg.action, "data": msg.data, "timestamp": msg.timestamp}
        serialized = json.dumps(d, ensure_ascii=False)
        assert "quiz_result" in serialized
        assert "答对了" in serialized

    def test_mindmap_component(self):
        msg = ComponentMessage(
            type="mindmap",
            action="show",
            data={"id": "fallacy_types", "title": "逻辑小陷阱", "image_url": "/assets/mindmap.png"},
            timestamp=1718352000000,
        )
        assert msg.data["image_url"] == "/assets/mindmap.png"

    def test_chapter_indicator_component(self):
        msg = ComponentMessage(
            type="chapter_indicator",
            action="show",
            data={"current": 1, "total": 3, "title": "小故事"},
            timestamp=1718352000000,
        )
        assert msg.data["current"] == 1
        assert msg.data["total"] == 3

    def test_lecture_progress_component(self):
        msg = ComponentMessage(
            type="lecture_progress",
            action="update",
            data={"segment_current": 2, "segment_total": 5},
            timestamp=1718352000000,
        )
        assert msg.data["segment_current"] == 2

    def test_interaction_prompt_component(self):
        msg = ComponentMessage(
            type="interaction_prompt",
            action="show",
            data={"text": "你会跟着一起跑吗？", "chapter_id": "intro"},
            timestamp=1718352000000,
        )
        assert msg.data["chapter_id"] == "intro"
