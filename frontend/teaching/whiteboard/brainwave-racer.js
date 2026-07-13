import { sceneButton } from './shared.js';

export function renderBrainwaveRacer(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'scan';

  if (mode === 'signal') return renderSignalMode(props);
  if (mode === 'race') return renderRaceMode(props);
  return renderScanMode(props);
}

function renderScanMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-scan';

  const states = props.states || [
    { label: '分心', focus: 35, quality: '信号有点乱' },
    { label: '正常', focus: 62, quality: '信号正在变稳' },
    { label: '专注', focus: 88, quality: '平静清楚的脑波信号' },
  ];
  let index = Math.max(0, states.findIndex(item => item.label === (props.active || '专注')));
  if (index < 0) index = 0;

  const cockpit = document.createElement('div');
  cockpit.className = 'brainwave-cockpit';
  cockpit.append(createKid(), createSignalLink(), createMiniRacer(props.car || 'AI'));

  const wave = document.createElement('div');
  wave.className = 'brainwave-wave';

  const meter = document.createElement('div');
  meter.className = 'brainwave-meter';

  const readout = document.createElement('div');
  readout.className = 'brainwave-readout';

  function draw() {
    const state = states[index];
    const focus = Math.max(0, Math.min(100, Number(state.focus) || 0));
    wave.innerHTML = '';
    for (let i = 0; i < 18; i++) {
      const dot = document.createElement('span');
      dot.style.height = (12 + ((i * 7 + focus) % 34)) + 'px';
      dot.style.opacity = String(0.45 + focus / 180);
      wave.appendChild(dot);
    }
    meter.innerHTML = '<div class="brainwave-meter-fill"></div>';
    meter.firstChild.style.width = focus + '%';
    readout.textContent = state.label + '：' + focus + '% · ' + state.quality;
  }

  draw();
  stage.append(cockpit, wave, meter, readout);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('提升专注', () => {
      index = states.length - 1;
      draw();
    }),
    sceneButton('切换状态', () => {
      index = (index + 1) % states.length;
      draw();
    })
  );

  return [stage, actions];
}

function renderSignalMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-signal';

  const steps = props.steps || ['脑波输入', 'AI识别', '生成指令', '赛车响应'];
  const commands = props.commands || ['保持直线', '轻轻加速', '全速前进'];
  let active = 0;

  const pipeline = document.createElement('div');
  pipeline.className = 'brainwave-pipeline';

  const command = document.createElement('div');
  command.className = 'brainwave-command';

  const signalScene = document.createElement('div');
  signalScene.className = 'brainwave-signal-scene';
  signalScene.append(createKid(), createSignalLink(), createMiniRacer(props.car || 'AI'));

  function draw() {
    pipeline.innerHTML = '';
    steps.forEach((step, idx) => {
      const node = document.createElement('div');
      node.className = 'brainwave-node';
      if (idx <= active) node.classList.add('active');
      node.textContent = step;
      pipeline.appendChild(node);
    });
    command.textContent = '当前指令：' + commands[Math.min(active, commands.length - 1)];
  }

  draw();
  stage.append(signalScene, pipeline, command);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('发送信号', () => {
      active = Math.min(active + 1, steps.length - 1);
      draw();
    }),
    sceneButton('查看指令', () => {
      active = steps.length - 1;
      draw();
    })
  );

  return [stage, actions];
}

function renderRaceMode(props) {
  const stage = document.createElement('div');
  stage.className = 'brainwave-stage brainwave-race';

  let focus = Number(props.focus || 55);
  let distracted = Boolean(props.distraction);

  const track = document.createElement('div');
  track.className = 'brainwave-track';
  const car = document.createElement('div');
  car.className = 'brainwave-car';
  car.appendChild(createRacerBody(props.car || 'AI'));
  track.appendChild(car);

  const energy = document.createElement('div');
  energy.className = 'brainwave-energy';

  const status = document.createElement('div');
  status.className = 'brainwave-status';

  function draw() {
    focus = Math.max(0, Math.min(100, focus));
    car.style.left = Math.min(88, 8 + focus * 0.8) + '%';
    track.classList.toggle('has-distraction', distracted);
    energy.innerHTML = '<div class="brainwave-energy-fill"></div>';
    energy.firstChild.style.width = focus + '%';
    status.textContent = distracted
      ? '干扰出现：先稳住脑波'
      : focus >= 90
        ? '能量满格：可以冲过终点'
        : '专注能量：' + focus + '%';
  }

  draw();
  stage.append(track, energy, status);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('提升专注', () => {
      focus += distracted ? 8 : 16;
      draw();
    }),
    sceneButton('排除干扰', () => {
      distracted = false;
      draw();
    }),
    sceneButton('冲刺', () => {
      if (!distracted && focus >= 80) focus = 100;
      draw();
    })
  );

  return [stage, actions];
}

function createKid() {
  const kid = document.createElement('div');
  kid.className = 'brainwave-kid';
  kid.innerHTML = [
    '<div class="brainwave-kid-helmet"></div>',
    '<div class="brainwave-kid-face"><span></span></div>',
    '<div class="brainwave-kid-body"></div>',
  ].join('');
  return kid;
}

function createSignalLink() {
  const link = document.createElement('div');
  link.className = 'brainwave-signal-link';
  link.innerHTML = '<span></span><span></span><span></span>';
  return link;
}

function createMiniRacer(label) {
  const racer = document.createElement('div');
  racer.className = 'brainwave-mini-racer';
  racer.appendChild(createRacerBody(label));
  return racer;
}

function createRacerBody(label) {
  const body = document.createElement('div');
  body.className = 'brainwave-car-body';
  body.innerHTML = [
    '<div class="brainwave-car-flame"></div>',
    '<div class="brainwave-car-cabin">' + label + '</div>',
    '<div class="brainwave-car-wheel left"></div>',
    '<div class="brainwave-car-wheel right"></div>',
  ].join('');
  return body;
}
