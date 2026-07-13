#!/usr/bin/env python3
"""Batch-generate 30 courses (10 per age stage) for the thinking curriculum.

Usage:
  export DEEPSEEK_API_KEY=sk-xxx
  python batch_generate_courses.py              # generate all 30
  python batch_generate_courses.py --stage 1    # only stage 1 (4-6岁)
  python batch_generate_courses.py --dry-run    # print topics only, don't generate
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from llm_client import LlmClient
from teaching.course_generator import CourseGenerator

# ---------------------------------------------------------------------------
# 30-course curriculum — organized by developmental stage
# ---------------------------------------------------------------------------

COURSES = {
    "4-6": {
        "stage": "阶段一：启蒙期 — 具象游戏与感官思维",
        "interaction_style": "动作夸张、语速慢、语调高、屏幕充满可拖拽的彩色方块和小动物",
        "topics": [
            # (topic_id, topic_title, core_ability)
            ("01", "小动物排排队", "一一对应与数感 — 把实物一对一拖给动物，理解数量对应"),
            ("02", "糖果色风暴",   "单维分类 — 按颜色/形状把玩具放进正确的框里"),
            ("03", "啪嗒啪嗒跳个舞", "视觉模式识别 — 踏脚-拍手规律，选出下一个魔方颜色"),
            ("04", "影子连连看",   "平面空间感知 — 通过轮廓把动物和影子连线匹配"),
            ("05", "变多还是变少", "数量守恒启蒙 — 两排糖果拉长后真的变多了吗？"),
            ("06", "小树长高了",   "比较与序列 — 给三棵高矮不同的小树贴1-2-3标签"),
            ("07", "找出捣蛋鬼",   "基础排除法 — 4只大象里混进1只兔子，找出异类"),
            ("08", "神奇魔法袋",   "触觉与形状联想 — 摸到三角尖尖的东西，猜是什么形状"),
            ("09", "分一分大西瓜", "二等分感知 — 在屏幕上画线把西瓜切成一样大的两半"),
            ("10", "小老鼠走迷宫", "方向与初级路径 — 点击上下左右箭头指挥老鼠吃奶酪"),
        ],
    },
    "7-8": {
        "stage": "阶段二：过渡期 — 故事线索与形象逻辑",
        "interaction_style": "小AI化身侦探伙伴，语速正常，丰富面部表情，启发式提问",
        "topics": [
            ("01", "密码破译墙",     "符号化思维与等量代换 — 两个星星加起来是6，一个星星是几？"),
            ("02", "神秘服装店的订单", "多维分类 — 找一件红色且带口袋的衣服（韦恩图思想）"),
            ("03", "消失的第100个方块", "周期规律与数学推导 — 红蓝绿循环，第20个是什么颜色？"),
            ("04", "谁偷了魔法药水",   "逻辑演绎与排除法 — 三个嫌疑人各说一句话，谁在说谎？"),
            ("05", "城堡后面的隐形方块", "3D空间想象与计数 — 积木堆叠，大白熊脚下藏了几个？"),
            ("06", "怪兽镜子大冒险",   "镜像与轴对称 — 镜子里的影子举哪只手？剪纸打开什么样？"),
            ("07", "天平两端的小秘密", "逆向思维与初级代数 — 从苹果换橘子推到菠萝换橘子"),
            ("08", "时间小管家的金币", "时间感知与统筹 — 三件事怎么安排用时最少？"),
            ("09", "火柴棒变变变",     "空间重构与发散思维 — 移动一根火柴把6变成0"),
            ("10", "小小社区统计员",   "数据收集与图表 — 涂格子做柱状图，哪个最多？多几个？"),
        ],
    },
    "9-10": {
        "stage": "阶段三：跃迁期 — 智力PK与抽象策略",
        "interaction_style": "小AI语速较快、眼神坚定、语气具挑战性，语音阐述+虚拟草稿纸+追问功能",
        "topics": [
            ("01", "数字迷宫与最佳路径",   "图论与算法优化 — 外卖员走4个地方，规划总用时最短路径"),
            ("02", "抢30的必胜法宝",      "博弈论与逆向推导 — 轮流数1-2个数，谁先到30谁赢，找必胜策略点"),
            ("03", "鸡兔同笼的N种解法",   "多角度建模思维 — 假设法→列表法→图形法，一个问题多种模型"),
            ("04", "折纸能触及月球吗",    "指数爆炸与几何递增 — 0.1mm纸对折30次有多厚？打破线性思维"),
            ("05", "不可能的裁剪",        "拓扑学与空间逆向 — A4纸中间剪洞，怎么让整个人穿过去？"),
            ("06", "狼羊菜的现代版危机",  "条件限制与状态机 — 三件危险品两两相克，建立状态转移图"),
            ("07", "假币风波",           "二分法与称重逻辑 — 8个金币1个假币，最少称几次保证找出？"),
            ("08", "天气预报背后的秘密",  "概率与不确定性 — 降水概率80%就一定下雨吗？理解风险评估"),
            ("09", "工程队的效率大战",    "归一与复合逻辑 — 两队速度不同，一起修路要几天？"),
            ("10", "逻辑悖论：说谎的长鼻子", "批判性思维 — 「这句话是假话」到底真还是假？罗素悖论儿童版"),
        ],
    },
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-generate thinking courses")
    parser.add_argument("--stage", type=str, choices=["1", "2", "3"],
                        help="Only generate one stage (1=4-6岁, 2=7-8岁, 3=9-10岁)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print topics without generating")
    parser.add_argument("--start-at", type=int, default=0,
                        help="Skip first N topics (for resume)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key and not args.dry_run:
        print("❌ DEEPSEEK_API_KEY not set. Export it first:")
        print("   export DEEPSEEK_API_KEY=sk-xxx")
        sys.exit(1)

    age_stages = [("4-6", "1"), ("7-8", "2"), ("9-10", "3")]
    if args.stage:
        idx = int(args.stage) - 1
        age_stages = [age_stages[idx]]

    if args.dry_run:
        for age, stage_num in age_stages:
            info = COURSES[age]
            print(f"\n{'='*60}")
            print(f"  {info['stage']}  (age={age})")
            print(f"  交互策略：{info['interaction_style']}")
            print(f"{'='*60}")
            for tid, title, ability in info["topics"]:
                print(f"  [{age}] 主题{tid}：{title}")
                print(f"          {ability}")
            print()
        return

    # Init generator
    llm = LlmClient(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    )
    output_dir = Path(__file__).parent / "config" / "courses"
    generator = CourseGenerator(llm, output_dir)

    total_generated = 0
    total_skipped = 0

    for age, stage_num in age_stages:
        info = COURSES[age]
        logger.info("=" * 50)
        logger.info("🚀 %s (age=%s)", info["stage"], age)
        logger.info("=" * 50)

        for i, (tid, title, ability) in enumerate(info["topics"]):
            if i < args.start_at and age == age_stages[0][0]:
                logger.info("⏭  Skipping [%s] 主题%s：%s (--start-at=%d)", age, tid, title, args.start_at)
                total_skipped += 1
                continue

            topic_full = f"{title}（{ability}）"
            logger.info("📝 [%s] 主题%s：%s", age, tid, title)

            # Check if already generated
            slug = generator._slugify(title)
            expected_file = output_dir / f"{slug}_{age}.yaml"
            if expected_file.exists():
                logger.info("✅ Already exists: %s", expected_file.name)
                total_skipped += 1
                continue

            try:
                result = await generator.generate(topic=topic_full, age=age)
                logger.info("✅ [%s/%s] Generated: %s (%d chapters)",
                            age, tid, result["course_name"], result["chapters"])
                total_generated += 1
            except Exception as e:
                logger.error("❌ [%s/%s] Failed: %s — %s", age, tid, title, e)
                # Continue to next topic — don't abort the entire batch

    logger.info("=" * 50)
    logger.info("🏁 Done. Generated: %d, Skipped: %d, Total: %d",
                total_generated, total_skipped, total_generated + total_skipped)


if __name__ == "__main__":
    asyncio.run(main())
