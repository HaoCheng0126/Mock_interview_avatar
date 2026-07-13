import { sceneButton } from './shared.js';

export function renderDragToAnimal(scene) {
  const props = scene.props || {};
  const animal = props.animal || '';
  const fruits = props.fruits || [];
  const correctIdx = props.correct !== undefined ? props.correct : 0;
  if (!animal || !fruits.length) return [document.createElement('div')];

  const stage = document.createElement('div');
  stage.className = 'dta-stage';

  // ── Animal (center, drop target) ──
  const animalEl = document.createElement('div');
  animalEl.className = 'dta-animal';
  const animalEmoji = animal.replace(/[^\p{Emoji}‍]/gu, '').trim() || animal.slice(-2);
  const animalLabel = animal.replace(animalEmoji, '').trim();
  animalEl.innerHTML = '<span class="dta-animal-emoji">' + animalEmoji + '</span>' +
    (animalLabel ? '<span class="dta-animal-label">' + animalLabel + '</span>' : '');
  animalEl.dataset.accepts = String(correctIdx);

  let fed = false;

  // Drop handlers on animal
  animalEl.addEventListener('dragover', e => { e.preventDefault(); animalEl.classList.add('dta-hover'); });
  animalEl.addEventListener('dragleave', () => animalEl.classList.remove('dta-hover'));
  animalEl.addEventListener('drop', e => {
    e.preventDefault();
    animalEl.classList.remove('dta-hover');
    if (fed) return;
    const fruitIdx = parseInt(e.dataTransfer.getData('text/dta-idx') || '-1');
    if (fruitIdx < 0) return;
    handleDrop(fruitIdx);
  });

  function handleDrop(fruitIdx) {
    const fruitEl = stage.querySelector('[data-dta-idx="' + fruitIdx + '"]');
    if (!fruitEl) return;

    if (fruitIdx === correctIdx) {
      // Correct!
      fed = true;
      fruitEl.classList.add('dta-correct');
      animalEl.classList.add('dta-fed');
      // Spawn celebration particles
      for (let i = 0; i < 6; i++) {
        const p = document.createElement('div');
        p.className = 'dta-particle';
        p.textContent = ['❤️', '✨', '🌟', '💫', '🎉', '💖'][i];
        p.style.left = (30 + Math.random() * 40) + '%';
        p.style.top = (10 + Math.random() * 60) + '%';
        p.style.animationDelay = (Math.random() * 0.3) + 's';
        p.style.animationDuration = (0.8 + Math.random() * 0.8) + 's';
        stage.appendChild(p);
        setTimeout(() => p.remove(), 1800);
      }
    } else {
      // Wrong — bounce back
      fruitEl.classList.add('dta-wrong');
      setTimeout(() => fruitEl.classList.remove('dta-wrong'), 500);
      animalEl.classList.add('dta-shake');
      setTimeout(() => animalEl.classList.remove('dta-shake'), 400);
    }
  }

  stage.appendChild(animalEl);

  // ── Scattered fruits ──
  // Position them in an arc below/around the animal
  const positions = [
    { top: '10%', left: '8%', rot: -15 },
    { top: '60%', left: '5%', rot: 10 },
    { top: '70%', left: '55%', rot: -8 },
    { top: '15%', left: '65%', rot: 20 },
  ];

  fruits.forEach((fruit, idx) => {
    const el = document.createElement('div');
    el.className = 'dta-fruit';
    el.textContent = fruit;
    el.dataset.dtaIdx = idx;
    el.draggable = true;
    // Position
    const pos = positions[idx] || { top: (30 + idx * 20) + '%', left: (10 + idx * 25) + '%', rot: 0 };
    el.style.top = pos.top;
    el.style.left = pos.left;
    el.style.setProperty('--rot', pos.rot + 'deg');

    el.addEventListener('dragstart', e => {
      if (fed) { e.preventDefault(); return; }
      el.classList.add('dta-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/dta-idx', String(idx));
    });
    el.addEventListener('dragend', () => el.classList.remove('dta-dragging'));

    stage.appendChild(el);
  });

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('再来一次', () => {
      fed = false;
      animalEl.classList.remove('dta-fed', 'dta-shake');
      stage.querySelectorAll('.dta-fruit').forEach(f => f.classList.remove('dta-correct', 'dta-wrong'));
      stage.querySelectorAll('.dta-particle').forEach(p => p.remove());
    })
  );

  stage.appendChild(actions);
  return [stage];
}
