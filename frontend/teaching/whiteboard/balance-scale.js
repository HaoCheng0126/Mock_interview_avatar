import { sceneButton } from './shared.js';

export function renderBalanceScale(scene) {
  const props = scene.props || {};
  const totalCoins = props.coins || 8;
  const fakeIdx = props.fakeIndex !== undefined ? props.fakeIndex : 3;
  const maxWeighs = props.maxWeighs || 2;

  const stage = document.createElement('div');
  stage.className = 'balance-stage';

  // Scale visualization
  const scale = document.createElement('div');
  scale.className = 'balance-scale';

  const leftPan = document.createElement('div');
  leftPan.className = 'scale-pan left-pan';
  const rightPan = document.createElement('div');
  rightPan.className = 'scale-pan right-pan';

  const beam = document.createElement('div');
  beam.className = 'scale-beam';
  const pivot = document.createElement('div');
  pivot.className = 'scale-pivot';
  pivot.textContent = '⚖️';

  scale.append(leftPan, beam, rightPan, pivot);

  // Coin selection area
  const coinArea = document.createElement('div');
  coinArea.className = 'coin-area';

  let leftCoins = [];
  let rightCoins = [];
  let weighCount = 0;
  let found = false;

  function createCoin(idx) {
    const coin = document.createElement('button');
    coin.className = 'coin-btn';
    coin.type = 'button';
    coin.textContent = '🪙 ' + (idx + 1);
    coin.dataset.idx = idx;
    coin.onclick = () => {
      if (found) return;
      if (coin.classList.contains('on-scale')) {
        // Remove from scale
        coin.classList.remove('on-scale');
        leftCoins = leftCoins.filter(i => i !== idx);
        rightCoins = rightCoins.filter(i => i !== idx);
      } else {
        // Add to left or right
        if (leftCoins.length <= rightCoins.length) {
          leftCoins.push(idx);
          coin.classList.add('on-scale', 'on-left');
        } else {
          rightCoins.push(idx);
          coin.classList.add('on-scale', 'on-right');
        }
      }
      updatePans();
    };
    return coin;
  }

  function updatePans() {
    leftPan.innerHTML = '';
    rightPan.innerHTML = '';
    leftCoins.forEach(i => {
      const c = document.createElement('span');
      c.className = 'pan-coin';
      c.textContent = '🪙' + (i + 1);
      leftPan.appendChild(c);
    });
    rightCoins.forEach(i => {
      const c = document.createElement('span');
      c.className = 'pan-coin';
      c.textContent = '🪙' + (i + 1);
      rightPan.appendChild(c);
    });

    // Tilt based on fake coin presence
    const leftHasFake = leftCoins.includes(fakeIdx);
    const rightHasFake = rightCoins.includes(fakeIdx);
    if (leftCoins.length === rightCoins.length && leftCoins.length > 0) {
      beam.classList.remove('tilt-left', 'tilt-right', 'balanced');
      if (leftHasFake) {
        beam.classList.add('tilt-left');
        pivot.textContent = '⬆️ 左边轻';
      } else if (rightHasFake) {
        beam.classList.add('tilt-right');
        pivot.textContent = '⬆️ 右边轻';
      } else {
        beam.classList.add('balanced');
        pivot.textContent = '✅ 平衡';
      }
    } else {
      beam.classList.remove('tilt-left', 'tilt-right', 'balanced');
      pivot.textContent = '⚖️ 放一样多';
    }
  }

  for (let i = 0; i < totalCoins; i++) {
    coinArea.appendChild(createCoin(i));
  }

  const info = document.createElement('div');
  info.className = 'balance-info';
  info.textContent = '还剩 ' + (maxWeighs - weighCount) + ' 次称重机会 | 把金币拖到天平两边';

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('称重', () => {
      if (leftCoins.length !== rightCoins.length || leftCoins.length === 0) return;
      weighCount++;
      info.textContent = '还剩 ' + (maxWeighs - weighCount) + ' 次称重机会';

      const leftHasFake = leftCoins.includes(fakeIdx);
      const rightHasFake = rightCoins.includes(fakeIdx);

      if (weighCount > maxWeighs) {
        info.textContent = '称重次数用完啦！请找出假币！';
        // Enable guessing
        coinArea.querySelectorAll('.coin-btn').forEach(btn => {
          btn.style.border = '2px dashed #f5c84b';
          btn.onclick = function() {
            const idx = parseInt(this.dataset.idx);
            if (idx === fakeIdx) {
              this.classList.add('found-fake');
              info.textContent = '🎉 找到了！假币是 #' + (idx + 1) + '！用了 ' + weighCount + ' 次称重';
              found = true;
            } else {
              this.classList.add('wrong-guess');
              setTimeout(() => this.classList.remove('wrong-guess'), 800);
            }
          };
        });
      }
    }),
    sceneButton('重置', () => {
      leftCoins = [];
      rightCoins = [];
      weighCount = 0;
      found = false;
      coinArea.querySelectorAll('.coin-btn').forEach(b => {
        b.classList.remove('on-scale', 'on-left', 'on-right', 'found-fake', 'wrong-guess');
        b.style.border = '';
      });
      beam.classList.remove('tilt-left', 'tilt-right', 'balanced');
      pivot.textContent = '⚖️';
      info.textContent = '还剩 ' + maxWeighs + ' 次称重机会 | 把金币拖到天平两边';
      updatePans();
    })
  );

  stage.append(scale, coinArea, info);
  return [stage, actions];
}
