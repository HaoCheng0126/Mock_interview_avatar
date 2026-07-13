import { sceneButton } from './shared.js';

export function renderGridExplorer(scene) {
  const props = scene.props || {};
  const gridSize = props.gridSize || 5;
  const start = props.start || [0, 0];
  const end = props.end || [4, 4];
  const obstacles = new Set((props.obstacles || []).map(p => p.join(',')));
  const mode = props.mode || 'free'; // 'free' | 'pathfind'

  const stage = document.createElement('div');
  stage.className = 'grid-stage';

  let pos = [start[0], start[1]];
  let path = [pos.join(',')];

  const gridEl = document.createElement('div');
  gridEl.className = 'explorer-grid';
  gridEl.style.gridTemplateColumns = 'repeat(' + gridSize + ', 1fr)';

  function drawGrid() {
    gridEl.innerHTML = '';
    for (let r = 0; r < gridSize; r++) {
      for (let c = 0; c < gridSize; c++) {
        const cell = document.createElement('div');
        cell.className = 'grid-cell';
        cell.dataset.pos = r + ',' + c;

        if (r === pos[0] && c === pos[1]) {
          cell.classList.add('player');
          cell.textContent = '🐭';
        } else if (r === end[0] && c === end[1]) {
          cell.classList.add('goal');
          cell.textContent = '🧀';
        } else if (obstacles.has(r + ',' + c)) {
          cell.classList.add('obstacle');
          cell.textContent = '🐱';
        } else if (mode === 'pathfind' && path.includes(r + ',' + c)) {
          cell.classList.add('path-trail');
        }
        gridEl.appendChild(cell);
      }
    }
  }

  drawGrid();

  const dpad = document.createElement('div');
  dpad.className = 'dpad';

  function move(dr, dc) {
    const nr = pos[0] + dr;
    const nc = pos[1] + dc;
    if (nr < 0 || nr >= gridSize || nc < 0 || nc >= gridSize) return;
    if (obstacles.has(nr + ',' + nc)) return;
    pos = [nr, nc];
    if (mode === 'pathfind') {
      if (!path.includes(pos.join(','))) {
        path.push(pos.join(','));
      }
    }
    drawGrid();
    if (pos[0] === end[0] && pos[1] === end[1]) {
      setTimeout(() => {
        const win = document.createElement('div');
        win.className = 'grid-win';
        win.textContent = mode === 'pathfind'
          ? '🎉 找到最短路径！用了 ' + (path.length - 1) + ' 步！'
          : '🎉 到达终点！你太棒了！';
        stage.appendChild(win);
      }, 300);
    }
  }

  const directions = [
    { label: '⬆️ 上', dr: -1, dc: 0 },
    { label: '⬅️ 左', dr: 0, dc: -1 },
    { label: '⬇️ 下', dr: 1, dc: 0 },
    { label: '➡️ 右', dr: 0, dc: 1 },
  ];

  directions.forEach(d => {
    const btn = sceneButton(d.label, () => move(d.dr, d.dc));
    dpad.appendChild(btn);
  });

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('重新开始', () => {
      pos = [start[0], start[1]];
      path = [pos.join(',')];
      const winEl = stage.querySelector('.grid-win');
      if (winEl) winEl.remove();
      drawGrid();
    })
  );

  if (mode === 'pathfind') {
    actions.append(
      sceneButton('最短路径', () => {
        // Simple BFS to show shortest path
        const queue = [[start[0], start[1], [start.join(',')]]];
        const visited = new Set([start.join(',')]);
        let found = null;
        while (queue.length > 0) {
          const [r, c, p] = queue.shift();
          if (r === end[0] && c === end[1]) { found = p; break; }
          for (const [dr, dc] of [[-1,0],[1,0],[0,-1],[0,1]]) {
            const nr = r + dr, nc = c + dc;
            const key = nr + ',' + nc;
            if (nr >= 0 && nr < gridSize && nc >= 0 && nc < gridSize &&
                !obstacles.has(key) && !visited.has(key)) {
              visited.add(key);
              queue.push([nr, nc, [...p, key]]);
            }
          }
        }
        if (found) {
          path = found;
          pos = [end[0], end[1]];
          drawGrid();
          const win = document.createElement('div');
          win.className = 'grid-win';
          win.textContent = '🔍 最短路径找到！共 ' + (path.length - 1) + ' 步！';
          stage.appendChild(win);
        }
      })
    );
  }

  stage.append(gridEl, dpad);
  return [stage, actions];
}
