import { sceneButton } from './shared.js';

// ── watermelon drawing helpers ──────────────────────────────────────────

function watermelonWhole(size = 180) {
  const el = document.createElement('div');
  el.className = 'wm-whole';
  el.style.width = size + 'px';
  el.style.height = size + 'px';
  // Seeds: 5 small black teardrops arranged in a ring
  const seedPositions = [
    { top: '30%', left: '35%', rot: -20 },
    { top: '55%', left: '25%', rot: 10 },
    { top: '50%', left: '55%', rot: -30 },
    { top: '28%', left: '60%', rot: 15 },
    { top: '65%', left: '65%', rot: -5 },
  ];
  seedPositions.forEach(pos => {
    const seed = document.createElement('div');
    seed.className = 'wm-seed';
    seed.style.top = pos.top;
    seed.style.left = pos.left;
    seed.style.transform = 'rotate(' + pos.rot + 'deg)';
    el.appendChild(seed);
  });
  return el;
}

function watermelonHalf(side, size = 180) {
  const el = document.createElement('div');
  el.className = 'wm-half wm-half-' + side;
  el.style.width = (size / 2) + 'px';
  el.style.height = size + 'px';
  // Seeds on the cut face (flat side)
  const seedPositions = side === 'left'
    ? [
        { top: '20%', right: '18%', rot: -80 },
        { top: '50%', right: '22%', rot: -90 },
        { top: '75%', right: '16%', rot: -100 },
      ]
    : [
        { top: '20%', left: '18%', rot: 80 },
        { top: '50%', left: '22%', rot: 90 },
        { top: '75%', left: '16%', rot: 100 },
      ];
  seedPositions.forEach(pos => {
    const seed = document.createElement('div');
    seed.className = 'wm-seed';
    if (pos.right) seed.style.right = pos.right;
    if (pos.left) seed.style.left = pos.left;
    seed.style.top = pos.top;
    seed.style.transform = 'rotate(' + pos.rot + 'deg)';
    el.appendChild(seed);
  });
  // Cut-face inner highlight
  const face = document.createElement('div');
  face.className = 'wm-cut-face';
  el.appendChild(face);
  return el;
}

// ── compare mode ────────────────────────────────────────────────────────

function renderCompare(scene) {
  const props = scene.props || {};
  const state = props.state || 'whole';
  const size = 130;
  const stage = document.createElement('div');
  stage.className = 'wm-stage';

  const display = document.createElement('div');
  display.className = 'wm-compare-display';

  function draw() {
    display.innerHTML = '';
    if (state === 'whole') {
      // Two whole watermelons side by side: compare sizes
      const wrap1 = labeledCard(watermelonWhole(size), '完整');
      const eq = document.createElement('div');
      eq.className = 'wm-eq-sign';
      eq.textContent = '=';
      display.append(wrap1, eq, labeledCard(watermelonWhole(size), '完整'));
    } else {
      // One whole = two halves
      const wrapWhole = labeledCard(watermelonWhole(size), '完整');
      const eq = document.createElement('div');
      eq.className = 'wm-eq-sign';
      eq.textContent = '=';
      const halvesWrap = document.createElement('div');
      halvesWrap.className = 'wm-halves-pair';
      halvesWrap.append(watermelonHalf('left', size), watermelonHalf('right', size));
      const labeledHalves = labeledCard(halvesWrap, '两个半块');
      display.append(wrapWhole, eq, labeledHalves);
    }
  }

  draw();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton(state === 'whole' ? '切成两半看看' : '拼回完整的', () => {
      const newState = state === 'whole' ? 'halved' : 'whole';
      // Mutate props so toggle persists across draws
      props.state = newState;
      // Reassign state for local closure
      draw();
      // Update button text
      actions.innerHTML = '';
      actions.append(
        sceneButton(newState === 'whole' ? '切成两半看看' : '拼回完整的', () => {
          props.state = newState === 'whole' ? 'halved' : 'whole';
          draw();
          actions.innerHTML = '';
          actions.append(
            sceneButton(props.state === 'whole' ? '切成两半看看' : '拼回完整的', arguments.callee)
          );
        })
      );
      // Simple re-render via replacing the scene actions
    })
  );

  stage.append(display, actions);
  return [stage];
}

function labeledCard(child, label) {
  const wrap = document.createElement('div');
  wrap.className = 'wm-labeled-card';
  wrap.appendChild(child);
  const lbl = document.createElement('div');
  lbl.className = 'wm-card-label';
  lbl.textContent = label;
  wrap.appendChild(lbl);
  return wrap;
}

// ── cut mode ─────────────────────────────────────────────────────────────

function renderCut(scene) {
  const props = scene.props || {};
  const size = 140;
  const stage = document.createElement('div');
  stage.className = 'wm-stage';

  const display = document.createElement('div');
  display.className = 'wm-cut-display';

  let cutState = props.state || 'uncut';
  let animating = false;

  function draw() {
    display.innerHTML = '';
    if (cutState === 'uncut') {
      const wholeWrap = document.createElement('div');
      wholeWrap.className = 'wm-whole-wrap';
      wholeWrap.appendChild(watermelonWhole(size));
      const cutLine = document.createElement('div');
      cutLine.className = 'wm-cut-line';
      wholeWrap.appendChild(cutLine);
      display.appendChild(wholeWrap);
    } else {
      // Two halves slightly separated
      const halvesWrap = document.createElement('div');
      halvesWrap.className = 'wm-halves-pair wm-halves-separated';
      halvesWrap.append(watermelonHalf('left', size), watermelonHalf('right', size));
      display.appendChild(halvesWrap);
      const info = document.createElement('div');
      info.className = 'wm-cut-info';
      info.textContent = '左边和右边一样大！各占一半～';
      display.appendChild(info);
    }
  }

  draw();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';

  function updateActions() {
    actions.innerHTML = '';
    if (cutState === 'uncut') {
      const cutBtn = sceneButton('🔪 咔嚓切下去！', () => {
        if (animating) return;
        animating = true;
        // Brief animation: shake then split
        const wholeWrap = display.querySelector('.wm-whole-wrap');
        if (wholeWrap) {
          wholeWrap.style.transition = 'transform 0.15s';
          wholeWrap.style.transform = 'rotate(-3deg)';
          setTimeout(() => {
            wholeWrap.style.transform = 'rotate(3deg)';
            setTimeout(() => {
              wholeWrap.style.transform = 'rotate(0deg)';
              cutState = 'halves';
              draw();
              updateActions();
              animating = false;
            }, 150);
          }, 150);
        } else {
          cutState = 'halves';
          draw();
          updateActions();
          animating = false;
        }
      });
      actions.appendChild(cutBtn);
    } else {
      const resetBtn = sceneButton('重新切一次', () => {
        cutState = 'uncut';
        draw();
        updateActions();
      });
      actions.appendChild(resetBtn);
    }
  }

  updateActions();
  stage.append(display, actions);
  return [stage];
}

// ── match mode ───────────────────────────────────────────────────────────

function renderMatch(scene) {
  const props = scene.props || {};
  const size = 130;
  const stage = document.createElement('div');
  stage.className = 'wm-stage';

  const display = document.createElement('div');
  display.className = 'wm-match-display';

  let matchState = props.state || 'matching';
  let selected = null;
  let correctKey = Math.floor(Math.random() * 3); // which candidate is correct

  // Wrong candidates get a vertical offset; correct one stays aligned at 0.
  function buildCandidates(correctIdx) {
    const offsets = [6, 12, 18];
    return [0, 1, 2].map(i => ({
      key: i,
      label: String.fromCharCode(65 + i), // A, B, C
      offset: i === correctIdx ? 0 : offsets[i],
    }));
  }

  let candidates = buildCandidates(correctKey);

  function draw() {
    display.innerHTML = '';

    if (matchState === 'matching') {
      const leftPanel = document.createElement('div');
      leftPanel.className = 'wm-match-left';
      const leftLabel = document.createElement('div');
      leftLabel.className = 'wm-match-label';
      leftLabel.textContent = '找到另一半';
      leftPanel.appendChild(leftLabel);
      leftPanel.appendChild(watermelonHalf('left', size));
      display.appendChild(leftPanel);

      const rightPanel = document.createElement('div');
      rightPanel.className = 'wm-match-right';
      candidates.forEach(c => {
        const card = document.createElement('div');
        card.className = 'wm-match-card';
        if (selected === c.key) card.classList.add('wm-match-selected');
        const half = watermelonHalf('right', size);
        if (c.offset) half.style.marginTop = c.offset + 'px';
        card.appendChild(half);
        const lbl = document.createElement('div');
        lbl.className = 'wm-match-option-label';
        lbl.textContent = c.label;
        card.appendChild(lbl);
        card.onclick = () => {
          if (selected !== null) return;
          selected = c.key;
          if (c.key === correctKey) {
            card.classList.add('wm-match-correct');
            setTimeout(() => {
              matchState = 'complete';
              draw();
            }, 800);
          } else {
            card.classList.add('wm-match-wrong');
            setTimeout(() => {
              card.classList.remove('wm-match-wrong', 'wm-match-selected');
              selected = null;
            }, 600);
          }
        };
        rightPanel.appendChild(card);
      });
      display.appendChild(rightPanel);
    } else {
      // Complete: two halves joined together
      const completeWrap = document.createElement('div');
      completeWrap.className = 'wm-complete-wrap';
      completeWrap.appendChild(watermelonWhole(size));
      display.appendChild(completeWrap);

      const celebrate = document.createElement('div');
      celebrate.className = 'wm-celebrate';
      celebrate.textContent = '🎉 变回大西瓜啦！';
      display.appendChild(celebrate);

      // Confetti particles
      for (let i = 0; i < 8; i++) {
        const particle = document.createElement('div');
        particle.className = 'wm-particle';
        particle.style.left = (10 + Math.random() * 80) + '%';
        particle.style.animationDelay = (Math.random() * 0.5) + 's';
        particle.style.animationDuration = (1 + Math.random() * 1.5) + 's';
        particle.textContent = ['🎉', '✨', '🌟', '💫'][i % 4];
        display.appendChild(particle);
      }
    }
  }

  draw();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  if (matchState === 'complete') {
    actions.append(sceneButton('再来一次', () => {
      matchState = 'matching';
      selected = null;
      correctKey = Math.floor(Math.random() * 3);
      candidates = buildCandidates(correctKey);
      draw();
      actions.innerHTML = '';
      actions.appendChild(sceneButton('再来一次', () => {
        matchState = 'matching';
        selected = null;
        correctKey = Math.floor(Math.random() * 3);
        candidates = buildCandidates(correctKey);
        draw();
        actions.innerHTML = '';
        actions.appendChild(sceneButton('再来一次', arguments.callee));
      }));
    }));
  }

  stage.append(display, actions);
  return [stage];
}

// ── main entry ───────────────────────────────────────────────────────────

export function renderWatermelonHalves(scene) {
  const mode = (scene.props && scene.props.mode) || 'compare';
  switch (mode) {
    case 'cut':   return renderCut(scene);
    case 'match': return renderMatch(scene);
    default:      return renderCompare(scene);
  }
}
