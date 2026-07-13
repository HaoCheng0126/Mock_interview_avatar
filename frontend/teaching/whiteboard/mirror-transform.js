import { sceneButton } from './shared.js';

function monsterElement(state){
  const wrap = document.createElement('div');
  wrap.className = 'monster';
  const star = document.createElement('div');
  star.className = 'monster-star ' + (state.star || 'left');
  const mouth = document.createElement('div');
  mouth.className = 'monster-mouth';
  const left = document.createElement('div');
  left.className = 'monster-hand left' + (state.hand === 'left' ? ' up' : '');
  const right = document.createElement('div');
  right.className = 'monster-hand right' + (state.hand === 'right' ? ' up' : '');
  wrap.append(star, mouth, left, right);
  return wrap;
}

function flipSide(side){
  if (side === 'left') return 'right';
  if (side === 'right') return 'left';
  return side || 'left';
}

function letterElement(letter, mirrored){
  const wrap = document.createElement('div');
  wrap.className = 'letter-symbol' + (mirrored ? ' mirror' : '');
  wrap.textContent = letter || 'A';
  return wrap;
}

function renderMirrorLetters(scene){
  const props = scene.props || {};
  const letter = props.letter || 'A';
  const stage = document.createElement('div');
  stage.className = 'mirror-stage';
  const realCard = document.createElement('div');
  realCard.className = 'monster-card';
  const mirrorLine = document.createElement('div');
  mirrorLine.className = 'mirror-line';
  const mirrorCard = document.createElement('div');
  mirrorCard.className = 'monster-card mirror';
  const realLabel = document.createElement('div');
  realLabel.className = 'letter-caption';
  realLabel.textContent = '真实字母';
  const mirrorLabel = document.createElement('div');
  mirrorLabel.className = 'letter-caption';
  mirrorLabel.textContent = '镜子里';
  realCard.append(realLabel, letterElement(letter, false));
  mirrorCard.append(mirrorLabel, letterElement(letter, true));
  stage.append(realCard, mirrorLine, mirrorCard);
  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(sceneButton('观察镜像A', () => {}));
  return [stage, actions];
}

function renderMirrorMonsters(scene){
  const props = scene.props || {};
  const stage = document.createElement('div');
  stage.className = 'mirror-stage';
  const realCard = document.createElement('div');
  realCard.className = 'monster-card';
  const mirrorLine = document.createElement('div');
  mirrorLine.className = 'mirror-line';
  const mirrorCard = document.createElement('div');
  mirrorCard.className = 'monster-card mirror';
  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  const state = {
    hand: props.hand || (props.action === 'hand_flip' ? 'right' : ''),
    star: props.star || 'left',
  };

  function draw(){
    realCard.innerHTML = '';
    mirrorCard.innerHTML = '';
    realCard.appendChild(monsterElement(state));
    mirrorCard.appendChild(monsterElement({
      hand: flipSide(state.hand),
      star: flipSide(state.star),
    }));
  }

  actions.append(
    sceneButton('举左手', () => { state.hand = 'left'; draw(); }),
    sceneButton('举右手', () => { state.hand = 'right'; draw(); }),
    sceneButton('星星换边', () => { state.star = flipSide(state.star); draw(); })
  );
  draw();
  stage.append(realCard, mirrorLine, mirrorCard);
  return [stage, actions];
}

export function renderMirrorTransform(scene){
  const props = scene.props || {};
  if (props.mode === 'letter') return renderMirrorLetters(scene);
  return renderMirrorMonsters(scene);
}
