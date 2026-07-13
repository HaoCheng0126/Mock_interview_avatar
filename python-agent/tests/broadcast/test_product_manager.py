import tempfile
from pathlib import Path
import pytest
from broadcast.product_manager import ProductManager


SAMPLE_YAML = """
settings:
  loop: true
  default_tts_speed: 1.0
  default_pause_ms: 3000
  chunk_delay_ms: 200
  default_loop_video: "res_video_bg"

products:
  - id: "prod_001"
    name: "Test Product"
    url: "https://example.com/product"
    loop_video: "res_video_bg"
    tts_speed: 1.2
    pause_after_script_ms: 5000
    video_scripts:
      - video: "res_video_a"
        scripts:
          - "Script A1"
          - "Script A2"
      - video: "res_video_b"
        scripts:
          - "Script B1"
  - id: "prod_002"
    name: "Minimal Product"
    video_scripts:
      - video: "res_video_c"
        scripts:
          - "Script C1"
"""


@pytest.fixture
def config_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        path = Path(f.name)
    yield path
    path.unlink()


def test_load_products(config_file):
    pm = ProductManager(config_file)
    products = pm.get_products()
    assert len(products) == 2
    assert products[0].id == "prod_001"
    assert products[0].name == "Test Product"
    assert products[0].tts_speed == 1.2
    assert products[0].pause_after_script_ms == 5000


def test_defaults_fallback(config_file):
    pm = ProductManager(config_file)
    prod2 = pm.get_products()[1]
    assert prod2.tts_speed == 1.0
    assert prod2.pause_after_script_ms == 3000
    assert prod2.loop_video == "res_video_bg"


def test_loads_generic_items_and_prompt_config(tmp_path):
    config = tmp_path / "crypto.yaml"
    config.write_text(
        """
settings:
  loop: true
  default_loop_video: "res_crypto_bg"
persona:
  system_prompt: "你是虚拟币行情解说主播，只做风险教育。"
prompts:
  script_system_prompt: "只输出JSON数组。"
  script_template: "围绕这个主题生成脚本：{product_info}"
  reply_template: "观众问：{text}\\n请做泛行情和风险教育回复。"
items:
  - id: "btc_risk"
    title: "BTC 波动风险"
    description: "解释高波动和仓位纪律。"
    loop_video: "res_crypto_bg"
    video_scripts:
      - video: "res_crypto_a"
        scripts:
          - "先说风险。"
""",
        encoding="utf-8",
    )

    pm = ProductManager(config)
    item = pm.get_products()[0]

    assert item.id == "btc_risk"
    assert item.name == "BTC 波动风险"
    assert item.description == "解释高波动和仓位纪律。"
    assert pm.get_persona_system_prompt() == "你是虚拟币行情解说主播，只做风险教育。"
    assert pm.get_prompt("script_template") == "围绕这个主题生成脚本：{product_info}"
    assert pm.format_prompt("reply_template", text="BTC 能买吗？") == (
        "观众问：BTC 能买吗？\n请做泛行情和风险教育回复。"
    )


def test_random_video_script(config_file):
    pm = ProductManager(config_file)
    video, script = pm.random_video_script("prod_001")
    assert video in ("res_video_a", "res_video_b")
    if video == "res_video_a":
        assert script in ("Script A1", "Script A2")
    else:
        assert script == "Script B1"


def test_random_video_script_unknown_product(config_file):
    pm = ProductManager(config_file)
    with pytest.raises(ValueError, match="Product not found"):
        pm.random_video_script("nonexistent")


def test_add_script(config_file):
    pm = ProductManager(config_file)
    pm.add_script("prod_002", "res_video_c", "Script C2")
    video, _ = pm.random_video_script("prod_002")
    assert video == "res_video_c"


def test_add_script_new_video(config_file):
    pm = ProductManager(config_file)
    pm.add_script("prod_001", "res_video_new", "New script")
    found = False
    for _ in range(50):
        video, script = pm.random_video_script("prod_001")
        if video == "res_video_new" and script == "New script":
            found = True
            break
    assert found


def test_validate_skips_product_without_video_scripts(config_file):
    pm = ProductManager(config_file)
    assert len(pm.get_products()) == 2


def test_reload(config_file):
    pm = ProductManager(config_file)
    assert len(pm.get_products()) == 2
    new_yaml = (
        SAMPLE_YAML
        + """
  - id: "prod_003"
    name: "Added Later"
    video_scripts:
      - video: "res_video_d"
        scripts:
          - "Script D1"
"""
    )
    config_file.write_text(new_yaml)
    pm.reload()
    assert len(pm.get_products()) == 3
    config_file.write_text(SAMPLE_YAML)
    pm.reload()
    assert len(pm.get_products()) == 2


def test_save_scripts_round_robin(config_file):
    pm = ProductManager(config_file)
    scripts = ["S1", "S2", "S3", "S4", "S5"]
    pm.save_scripts("prod_001", scripts)

    # prod_001 has 2 videos: res_video_a (idx 0) and res_video_b (idx 1)
    # Round-robin: S1→a, S2→b, S3→a, S4→b, S5→a
    # So video_a gets 3 scripts, video_b gets 2
    pm2 = ProductManager(config_file)  # reload fresh
    product = pm2.get_product("prod_001")
    vs_a = next(vs for vs in product.video_scripts if vs.video == "res_video_a")
    vs_b = next(vs for vs in product.video_scripts if vs.video == "res_video_b")

    assert vs_a.scripts == ["S1", "S3", "S5"]
    assert vs_b.scripts == ["S2", "S4"]


def test_save_scripts_unknown_product(config_file):
    pm = ProductManager(config_file)
    with pytest.raises(ValueError, match="Product not found"):
        pm.save_scripts("nonexistent", ["S1"])
