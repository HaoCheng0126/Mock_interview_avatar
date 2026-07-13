import { sceneButton } from './shared.js';

export function renderTimeScheduler(scene) {
  const props = scene.props || {};
  const tasks = props.tasks || [
    { label: '写作业', duration: 20, parallel: false },
    { label: '烧水', duration: 10, parallel: true },
    { label: '听英语', duration: 15, parallel: false },
  ];
  const timeline = props.timeline || 30; // total minutes
  const mode = props.mode || 'schedule'; // 'schedule' | 'parallel'

  const stage = document.createElement('div');
  stage.className = 'scheduler-stage';

  // Timeline header
  const header = document.createElement('div');
  header.className = 'timeline-header';
  for (let i = 0; i <= timeline; i += 5) {
    const tick = document.createElement('div');
    tick.className = 'timeline-tick';
    tick.textContent = i + '\'';
    header.appendChild(tick);
  }

  // Task lanes
  const lanes = document.createElement('div');
  lanes.className = 'task-lanes';

  // For parallel mode, we show two lanes: main tasks + parallel tasks
  const mainLane = document.createElement('div');
  mainLane.className = 'task-lane';
  const mainLabel = document.createElement('div');
  mainLabel.className = 'lane-label';
  mainLabel.textContent = '主要任务';
  mainLane.appendChild(mainLabel);

  const parallelLane = document.createElement('div');
  parallelLane.className = 'task-lane parallel';
  const parallelLabel = document.createElement('div');
  parallelLabel.className = 'lane-label';
  parallelLabel.textContent = '并行任务';
  parallelLane.appendChild(parallelLabel);

  // Schedule: place non-parallel tasks sequentially, parallel ones alongside
  let timeOffset = 0;
  const mainTasks = tasks.filter(t => !t.parallel);
  const parallelTasks = tasks.filter(t => t.parallel);

  function drawSchedule() {
    mainLane.querySelectorAll('.task-block').forEach(b => b.remove());
    parallelLane.querySelectorAll('.task-block').forEach(b => b.remove());

    let t = 0;
    mainTasks.forEach(task => {
      const block = document.createElement('div');
      block.className = 'task-block';
      block.style.left = (t / timeline * 100) + '%';
      block.style.width = (task.duration / timeline * 100) + '%';
      block.textContent = task.label + ' (' + task.duration + '分)';
      mainLane.appendChild(block);
      t += task.duration;
    });

    // Place parallel tasks alongside the longest main task
    parallelTasks.forEach(task => {
      const bestIdx = mainTasks.reduce((best, mt, i) => {
        const mtDur = mt.duration;
        const bestDur = mainTasks[best] ? mainTasks[best].duration : 0;
        return mtDur >= task.duration && mtDur > bestDur ? i : best;
      }, 0);

      let startOffset = 0;
      for (let i = 0; i < bestIdx; i++) startOffset += mainTasks[i].duration;

      const block = document.createElement('div');
      block.className = 'task-block parallel-block';
      block.style.left = (startOffset / timeline * 100) + '%';
      block.style.width = (task.duration / timeline * 100) + '%';
      block.textContent = task.label + ' (' + task.duration + '分)';
      parallelLane.appendChild(block);
    });

    // Total time display
    const totalEl = stage.querySelector('.total-time') || document.createElement('div');
    totalEl.className = 'total-time';
    const serialTime = tasks.reduce((s, t) => s + t.duration, 0);
    const parallelTime = mainTasks.reduce((s, t) => s + t.duration, 0);
    totalEl.textContent = '串行需要 ' + serialTime + ' 分钟 | 并行只需 ' + parallelTime + ' 分钟 ⚡';
    if (!stage.querySelector('.total-time')) {
      stage.appendChild(totalEl);
    }
  }

  drawSchedule();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('显示串行', () => {
      // Show all tasks sequentially
      mainLane.querySelectorAll('.task-block').forEach(b => b.remove());
      parallelLane.querySelectorAll('.task-block').forEach(b => b.remove());
      let t = 0;
      tasks.forEach(task => {
        const block = document.createElement('div');
        block.className = 'task-block';
        block.style.left = (t / timeline * 100) + '%';
        block.style.width = (task.duration / timeline * 100) + '%';
        block.textContent = task.label + ' (' + task.duration + '分)';
        mainLane.appendChild(block);
        t += task.duration;
      });
      const totalEl = stage.querySelector('.total-time');
      if (totalEl) totalEl.textContent = '串行需要 ' + t + ' 分钟';
    }),
    sceneButton('显示并行', () => {
      drawSchedule();
    })
  );

  lanes.append(mainLane, parallelLane);
  stage.append(header, lanes);
  return [stage, actions];
}
