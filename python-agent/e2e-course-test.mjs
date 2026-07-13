/**
 * Comprehensive E2E Test: Verify ALL courses render their interactive primitives.
 * Reads actual course YAML files and tests each one in the browser.
 *
 * Usage: node e2e-course-test.mjs
 */

import { chromium } from 'playwright';
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = join(__dirname, 'screenshots');
const COURSE_DIR = join(__dirname, '..', 'python-agent', 'config', 'courses');
const REPORT_FILE = join(__dirname, 'course-test-report.json');

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const APP_URL = 'http://localhost:8082';

// All known primitives and their expected CSS class names
const PRIMITIVE_CLASSES = {
  bar_chart: 'barchart-stage',
  sort_order: 'sort-stage',
  number_game: 'numbergame-stage',
  state_machine: 'state-stage',
  pattern_sequence: 'pattern-stage',
  drag_to_animal: 'dta-stage',
  match_pairs: 'match-stage',
  logic_grid: 'logic-stage',
  grid_explorer: 'grid-stage',
  balance_scale: 'balance-stage',
  time_scheduler: 'scheduler-stage',
  paper_fold_explorer: 'fold-explorer-stage',
  attribute_sort: 'attribute-stage',
  shape_guess: 'shape-guess-stage',
  watermelon_halves: 'wm-stage',
  mirror_transform: 'mirror-stage',
  cut_fold_unfold: 'fold-stage',
};

const RESULTS = {
  total: 0,
  passed: 0,
  failed: 0,
  errors: [],
  courses: [],
  startTime: null,
  endTime: null,
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function run() {
  RESULTS.startTime = new Date().toISOString();

  // ── Step 1: Read all course YAML files ──
  console.log('📖 Reading course YAML files...\n');
  const yamlFiles = readdirSync(COURSE_DIR).filter(f => f.endsWith('.yaml'));

  const courseExperiences = [];

  for (const yamlFile of yamlFiles) {
    const courseName = yamlFile.replace('.yaml', '');
    const filePath = join(COURSE_DIR, yamlFile);
    const content = readFileSync(filePath, 'utf-8');

    try {
      const data = yaml.load(content);
      const chapters = data.chapters || [];

      for (const chapter of chapters) {
        const skeleton = chapter.skeleton || [];
        for (const [stepIdx, step] of skeleton.entries()) {
          const exp = step.experience;
          if (exp && exp.primitive) {
            courseExperiences.push({
              courseName,
              chapterId: chapter.id,
              chapterTitle: chapter.title,
              stepIndex: stepIdx + 1,
              stepText: step.text || '',
              primitive: exp.primitive,
              scene: {
                primitive: exp.primitive,
                title: chapter.title || '课堂互动',
                prompt: exp.prompt || '动手试试看',
                goal: exp.goal || '',
                props: exp.props || {},
              }
            });
          }
        }
      }
    } catch (err) {
      console.log(`  ⚠️ Could not parse ${yamlFile}: ${err.message}`);
    }
  }

  console.log(`✅ Found ${courseExperiences.length} experiences across ${yamlFiles.length} courses\n`);

  // Group by primitive type
  const byPrimitive = {};
  for (const exp of courseExperiences) {
    if (!byPrimitive[exp.primitive]) byPrimitive[exp.primitive] = [];
    byPrimitive[exp.primitive].push(exp);
  }

  console.log('📊 Primitive coverage:');
  for (const [prim, items] of Object.entries(byPrimitive)) {
    console.log(`   ${prim}: ${items.length} instances across ${new Set(items.map(i => i.courseName)).size} courses`);
  }
  console.log();

  // ── Step 2: Launch browser and test ──
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

  const browserErrors = [];
  const network404s = new Set();

  page.on('console', msg => {
    const text = msg.text();
    if (text.includes('404') || text.includes('Failed to load') || text.includes('ERR_')) {
      browserErrors.push({ type: 'console', level: msg.type(), text: text.substring(0, 300) });
    }
  });

  page.on('pageerror', err => {
    browserErrors.push({ type: 'pageerror', text: err.message.substring(0, 300) });
  });

  page.on('response', response => {
    if (response.status() === 404) {
      network404s.add(response.url());
    }
  });

  try {
    console.log('📍 Navigating to app...');
    await page.goto(APP_URL, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForSelector('.app', { timeout: 10000 });
    console.log('✅ App loaded successfully\n');

    // Check initial 404s after page load
    if (network404s.size > 0) {
      console.log('⚠️  INITIAL NETWORK 404s:');
      network404s.forEach(url => console.log(`   ${url}`));
      RESULTS.errors.push(...Array.from(network404s).map(url => ({
        type: 'network_404_initial',
        url,
      })));
    }

    // Wait for module scripts to load
    await sleep(2000);

    // ── Step 3: Test each primitive type with real course data ──
    const testedPrimitives = new Set();
    const primitiveResults = {};
    const failedCourses = [];

    for (const [primitive, exps] of Object.entries(byPrimitive)) {
      const className = PRIMITIVE_CLASSES[primitive];
      if (!className) {
        console.log(`  ⚠️ Unknown primitive "${primitive}" — no CSS class mapping`);
        continue;
      }

      const courseNames = [...new Set(exps.map(e => e.courseName))];
      console.log(`\n🧪 Testing primitive: ${primitive} (${exps.length} instances, in ${courseNames.join(', ')})`);

      // Test each unique scene data from different courses
      let coursePassed = 0;
      let courseFailed = 0;

      for (const exp of exps) {
        const courseKey = `${exp.courseName}::${exp.chapterId}::step${exp.stepIndex}`;
        const screenshotPath = join(SCREENSHOT_DIR,
          `${primitive}_${exp.courseName.replace(/[^a-zA-Z0-9一-鿿]/g, '_')}_step${exp.stepIndex}.png`);

        // Clear previous scene
        await page.evaluate(() => {
          const scene = document.getElementById('interactive-scene');
          if (scene) { scene.innerHTML = ''; scene.hidden = true; }
          const quizEl = document.getElementById('quiz-options');
          if (quizEl) quizEl.innerHTML = '';
        });
        await sleep(200);

        // Inject scene data
        await page.evaluate((sceneData) => {
          if (typeof window.renderInteractiveScene === 'function') {
            window.renderInteractiveScene(sceneData);
          } else {
            document.getElementById('interactive-scene').textContent =
              'ERROR: renderInteractiveScene not found';
          }
        }, exp.scene);

        await sleep(600);

        // Verify rendering
        const result = await page.evaluate((className) => {
          const scene = document.getElementById('interactive-scene');
          if (!scene) return { hidden: true, componentFound: false };
          return {
            hidden: scene.hidden,
            innerHTMLSize: scene.innerHTML.length,
            componentFound: !!document.querySelector('.' + className),
          };
        }, className);

        // Take screenshot
        try {
          await page.screenshot({ path: screenshotPath, fullPage: false });
        } catch (e) {
          console.log(`   ⚠️ Screenshot failed: ${e.message}`);
        }

        // Score
        const passed = result.componentFound && !result.hidden && result.innerHTMLSize > 100;

        primitiveResults[courseKey] = {
          course: exp.courseName,
          chapter: exp.chapterId,
          step: exp.stepIndex,
          primitive,
          passed,
          componentFound: result.componentFound,
          sceneHidden: result.hidden,
          innerHTMLSize: result.innerHTMLSize,
          screenshot: screenshotPath,
        };

        if (passed) {
          coursePassed++;
        } else {
          courseFailed++;
        }
      }

      testedPrimitives.add(primitive);

      const total = exps.length;
      console.log(`   Results: ${coursePassed}/${total} passed, ${courseFailed}/${total} failed`);

      if (courseFailed > 0) {
        failedCourses.push({ primitive, failedInstances: courseFailed, total });
      }
    }

    // ── Step 4: Compile results ──
    RESULTS.total = courseExperiences.length;
    for (const [key, result] of Object.entries(primitiveResults)) {
      if (result.passed) RESULTS.passed++;
      else RESULTS.failed++;
      RESULTS.courses.push(result);
    }

    // Collect network 404 errors
    if (network404s.size > 0) {
      RESULTS.errors.push({
        type: 'network_404',
        urls: [...network404s],
      });
    }

    // Check for any browser console errors during rendering
    if (browserErrors.length > 0) {
      RESULTS.errors.push({
        type: 'console_errors',
        count: browserErrors.length,
        errors: browserErrors.slice(0, 20),
      });
    }

  } catch (err) {
    console.error(`\n💥 Fatal error: ${err.message}`);
    RESULTS.errors.push({ type: 'fatal', text: err.message });
  } finally {
    RESULTS.endTime = new Date().toISOString();
    writeFileSync(REPORT_FILE, JSON.stringify(RESULTS, null, 2));

    console.log('\n' + '═'.repeat(55));
    console.log('📊 FINAL TEST REPORT');
    console.log('═'.repeat(55));
    console.log(`Total experiences tested: ${RESULTS.total}`);
    console.log(`Passed:                  ${RESULTS.passed}`);
    console.log(`Failed:                  ${RESULTS.failed}`);

    // Primitive type summary
    const byPrim = {};
    for (const r of RESULTS.courses) {
      if (!byPrim[r.primitive]) byPrim[r.primitive] = { total: 0, passed: 0 };
      byPrim[r.primitive].total++;
      if (r.passed) byPrim[r.primitive].passed++;
    }

    console.log('\n📊 Per-primitive results:');
    for (const [prim, stats] of Object.entries(byPrim)) {
      const status = stats.passed === stats.total ? '✅' : '❌';
      console.log(`   ${status} ${prim}: ${stats.passed}/${stats.total}`);
    }

    // Failed courses
    const failed = RESULTS.courses.filter(r => !r.passed);
    if (failed.length > 0) {
      console.log('\n❌ FAILED INSTANCES:');
      for (const f of failed) {
        console.log(`   ${f.primitive}: course="${f.course}" chapter=${f.chapter} step=${f.step}`);
        console.log(`     componentFound=${f.componentFound} hidden=${f.sceneHidden} htmlSize=${f.innerHTMLSize}`);
      }
    }

    if (RESULTS.errors.length > 0) {
      console.log(`\n⚠️  ${RESULTS.errors.length} error(s) reported`);
      for (const err of RESULTS.errors) {
        if (err.type === 'network_404') {
          console.log(`   404 errors: ${err.urls?.length || 0} URLs`);
          err.urls?.forEach(u => console.log(`     ${u}`));
        } else if (err.type === 'console_errors') {
          console.log(`   Console errors: ${err.count}`);
          err.errors?.forEach(e => console.log(`     [${e.level}] ${e.text}`));
        } else {
          console.log(`   ${err.type}: ${err.text || err.message || ''}`);
        }
      }
    }

    console.log(`\n📸 Screenshots: ${SCREENSHOT_DIR}`);
    console.log(`📊 Report: ${REPORT_FILE}`);

    await browser.close();
    console.log('✅ Done\n');
  }
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
