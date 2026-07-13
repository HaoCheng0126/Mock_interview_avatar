/**
 * E2E Test: Verify ALL interactive primitive components render correctly
 * in the 小思老师 teaching app at http://localhost:8082
 *
 * Usage: node e2e-test.mjs
 */

import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = join(__dirname, 'screenshots');
const REPORT_FILE = join(__dirname, 'test-report.json');

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const APP_URL = 'http://localhost:8082';

// Test configuration for EACH primitive type
// Each entry has: course name to select, test scene data to inject if needed
const PRIMITIVE_TESTS = [
  {
    primitive: 'bar_chart',
    className: 'barchart-stage',
    label: '柱状图 (bar_chart)',
    scene: {
      primitive: 'bar_chart',
      title: '谁的最多？',
      prompt: '数一数每个水果有几票',
      goal: '比较数量',
      props: {
        bars: [
          { label: '🥕 胡萝卜', value: 5, color: '#d95b59' },
          { label: '🥜 坚果', value: 7, color: '#f5c84b' },
          { label: '🐟 小鱼干', value: 3, color: '#4f8ed8' },
        ],
        highlight: 'max'
      }
    }
  },
  {
    primitive: 'sort_order',
    className: 'sort-stage',
    label: '排序卡 (sort_order)',
    scene: {
      primitive: 'sort_order',
      title: '给水果排队',
      prompt: '拖动卡片按大小排序',
      goal: '由低到高',
      props: {
        items: [
          { label: '🍎 苹果', value: 3, color: '#d95b59' },
          { label: '🍉 西瓜', value: 8, color: '#58a875' },
          { label: '🍇 葡萄', value: 1, color: '#b07cd8' },
          { label: '🍌 香蕉', value: 5, color: '#f5c84b' },
        ],
        sortBy: 'value',
        direction: 'asc',
        mode: 'sort'
      }
    }
  },
  {
    primitive: 'number_game',
    className: 'numbergame-stage',
    label: '数字游戏 (number_game)',
    scene: {
      primitive: 'number_game',
      title: '抢30大作战',
      prompt: '点击数字抢到30',
      goal: '抢到30',
      props: {
        target: 30,
        step: [1, 2],
        highlightMultiples: 3,
        mode: 'game'
      }
    }
  },
  {
    primitive: 'state_machine',
    className: 'state-stage',
    label: '状态机 (state_machine)',
    scene: {
      primitive: 'state_machine',
      title: '狼羊菜过河',
      prompt: '帮助农夫安全过河',
      goal: '全部安全过河',
      props: {
        items: [
          { name: '狼', emoji: '🐺', eats: '羊' },
          { name: '羊', emoji: '🐑', eats: '菜' },
          { name: '菜', emoji: '🥬', eats: null },
        ]
      }
    }
  },
  {
    primitive: 'pattern_sequence',
    className: 'pattern-stage',
    label: '图案规律 (pattern_sequence)',
    scene: {
      primitive: 'pattern_sequence',
      title: '找规律填图案',
      prompt: '观察规律选答案',
      goal: '找出规律',
      props: {
        sequence: ['🔴', '🔵', '🟢', '🔴', '🔵', '🟢', '🔴', '🔵', '?'],
        choices: ['🔴', '🔵', '🟢', '🟡'],
        answer: 2,
        mode: 'complete'
      }
    }
  },
  {
    primitive: 'drag_to_animal',
    className: 'dta-stage',
    label: '拖拽喂动物 (drag_to_animal)',
    scene: {
      primitive: 'drag_to_animal',
      title: '给小动物喂食',
      prompt: '把正确的食物拖给小动物',
      goal: '找到小动物想吃的',
      props: {
        animal: '🐱 小猫',
        fruits: ['🐟 鱼', '🥕 胡萝卜', '🦴 骨头', '🍎 苹果'],
        correct: 0
      }
    }
  },
  {
    primitive: 'match_pairs',
    className: 'match-stage',
    label: '配对 (match_pairs)',
    scene: {
      primitive: 'match_pairs',
      title: '小动物找影子',
      prompt: '把动物和它的影子连起来',
      goal: '完成配对',
      props: {
        pairs: [
          { left: '🐶 小狗', right: '🐾 脚印' },
          { left: '🐱 小猫', right: '🐾 肉垫' },
          { left: '🐰 兔子', right: '🥕 胡萝卜' },
        ]
      }
    }
  },
  {
    primitive: 'logic_grid',
    className: 'logic-stage',
    label: '逻辑表格 (logic_grid)',
    scene: {
      primitive: 'logic_grid',
      title: '侦探推理表',
      prompt: '点击格子标记对错',
      goal: '找出真相',
      props: {
        mode: 'truth_table',
        rows: ['小红', '小明', '小华'],
        cols: ['红色', '蓝色', '黄色'],
        cornerLabel: '人物 \\ 颜色',
        clues: ['小红不喜欢红色', '小明喜欢蓝色'],
        answerGrid: [
          ['no', 'no', 'yes'],
          ['no', 'yes', 'no'],
          ['yes', 'no', 'no'],
        ]
      }
    }
  },
  {
    primitive: 'grid_explorer',
    className: 'grid-stage',
    label: '网格探索 (grid_explorer)',
    scene: {
      primitive: 'grid_explorer',
      title: '找宝藏迷宫',
      prompt: '移动小老鼠找到奶酪',
      goal: '到达终点',
      props: {
        gridSize: 5,
        start: [0, 0],
        end: [4, 4],
        obstacles: [[1, 2], [2, 2], [3, 1]],
        mode: 'pathfind'
      }
    }
  },
  {
    primitive: 'balance_scale',
    className: 'balance-stage',
    label: '天平称重 (balance_scale)',
    scene: {
      primitive: 'balance_scale',
      title: '假币大侦探',
      prompt: '用天平找出假币',
      goal: '找出假币',
      props: {
        coins: 8,
        fakeIndex: 3,
        maxWeighs: 2
      }
    }
  },
  {
    primitive: 'time_scheduler',
    className: 'scheduler-stage',
    label: '时间管理 (time_scheduler)',
    scene: {
      primitive: 'time_scheduler',
      title: '合理安排时间',
      prompt: '看看怎么安排最省时间',
      goal: '找到最优方案',
      props: {
        tasks: [
          { label: '写作业', duration: 20, parallel: false },
          { label: '烧水', duration: 10, parallel: true },
          { label: '听英语', duration: 15, parallel: false },
        ],
        timeline: 30,
        mode: 'parallel'
      }
    }
  },
  {
    primitive: 'paper_fold_explorer',
    className: 'fold-explorer-stage',
    label: '折纸探索 (paper_fold_explorer)',
    scene: {
      primitive: 'paper_fold_explorer',
      title: '折纸挑战',
      prompt: '试试对折多少次能到月球',
      goal: '探索折叠的奥秘',
      props: {
        mode: 'fold',
        thickness: 0.1,
        maxFolds: 42,
        folds: 0
      }
    }
  },
  {
    primitive: 'attribute_sort',
    className: 'attribute-stage',
    label: '属性分类 (attribute_sort)',
    scene: {
      primitive: 'attribute_sort',
      title: '神秘服装店',
      prompt: '根据线索找到目标衣服',
      goal: '找到正确衣服',
      props: {
        items: [
          { key:'red-pocket', label:'红色口袋服', color:'red', pocket:true },
          { key:'red-plain', label:'红色外套', color:'red', pocket:false },
          { key:'blue-pocket', label:'蓝色口袋服', color:'blue', pocket:true },
          { key:'yellow-plain', label:'黄色雨衣', color:'yellow', pocket:false },
          { key:'green-pocket', label:'绿色口袋服', color:'green', pocket:true },
          { key:'red-stripe', label:'红色条纹衣', color:'red', pocket:false },
        ],
        clues: [
          { key:'red', label:'红色', attr:'color', value:'red' },
          { key:'pocket', label:'有口袋', attr:'pocket', value:true },
        ],
        target: { color:'red', pocket:true },
        clueTitle: '侦探线索'
      }
    }
  },
  {
    primitive: 'shape_guess',
    className: 'shape-guess-stage',
    label: '形状猜测 (shape_guess)',
    scene: {
      primitive: 'shape_guess',
      title: '魔法袋摸形状',
      prompt: '伸手摸摸，猜是什么形状',
      goal: '猜出形状',
      props: {
        shapes: ['circle', 'square', 'triangle'],
        shape: 'circle',
        revealStep: 0
      }
    }
  },
  {
    primitive: 'watermelon_halves',
    className: 'wm-stage',
    label: '西瓜分半 (watermelon_halves)',
    scene: {
      primitive: 'watermelon_halves',
      title: '分一分大西瓜',
      prompt: '看看半个西瓜和整个一样大吗',
      goal: '理解等分',
      props: {
        mode: 'compare',
        state: 'whole'
      }
    }
  },
  {
    primitive: 'mirror_transform',
    className: 'mirror-stage',
    label: '镜像变换 (mirror_transform)',
    scene: {
      primitive: 'mirror_transform',
      title: '怪兽镜子大冒险',
      prompt: '看看镜子里的小怪兽长什么样',
      goal: '理解镜像对称',
      props: {
        mode: 'monster',
        hand: 'right',
        star: 'left'
      }
    }
  },
  {
    primitive: 'cut_fold_unfold',
    className: 'fold-stage',
    label: '剪折展开 (cut_fold_unfold)',
    scene: {
      primitive: 'cut_fold_unfold',
      title: '折纸大王',
      prompt: '对折后剪开，看看展开是什么',
      goal: '理解对称剪裁',
      props: {
        open: false,
        shape: 'heart'
      }
    }
  }
];

const RESULTS = {
  results: [],
  errors: [],
  total: 0,
  passed: 0,
  failed: 0,
  startTime: null,
  endTime: null,
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function run() {
  RESULTS.startTime = new Date().toISOString();

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
    locale: 'zh-CN',
  });

  const page = await context.newPage();
  const errors = [];

  // Collect ALL console messages and network requests
  page.on('console', msg => {
    const text = msg.text();
    if (text.includes('[DIAG]') || text.includes('error') || text.includes('Error') ||
        text.includes('404') || text.includes('Failed to load') || text.includes('ERR_')) {
      errors.push({ type: 'console', level: msg.type(), text, url: msg.location().url });
    }
  });

  page.on('pageerror', err => {
    errors.push({ type: 'pageerror', text: err.message, stack: err.stack });
  });

  page.on('response', response => {
    if (response.status() >= 400) {
      errors.push({ type: 'network', status: response.status(), url: response.url() });
    }
  });

  let connected = false;

  try {
    console.log('🔍 Navigating to app...');
    await page.goto(APP_URL, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForSelector('.app', { timeout: 10000 });
    console.log('✅ App loaded successfully');

    // Try to connect
    console.log('🔌 Attempting to connect...');
    const connectBtn = page.locator('#btn-connect');

    // Check if button is enabled
    const isDisabled = await connectBtn.isDisabled();
    if (!isDisabled) {
      await connectBtn.click();
      console.log('⏳ Waiting for connection...');

      // Wait up to 15 seconds for connection status to show "已连接"
      try {
        await page.waitForSelector('.status.connected', { timeout: 15000 });
        connected = true;
        console.log('✅ Connected to teacher!');
        // Wait a bit for initial components
        await sleep(3000);
      } catch (e) {
        console.log('⚠️ Connection timed out (no teaching agent running)');
        console.log('   Will inject test data directly for verification');
        await sleep(2000);
      }
    } else {
      console.log('⚠️ Connect button is disabled');
    }

    // Now test EACH primitive type
    RESULTS.total = PRIMITIVE_TESTS.length;

    for (const test of PRIMITIVE_TESTS) {
      console.log(`\n🧪 Testing: ${test.label} (${test.primitive})`);
      const testResult = {
        primitive: test.primitive,
        label: test.label,
        status: 'unknown',
        errors: [],
        screenshot: null,
        details: {}
      };

      const beforeErrors = errors.length;

      try {
        // Clear previous interactive scene
        await page.evaluate(() => {
          const scene = document.getElementById('interactive-scene');
          if (scene) {
            scene.innerHTML = '';
            scene.hidden = true;
          }
          // Clear any quiz/board artifacts
          const quizEl = document.getElementById('quiz-options');
          if (quizEl) quizEl.innerHTML = '';
        });

        await sleep(300);

        // Inject the scene data directly
        await page.evaluate((sceneData) => {
          if (typeof window.renderInteractiveScene === 'function') {
            window.renderInteractiveScene(sceneData);
          } else {
            document.getElementById('interactive-scene').textContent =
              'ERROR: renderInteractiveScene not found on window';
          }
        }, test.scene);

        await sleep(800);

        // Check if the scene element is visible
        const sceneVisible = await page.evaluate(() => {
          const el = document.getElementById('interactive-scene');
          return el && !el.hidden && el.innerHTML.length > 0;
        });

        testResult.details.sceneVisible = sceneVisible;

        // Check for the specific component class
        const componentExists = await page.evaluate((className) => {
          return !!document.querySelector('.' + className);
        }, test.className);

        testResult.details.componentExists = componentExists;

        // Get innerHTML length for content verification
        const innerHTMLSize = await page.evaluate(() => {
          const el = document.getElementById('interactive-scene');
          return el ? el.innerHTML.length : 0;
        });
        testResult.details.innerHTMLSize = innerHTMLSize;

        // Check for errors
        const newErrors = errors.slice(beforeErrors);
        if (newErrors.length > 0) {
          testResult.errors = newErrors.map(e => ({
            type: e.type,
            text: e.text || e.url || '',
            status: e.status || ''
          }));
        }

        // Take screenshot
        const screenshotPath = join(SCREENSHOT_DIR, `${test.primitive}.png`);
        await page.screenshot({ path: screenshotPath, fullPage: false });
        testResult.screenshot = screenshotPath;

        // Determine status
        if (componentExists && sceneVisible) {
          testResult.status = 'pass';
          console.log(`  ✅ PASS: ${test.className} found, scene visible, ${innerHTMLSize} chars`);
        } else if (!componentExists && sceneVisible) {
          testResult.status = 'warn';
          console.log(`  ⚠️ WARN: Scene visible but ${test.className} class not found`);
        } else if (!sceneVisible) {
          testResult.status = 'fail';
          console.log(`  ❌ FAIL: Scene not visible`);
        } else {
          testResult.status = 'fail';
          console.log(`  ❌ FAIL: ${test.className} not found`);
        }

      } catch (err) {
        testResult.status = 'error';
        testResult.errors.push({ type: 'exception', text: err.message });
        console.log(`  💥 ERROR: ${err.message}`);
      }

      if (testResult.status === 'pass') RESULTS.passed++;
      else RESULTS.failed++;

      RESULTS.results.push(testResult);

      // Collect new errors
      const newErrors = errors.slice(beforeErrors);
      newErrors.forEach(e => {
        RESULTS.errors.push({
          primitive: test.primitive,
          type: e.type,
          text: e.text || e.url || '',
          status: e.status || '',
          level: e.level || '',
        });
      });

      await sleep(200);
    }

  } catch (err) {
    console.error(`💥 Fatal error: ${err.message}`);
    RESULTS.errors.push({ type: 'fatal', text: err.message });
  } finally {
    RESULTS.endTime = new Date().toISOString();

    // Write results
    writeFileSync(REPORT_FILE, JSON.stringify(RESULTS, null, 2));
    console.log(`\n📊 Report written to ${REPORT_FILE}`);

    // Summary
    console.log('\n═══════════════════════════════════');
    console.log(`📊 TEST SUMMARY`);
    console.log('═══════════════════════════════════');
    console.log(`Total:  ${RESULTS.total}`);
    console.log(`Passed: ${RESULTS.passed}`);
    console.log(`Failed: ${RESULTS.failed}`);
    console.log(`Errors: ${RESULTS.errors.length}`);

    // Error details
    if (RESULTS.errors.length > 0) {
      console.log('\n⚠️  ERRORS DETECTED:');
      const seen = new Set();
      RESULTS.errors.forEach(e => {
        const key = e.text + e.type;
        if (!seen.has(key)) {
          seen.add(key);
          console.log(`   [${e.type}] ${e.text.substring(0, 200)}`);
        }
      });
    }

    // Failed tests
    const failed = RESULTS.results.filter(r => r.status !== 'pass');
    if (failed.length > 0) {
      console.log('\n❌ FAILED TESTS:');
      failed.forEach(r => {
        console.log(`   - ${r.label} (${r.primitive}): ${r.status}`);
        r.errors.forEach(e => console.log(`     Error: ${e.text}`));
      });
    }

    await browser.close();
    console.log('\n✅ Browser closed');
  }
}

run().catch(console.error);
