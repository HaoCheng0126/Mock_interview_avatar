import { sceneButton } from './shared.js';

export function renderCutFoldUnfold(scene){
  const props = scene.props || {};
  const stage = document.createElement('div');
  stage.className = 'fold-stage';
  const foldedCard = document.createElement('div');
  foldedCard.className = 'paper-card';
  const openCard = document.createElement('div');
  openCard.className = 'paper-card';
  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  let open = Boolean(props.open);
  let shape = props.shape || 'heart';
  let toggleButton = null;
  const allowedShapes = new Set(['heart', 'star', 'monster']);

  function paper(isOpen){
    const p = document.createElement('div');
    p.className = 'paper' + (isOpen ? '' : ' folded');
    const cut = document.createElement('div');
    cut.className = 'cut-shape';
    if (!allowedShapes.has(shape)) shape = 'heart';
    cut.classList.add('cut-' + shape);
    cut.classList.add(isOpen ? 'cut-open' : 'cut-half');
    cut.setAttribute('aria-label', shape);
    p.appendChild(cut);
    return p;
  }

  function draw(){
    foldedCard.innerHTML = '';
    openCard.innerHTML = '';
    foldedCard.appendChild(paper(false));
    openCard.appendChild(paper(open));
    if (toggleButton) toggleButton.textContent = open ? '重新折起' : '展开看看';
  }

  toggleButton = sceneButton(open ? '重新折起' : '展开看看', () => { open = !open; draw(); });
  actions.append(
    sceneButton('剪心形', () => { shape = 'heart'; draw(); }),
    sceneButton('剪星星', () => { shape = 'star'; draw(); }),
    toggleButton
  );
  draw();
  stage.append(foldedCard, openCard);
  return [stage, actions];
}
