import { sceneButton } from './shared.js';

export function renderNumberGame(scene) {
  const props = scene.props || {};
  const target = props.target || 30;
  const stepMin = (props.step || [1, 2])[0] || 1;
  const stepMax = (props.step || [1, 2])[1] || 2;
  const highlightMultiples = props.highlightMultiples || 3;
  const mode = props.mode || 'game'; // 'game' | 'demo'

  const stage = document.createElement('div');
  stage.className = 'numbergame-stage';

  let current = 0;
  let playerTurn = true;
  let gameOver = false;
  const totalSteps = stepMax - stepMin + 1;

  const info = document.createElement('div');
  info.className = 'ng-info';

  const grid = document.createElement('div');
  grid.className = 'number-grid';

  function drawGrid() {
    grid.innerHTML = '';
    for (let i = 1; i <= target; i++) {
      const cell = document.createElement('div');
      cell.className = 'ng-cell';
      cell.textContent = i;

      if (i <= current) {
        cell.classList.add('taken');
        if (i === current) cell.classList.add('last-taken');
      }

      // Highlight winning positions (multiples)
      if (highlightMultiples > 0) {
        // Winning positions from the end: target, target-step-1, etc.
        const winningNums = new Set();
        for (let w = target; w > 0; w -= (stepMax + 1)) {
          winningNums.add(w);
        }
        if (winningNums.has(i) && i > current) {
          cell.classList.add('winning-pos');
        }
      }

      if (!gameOver && i > current && i <= current + stepMax) {
        cell.classList.add('clickable');
        cell.onclick = () => {
          if (gameOver || !playerTurn) return;
          const take = i - current;
          if (take >= stepMin && take <= stepMax) {
            current = i;
            playerTurn = false;
            drawGrid();
            updateInfo();

            if (current >= target) {
              gameOver = true;
              info.textContent = '🎉 你赢了！你抢到了 ' + target + '！';
              drawGrid();
              return;
            }

            // AI move after short delay
            setTimeout(() => {
              if (gameOver) return;
              // Simple AI: try to land on winning position
              let bestMove = stepMin;
              const winningNums = new Set();
              for (let w = target; w > 0; w -= (stepMax + 1)) {
                winningNums.add(w);
              }
              for (let take = stepMax; take >= stepMin; take--) {
                if (winningNums.has(current + take)) {
                  bestMove = take;
                  break;
                }
              }
              // If can't reach winning pos, take random valid move
              const maxTake = Math.min(stepMax, target - current);
              if (bestMove > maxTake) bestMove = Math.max(stepMin, maxTake);

              current += bestMove;
              playerTurn = true;
              drawGrid();
              updateInfo();

              if (current >= target) {
                gameOver = true;
                info.textContent = '🤖 AI 赢了！它抢到了 ' + target + '。秘诀：控制 3 的倍数！';
                drawGrid();
              }
            }, 700);
          }
        };
      }
      grid.appendChild(cell);
    }
  }

  function updateInfo() {
    if (!gameOver) {
      const remaining = target - current;
      info.textContent = playerTurn
        ? '你的回合！当前 ' + current + '，你可以报 ' + stepMin + '~' + stepMax + ' 个数。目标：' + target
        : 'AI 思考中…当前 ' + current;
    }
  }

  drawGrid();
  updateInfo();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('重新开始', () => {
      current = 0;
      playerTurn = true;
      gameOver = false;
      drawGrid();
      updateInfo();
    }),
    sceneButton('显示必胜位', () => {
      grid.querySelectorAll('.winning-pos').forEach(c => c.classList.toggle('show-winning'));
    }),
    sceneButton('我先后', () => {
      current = 0;
      playerTurn = true;
      gameOver = false;
      drawGrid();
      updateInfo();
    }),
    sceneButton('AI先走', () => {
      current = 0;
      playerTurn = false;
      gameOver = false;
      drawGrid();
      updateInfo();
      setTimeout(() => {
        current += stepMin;
        playerTurn = true;
        drawGrid();
        updateInfo();
      }, 500);
    })
  );

  stage.append(info, grid);
  return [stage, actions];
}
