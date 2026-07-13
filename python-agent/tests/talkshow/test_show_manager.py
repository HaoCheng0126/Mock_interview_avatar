from pathlib import Path

import yaml

from talkshow.show_manager import Segment, ShowBatch, ShowManager


SAMPLE_YAML = """
settings:
  loop: true
  lang: zh
  batch_size: 6
  regenerate_at_ratio: 0.75
  opening_enabled: true
  idle_timeout_s: 30

persona:
  name: "阿麦"
  style: "观察生活，轻微自嘲，节奏快，不攻击观众。"
  boundaries:
    - "不讲政治"
    - "不讲低俗黄色内容"

show:
  title: "今晚不加班"
  opening: "大家好，欢迎来到今晚不加班。"

topics:
  - id: "workplace"
    title: "职场日常"
    description: "会议、加班、摸鱼、老板画饼。"

fallback_segments:
  - topic_id: "workplace"
    title: "备用段子"
    text: "我一直觉得，会议不是为了解决问题，会议是为了确认这个问题确实存在。"
"""


def test_loads_talkshow_config(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")

    manager = ShowManager(config_path)

    assert manager.settings["batch_size"] == 6
    assert manager.settings["opening_enabled"] is True
    assert manager.persona.name == "阿麦"
    assert manager.persona.boundaries == ["不讲政治", "不讲低俗黄色内容"]
    assert manager.show.title == "今晚不加班"
    assert manager.show.opening == "大家好，欢迎来到今晚不加班。"
    assert manager.get_topics()[0].id == "workplace"
    assert manager.get_fallback_segments()[0].title == "备用段子"


def test_defaults_do_not_require_video(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(
        """
persona:
  name: "阿麦"
show:
  title: "今晚不加班"
topics:
  - id: "workplace"
    title: "职场日常"
""",
        encoding="utf-8",
    )

    manager = ShowManager(config_path)

    assert manager.settings["loop"] is True
    assert manager.settings["lang"] == "zh"
    assert manager.settings["batch_size"] == 6
    assert manager.settings["regenerate_at_ratio"] == 0.75
    assert manager.settings["opening_enabled"] is True
    assert "default_loop_video" not in manager.settings


def test_loads_voice_config(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(
        SAMPLE_YAML
        + """
voice:
  voice_config:
    volume: 70
    speed: 1.08
    pitch: 1.03
""",
        encoding="utf-8",
    )

    manager = ShowManager(config_path)

    assert manager.voice_config == {"volume": 70, "speed": 1.08, "pitch": 1.03}


def test_reload_updates_topics(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")
    manager = ShowManager(config_path)
    assert len(manager.get_topics()) == 1

    config_path.write_text(
        SAMPLE_YAML.replace(
            '  - id: "workplace"\n'
            '    title: "职场日常"\n'
            '    description: "会议、加班、摸鱼、老板画饼。"\n',
            '  - id: "workplace"\n'
            '    title: "职场日常"\n'
            '    description: "会议、加班、摸鱼、老板画饼。"\n'
            '  - id: "city_life"\n'
            '    title: "城市生活"\n'
            '    description: "通勤、租房、外卖。"\n',
        ),
        encoding="utf-8",
    )

    manager.reload()
    assert [topic.id for topic in manager.get_topics()] == ["workplace", "city_life"]


def test_loads_seed_batch(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(
        SAMPLE_YAML
        + """
seed_batch:
  batch_title: "冷启动节目"
  segments:
    - topic_id: "workplace"
      title: "会议室里的时间黑洞"
      beats:
        - "会议迟迟不开始"
      text: "会议室有一种特殊的物理规则，只要门一关，时间就开始打折。"
    - topic_id: "city_life"
      title: "地铁里的社交礼仪"
      text: "早高峰地铁是城市里最公平的地方。"
  bridges:
    - from_title: "会议室里的时间黑洞"
      to_title: "地铁里的社交礼仪"
      text: "说到时间被偷走，地铁也不甘示弱。"
""",
        encoding="utf-8",
    )

    manager = ShowManager(config_path)
    batch = manager.get_seed_batch()

    assert batch is not None
    assert batch.batch_title == "冷启动节目"
    assert [segment.title for segment in batch.segments] == [
        "会议室里的时间黑洞",
        "地铁里的社交礼仪",
    ]
    assert batch.bridges[0].text == "说到时间被偷走，地铁也不甘示弱。"


def test_save_seed_batch_persists_last_generated_batch(tmp_path: Path):
    config_path = tmp_path / "talkshow.yaml"
    config_path.write_text(SAMPLE_YAML, encoding="utf-8")
    manager = ShowManager(config_path)
    batch = ShowBatch(
        batch_title="新生成节目",
        segments=[
            Segment(
                topic_id="workplace",
                title="新的会议段子",
                text="新的会议段子正文。",
                beats=["新角度"],
            )
        ],
        bridges=[],
    )

    manager.save_seed_batch(batch)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["seed_batch"]["batch_title"] == "新生成节目"
    assert saved["seed_batch"]["segments"][0]["title"] == "新的会议段子"
    reloaded = ShowManager(config_path)
    assert reloaded.get_seed_batch().segments[0].title == "新的会议段子"
