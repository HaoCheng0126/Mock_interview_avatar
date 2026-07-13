import { sceneButton } from './shared.js';

export function renderStateMachine(scene) {
  const props = scene.props || {};
  const items = props.items || [
    { name: '狼', emoji: '🐺', eats: '羊' },
    { name: '羊', emoji: '🐑', eats: '菜' },
    { name: '菜', emoji: '🥬', eats: null },
    { name: '农夫', emoji: '👨‍🌾', eats: null },
  ];

  // States: "left" bank items
  const allItems = items.map(i => i.name);
  let leftBank = new Set(allItems);
  let rightBank = new Set();
  let farmerOnLeft = true;
  let stepCount = 0;
  let message = '';

  const stage = document.createElement('div');
  stage.className = 'state-stage';

  const river = document.createElement('div');
  river.className = 'river-scene';

  const leftShore = document.createElement('div');
  leftShore.className = 'shore left-shore';
  const riverEl = document.createElement('div');
  riverEl.className = 'river-water';
  riverEl.textContent = '🌊 🌊 🌊';
  const rightShore = document.createElement('div');
  rightShore.className = 'shore right-shore';

  const boat = document.createElement('div');
  boat.className = 'boat';
  boat.textContent = '⛵';

  let boatPassenger = null;

  function isSafe(bank, farmerPresent) {
    if (bank.size <= 1) return true;
    if (farmerPresent) return true;
    for (const item of items) {
      if (item.eats && bank.has(item.name) && bank.has(item.eats)) {
        return false;
      }
    }
    return true;
  }

  function drawState() {
    leftShore.innerHTML = '<div class="shore-label">左岸</div>';
    rightShore.innerHTML = '<div class="shore-label">右岸</div>';

    leftBank.forEach(name => {
      const item = items.find(i => i.name === name);
      const el = document.createElement('button');
      el.className = 'state-item';
      el.type = 'button';
      el.textContent = item.emoji + ' ' + item.name;
      if (farmerOnLeft) {
        el.classList.add('selectable');
        el.onclick = () => {
          if (boatPassenger === name) {
            boatPassenger = null;
            el.classList.remove('on-boat');
          } else {
            if (boatPassenger) {
              document.querySelectorAll('.state-item.on-boat').forEach(e => e.classList.remove('on-boat'));
            }
            boatPassenger = name;
            el.classList.add('on-boat');
          }
          drawBoat();
        };
      }
      leftShore.appendChild(el);
    });

    rightBank.forEach(name => {
      const item = items.find(i => i.name === name);
      const el = document.createElement('button');
      el.className = 'state-item';
      el.type = 'button';
      el.textContent = item.emoji + ' ' + item.name;
      if (!farmerOnLeft) {
        el.classList.add('selectable');
        el.onclick = () => {
          if (boatPassenger === name) {
            boatPassenger = null;
            el.classList.remove('on-boat');
          } else {
            if (boatPassenger) {
              document.querySelectorAll('.state-item.on-boat').forEach(e => e.classList.remove('on-boat'));
            }
            boatPassenger = name;
            el.classList.add('on-boat');
          }
          drawBoat();
        };
      }
      rightShore.appendChild(el);
    });

    // Show danger state
    const leftSafe = isSafe(leftBank, farmerOnLeft);
    const rightSafe = isSafe(rightBank, !farmerOnLeft);
    if (!leftSafe) leftShore.classList.add('danger');
    else leftShore.classList.remove('danger');
    if (!rightSafe) rightShore.classList.add('danger');
    else rightShore.classList.remove('danger');

    drawBoat();
  }

  function drawBoat() {
    boat.innerHTML = '⛵';
    if (boatPassenger) {
      const item = items.find(i => i.name === boatPassenger);
      boat.textContent = '⛵ ' + (item ? item.emoji : '');
    }
    boat.style.alignSelf = farmerOnLeft ? 'flex-start' : 'flex-end';
  }

  const msgEl = document.createElement('div');
  msgEl.className = 'state-message';

  function crossRiver() {
    // Validate safety before crossing
    const leavingBank = farmerOnLeft ? leftBank : rightBank;
    const stayingBank = farmerOnLeft ? rightBank : leftBank;

    if (boatPassenger && !leavingBank.has(boatPassenger)) {
      message = '选中的不在这边岸上！';
      drawState();
      msgEl.textContent = message;
      return;
    }

    // Simulate new state
    const newLeft = new Set(leftBank);
    const newRight = new Set(rightBank);

    if (farmerOnLeft) {
      newLeft.delete('农夫');
      newRight.add('农夫');
      if (boatPassenger) {
        newLeft.delete(boatPassenger);
        newRight.add(boatPassenger);
      }
    } else {
      newRight.delete('农夫');
      newLeft.add('农夫');
      if (boatPassenger) {
        newRight.delete(boatPassenger);
        newLeft.add(boatPassenger);
      }
    }

    // Check safety after crossing
    if (!isSafe(newLeft, !farmerOnLeft) || !isSafe(newRight, farmerOnLeft)) {
      message = '⚠️ 危险！留下的会互相吃掉！重新选择吧。';
      drawState();
      msgEl.textContent = message;
      return;
    }

    // Apply move
    leftBank = newLeft;
    rightBank = newRight;
    farmerOnLeft = !farmerOnLeft;
    boatPassenger = null;
    stepCount++;
    message = '第 ' + stepCount + ' 步 — 安全！';

    // Check win
    if (rightBank.size === allItems.length + 1) { // +1 for farmer
      message = '🎉 全部安全过河！用了 ' + stepCount + ' 步！你太聪明了！';
    }

    drawState();
    msgEl.textContent = message;
  }

  drawState();
  msgEl.textContent = '选择要带的物品（可不选），然后点过河';

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('🚣 过河', crossRiver),
    sceneButton('不带东西过河', () => {
      boatPassenger = null;
      document.querySelectorAll('.state-item.on-boat').forEach(e => e.classList.remove('on-boat'));
      drawBoat();
      crossRiver();
    }),
    sceneButton('重新开始', () => {
      leftBank = new Set(allItems);
      leftBank.add('农夫');
      rightBank = new Set();
      farmerOnLeft = true;
      boatPassenger = null;
      stepCount = 0;
      message = '';
      drawState();
      msgEl.textContent = '选择要带的物品（可不选），然后点过河';
    })
  );

  river.append(leftShore, boat, riverEl, rightShore);
  stage.append(river, msgEl);
  return [stage, actions];
}
