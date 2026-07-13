import { sceneButton } from './shared.js';

export function renderMatchPairs(scene) {
  const props = scene.props || {};
  const pairs = props.pairs || [];
  if (!pairs.length) return [document.createElement('div')];

  const stage = document.createElement('div');
  stage.className = 'match-stage';

  const leftCol = document.createElement('div');
  leftCol.className = 'match-col';
  const rightCol = document.createElement('div');
  rightCol.className = 'match-col';
  const linesSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  linesSvg.classList.add('match-lines');

  const matchedPairs = new Set();
  let dragItem = null;  // { el, origIdx }

  function drawLines() {
    linesSvg.innerHTML = '';
    matchedPairs.forEach(origIdx => {
      const leftEl = leftCol.querySelector('[data-idx="' + origIdx + '"]');
      const rightEl = rightCol.querySelector('[data-orig-idx="' + origIdx + '"]');
      if (!leftEl || !rightEl) return;
      const sr = stage.getBoundingClientRect();
      const lr = leftEl.getBoundingClientRect();
      const rr = rightEl.getBoundingClientRect();
      const x1 = lr.right - sr.left;
      const y1 = lr.top + lr.height / 2 - sr.top;
      const x2 = rr.left - sr.left;
      const y2 = rr.top + rr.height / 2 - sr.top;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x1);
      line.setAttribute('y1', y1);
      line.setAttribute('x2', x2);
      line.setAttribute('y2', y2);
      line.classList.add('match-line-done');
      linesSvg.appendChild(line);
    });
  }

  // Shuffle right column order
  const shuffled = pairs.map((p, i) => ({ ...p, origIdx: i }));
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }

  // --- Left column: drop targets (animals) ---
  pairs.forEach((pair, idx) => {
    const el = document.createElement('div');
    el.className = 'match-item match-left';
    el.textContent = pair.left;
    el.dataset.idx = idx;

    // Drop handlers
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('match-hover'); });
    el.addEventListener('dragleave', () => el.classList.remove('match-hover'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      el.classList.remove('match-hover');
      if (!dragItem) return;
      const fromIdx = dragItem.origIdx;
      if (matchedPairs.has(fromIdx)) return;
      if (fromIdx === idx) {
        // Correct match
        matchedPairs.add(fromIdx);
        el.classList.add('matched');
        const rightEl = rightCol.querySelector('[data-orig-idx="' + fromIdx + '"]');
        if (rightEl) rightEl.classList.add('matched');
        drawLines();
        // All matched?
        if (matchedPairs.size === pairs.length) {
          stage.classList.add('match-all-done');
        }
      } else {
        // Wrong match — flash
        const rightEl = rightCol.querySelector('[data-orig-idx="' + fromIdx + '"]');
        el.classList.add('wrong-flash');
        if (rightEl) rightEl.classList.add('wrong-flash');
        setTimeout(() => {
          el.classList.remove('wrong-flash');
          if (rightEl) rightEl.classList.remove('wrong-flash');
        }, 500);
      }
      dragItem = null;
    });

    // Click fallback: if a left item is clicked while a right item is selected
    el.addEventListener('click', () => {
      const selectedRight = rightCol.querySelector('.match-item.selected');
      if (!selectedRight) return;
      const fromIdx = parseInt(selectedRight.dataset.origIdx);
      if (matchedPairs.has(fromIdx)) return;
      if (fromIdx === idx) {
        matchedPairs.add(fromIdx);
        el.classList.add('matched');
        selectedRight.classList.add('matched');
        selectedRight.classList.remove('selected');
        drawLines();
        if (matchedPairs.size === pairs.length) {
          stage.classList.add('match-all-done');
        }
      } else {
        el.classList.add('wrong-flash');
        selectedRight.classList.add('wrong-flash');
        selectedRight.classList.remove('selected');
        setTimeout(() => {
          el.classList.remove('wrong-flash');
          selectedRight.classList.remove('wrong-flash');
        }, 500);
      }
    });

    leftCol.appendChild(el);
  });

  // --- Right column: draggable fruits ---
  shuffled.forEach(pair => {
    const el = document.createElement('div');
    el.className = 'match-item match-right';
    el.textContent = pair.right;
    el.dataset.origIdx = pair.origIdx;
    el.draggable = true;

    el.addEventListener('dragstart', e => {
      if (matchedPairs.has(pair.origIdx)) { e.preventDefault(); return; }
      dragItem = { el, origIdx: pair.origIdx };
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(pair.origIdx));
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      dragItem = null;
    });

    // Click fallback: select this item, then click a left item to match
    el.addEventListener('click', () => {
      if (matchedPairs.has(pair.origIdx)) return;
      rightCol.querySelectorAll('.match-item').forEach(b => b.classList.remove('selected'));
      el.classList.add('selected');
    });

    rightCol.appendChild(el);
  });

  stage.append(leftCol, linesSvg, rightCol);

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('重新开始', () => {
      matchedPairs.clear();
      stage.classList.remove('match-all-done');
      leftCol.querySelectorAll('.match-item').forEach(b => b.classList.remove('matched', 'wrong-flash', 'match-hover'));
      rightCol.querySelectorAll('.match-item').forEach(b => b.classList.remove('matched', 'wrong-flash', 'selected', 'dragging'));
      // Reshuffle
      const items = Array.from(rightCol.children);
      for (let i = items.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        rightCol.insertBefore(items[j], items[i]);
      }
      drawLines();
    })
  );

  requestAnimationFrame(() => drawLines());

  return [stage, actions];
}
