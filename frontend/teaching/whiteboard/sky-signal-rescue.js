import { sceneButton } from './shared.js';

export function renderSkySignalRescue(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'blocked';

  if (mode === 'relay') return renderRelayMode(props);
  if (mode === 'mesh') return renderMeshMode(props);
  return renderBlockedMode(props);
}

function renderBlockedMode(props) {
  const stage = createStage('blocked');
  stage.append(createDetective(), createTerrain(props.status || '信号被大山挡住'), createSatellite('侦察'));
  return withActions(stage, [
    ['扫描地形', () => stage.classList.add('sky-signal-scan')],
    ['呼叫卫星', () => stage.classList.add('sky-signal-linked')],
  ]);
}

function renderRelayMode(props) {
  const stage = createStage('relay');
  stage.append(createDetective(), createRelayMap(props.nodes || ['地面站', '卫星A', '卫星B', '救援队']), createSatellite('接力'));
  return withActions(stage, [
    ['点亮卫星A', () => stage.classList.add('sky-signal-relay-a')],
    ['点亮卫星B', () => {
      stage.classList.add('sky-signal-relay-a');
      stage.classList.add('sky-signal-relay-b');
    }],
  ]);
}

function renderMeshMode(props) {
  const stage = createStage('mesh');
  stage.append(createDetective(), createMeshMap(props.nodes || ['A', 'B', 'C', 'D']), createSatellite('组网'));
  return withActions(stage, [
    ['建立主链路', () => stage.classList.add('sky-signal-linked')],
    ['启动备用路', () => stage.classList.add('sky-signal-strong')],
  ]);
}

function createStage(mode) {
  const stage = document.createElement('div');
  stage.className = 'sky-signal-stage sky-signal-' + mode;
  return stage;
}

function createDetective() {
  const detective = document.createElement('div');
  detective.className = 'sky-signal-detective';
  detective.innerHTML = '<div class="sky-signal-visor"></div><div class="sky-signal-face"></div><div class="sky-signal-console">指挥台</div>';
  return detective;
}

function createSatellite(label) {
  const satellite = document.createElement('div');
  satellite.className = 'sky-signal-satellite';
  satellite.innerHTML = '<div class="sky-signal-sat-core">' + label + '</div><div class="sky-signal-panel left"></div><div class="sky-signal-panel right"></div>';
  return satellite;
}

function createTerrain(status) {
  const terrain = document.createElement('div');
  terrain.className = 'sky-signal-map';
  terrain.innerHTML = '<div class="sky-signal-station left">基地</div><div class="sky-signal-mountain"></div><div class="sky-signal-station right">救援</div><div class="sky-signal-beam"></div><div class="sky-signal-status">' + status + '</div>';
  return terrain;
}

function createRelayMap(nodes) {
  const map = document.createElement('div');
  map.className = 'sky-signal-map sky-signal-relay-map';
  nodes.forEach((node, index) => map.appendChild(createNode(node, index)));
  map.appendChild(Object.assign(document.createElement('div'), { className: 'sky-signal-beam segment-a' }));
  map.appendChild(Object.assign(document.createElement('div'), { className: 'sky-signal-beam segment-b' }));
  return map;
}

function createMeshMap(nodes) {
  const map = document.createElement('div');
  map.className = 'sky-signal-map sky-signal-mesh-map';
  nodes.forEach((node, index) => map.appendChild(createNode(node, index)));
  map.appendChild(Object.assign(document.createElement('div'), { className: 'sky-signal-beam mesh-a' }));
  map.appendChild(Object.assign(document.createElement('div'), { className: 'sky-signal-beam mesh-b' }));
  map.appendChild(Object.assign(document.createElement('div'), { className: 'sky-signal-beam mesh-c' }));
  return map;
}

function createNode(label, index) {
  const item = document.createElement('div');
  item.className = 'sky-signal-node node-' + index;
  item.innerHTML = '<span class="sky-signal-node-icon">' + iconForNode(label) + '</span><span>' + label + '</span>';
  return item;
}

function iconForNode(label) {
  if (label.includes('卫星')) return 'SAT';
  if (label.includes('救援') || label.includes('目的')) return 'SOS';
  if (label.includes('基地') || label.includes('地面')) return 'ANT';
  return 'NODE';
}

function withActions(stage, actionsList) {
  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actionsList.forEach(([label, handler]) => actions.appendChild(sceneButton(label, handler)));
  return [stage, actions];
}
