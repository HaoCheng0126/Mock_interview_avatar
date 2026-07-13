import { sceneButton } from './shared.js';

export function renderShapeGuess(scene) {
  const props = scene.props || {};
  const shapes = props.shapes || ['circle', 'square', 'triangle'];
  const currentShape = props.shape || 'circle';
  const revealStep = props.revealStep || 0;

  const stage = document.createElement('div');
  stage.className = 'shape-guess-stage';

  const bag = document.createElement('div');
  bag.className = 'magic-bag';

  const bagTop = document.createElement('div');
  bagTop.className = 'bag-top';
  bagTop.textContent = '🎒 魔法袋';

  const revealArea = document.createElement('div');
  revealArea.className = 'reveal-area';

  let step = revealStep;
  const maxSteps = 3;

  const shapeEl = document.createElement('div');
  shapeEl.className = 'shape-reveal';

  function drawShape(s, sStep) {
    shapeEl.className = 'shape-reveal shape-' + s;
    if (sStep === 0) {
      shapeEl.style.clipPath = 'inset(0 70% 70% 0)';
      shapeEl.style.opacity = '0.3';
    } else if (sStep === 1) {
      shapeEl.style.clipPath = 'inset(0 40% 40% 0)';
      shapeEl.style.opacity = '0.6';
    } else if (sStep === 2) {
      shapeEl.style.clipPath = 'inset(0 10% 10% 0)';
      shapeEl.style.opacity = '0.85';
    } else {
      shapeEl.style.clipPath = 'none';
      shapeEl.style.opacity = '1';
    }
  }

  drawShape(currentShape, step);

  const guessLabel = document.createElement('div');
  guessLabel.className = 'guess-label';
  guessLabel.textContent = step >= maxSteps ? '猜出来了吗？这是什么形状？' : '再摸一摸，多露出一点…';

  revealArea.append(shapeEl);

  const guessButtons = document.createElement('div');
  guessButtons.className = 'guess-buttons';

  const shapeNames = { circle: '⚪ 圆形', square: '🟫 方形', triangle: '🔺 三角形' };
  shapes.forEach(s => {
    const btn = sceneButton(shapeNames[s] || s, () => {
      guessButtons.querySelectorAll('.scene-action').forEach(b => b.classList.remove('correct-guess', 'wrong-guess'));
      if (s === currentShape) {
        btn.classList.add('correct-guess');
        drawShape(currentShape, maxSteps);
        guessLabel.textContent = '太棒了！猜对啦！是' + (shapeNames[s] || s) + '！';
      } else {
        btn.classList.add('wrong-guess');
        guessLabel.textContent = '差一点点！再摸摸看～';
        setTimeout(() => {
          btn.classList.remove('wrong-guess');
          guessLabel.textContent = step >= maxSteps ? '猜出来了吗？这是什么形状？' : '再摸一摸，多露出一点…';
        }, 1000);
      }
    });
    guessButtons.appendChild(btn);
  });

  bag.append(bagTop, revealArea, guessLabel);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('伸手摸摸', () => {
      if (step < maxSteps) {
        step++;
        drawShape(currentShape, step);
        guessLabel.textContent = step >= maxSteps ? '猜出来了吗？这是什么形状？' : '再摸一摸，多露出一点…';
      }
    }),
    sceneButton('换一个形状', () => {
      const idx = shapes.indexOf(currentShape);
      const nextShape = shapes[(idx + 1) % shapes.length];
      // We need to update currentShape — but it's a local var. Use a workaround.
      step = 0;
      drawShape(nextShape, step);
      guessLabel.textContent = '新形状放进去了！伸手摸摸看～';
      // Update the closure variable
      shapeEl.dataset.current = nextShape;
    })
  );

  stage.append(bag, guessButtons);
  return [stage, actions];
}
