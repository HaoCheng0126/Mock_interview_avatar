import { sceneButton } from './shared.js';

export function renderBioBuilderLab(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'module';

  if (mode === 'combine') return renderCombineMode(props);
  if (mode === 'rescue') return renderRescueMode(props);
  return renderModuleMode(props);
}

function renderModuleMode(props) {
  const stage = createStage('module');
  const chips = props.chips || [
    { code: 'A', label: '荧光模块', active: true },
    { code: 'B', label: '治疗模块', active: false },
  ];
  stage.append(createScientist(), createGeneRack(chips), createPod(props.status || '等待扫描'));
  return withActions(stage, [
    ['扫描A模块', () => stage.classList.add('bio-lab-active-a')],
    ['扫描B模块', () => stage.classList.add('bio-lab-active-b')],
  ]);
}

function renderCombineMode(props) {
  const stage = createStage('combine');
  const chips = props.chips || [
    { code: 'A', label: '发光' },
    { code: 'B', label: '治疗' },
  ];
  const bridge = document.createElement('div');
  bridge.className = 'bio-lab-combine-bridge';
  bridge.textContent = props.formula || 'A + B → AB';
  stage.append(createScientist(), createGeneRack(chips), bridge, createPod(props.status || '组合测试中'));
  return withActions(stage, [
    ['插入A', () => stage.classList.add('bio-lab-active-a')],
    ['插入B', () => stage.classList.add('bio-lab-active-b')],
    ['合成AB', () => stage.classList.add('bio-lab-complete')],
  ]);
}

function renderRescueMode(props) {
  const stage = createStage('rescue');
  stage.append(createScientist(), createPod(props.status || '小象治疗舱', true), createGeneRack([
    { code: 'AB', label: props.result || '康复+荧光', active: true },
  ]));
  return withActions(stage, [
    ['启动治疗舱', () => stage.classList.add('bio-lab-active-b')],
    ['点亮荧光', () => stage.classList.add('bio-lab-complete')],
  ]);
}

function createStage(mode) {
  const stage = document.createElement('div');
  stage.className = 'bio-lab-stage bio-lab-' + mode;
  return stage;
}

function createScientist() {
  const scientist = document.createElement('div');
  scientist.className = 'bio-lab-scientist';
  scientist.innerHTML = '<div class="bio-lab-visor"></div><div class="bio-lab-face"></div><div class="bio-lab-coat"></div>';
  return scientist;
}

function createGeneRack(chips) {
  const rack = document.createElement('div');
  rack.className = 'bio-lab-gene-rack';
  chips.forEach(chip => {
    const item = document.createElement('div');
    item.className = 'bio-lab-gene-chip';
    if (chip.active) item.classList.add('active');
    item.innerHTML = '<strong>' + chip.code + '</strong><span>' + chip.label + '</span>';
    rack.appendChild(item);
  });
  return rack;
}

function createPod(status, elephantOn = false) {
  const pod = document.createElement('div');
  pod.className = 'bio-lab-pod';
  pod.innerHTML = '<div class="bio-lab-scan-line"></div><div class="bio-lab-elephant">' + (elephantOn ? '小象' : 'DNA') + '</div><div class="bio-lab-status">' + status + '</div>';
  return pod;
}

function withActions(stage, actionsList) {
  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actionsList.forEach(([label, handler]) => actions.appendChild(sceneButton(label, handler)));
  return [stage, actions];
}
