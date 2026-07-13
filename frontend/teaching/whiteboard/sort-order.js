import { sceneButton } from './shared.js';

export function renderSortOrder(scene) {
  const props = scene.props || {};
  const items = props.items || [];
  if (!items.length) return [document.createElement('div')];
  const sortBy = props.sortBy || 'value';
  const direction = props.direction || 'asc';
  const mode = props.mode || 'sort'; // 'sort' | 'oddone' | 'compare'

  const stage = document.createElement('div');
  stage.className = 'sort-stage';

  let currentItems = [...items];

  function draw() {
    stage.innerHTML = '';
    currentItems.forEach((item, idx) => {
      const card = document.createElement('div');
      card.className = 'sort-card';
      card.draggable = true;
      card.dataset.idx = idx;
      card.dataset.value = item.value;

      const label = document.createElement('div');
      label.className = 'sort-label';
      label.textContent = item.label;

      const bar = document.createElement('div');
      bar.className = 'sort-bar';
      const maxVal = Math.max(...items.map(i => i[sortBy] || 0), 1);
      const pct = ((item[sortBy] || 0) / maxVal) * 100;
      bar.style.width = pct + '%';
      bar.style.height = '24px';
      if (item.color) bar.style.backgroundColor = item.color;

      if (mode === 'oddone' && props.oddOneIdx === idx) {
        card.classList.add('odd-one');
      }

      card.append(label, bar);

      card.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', String(idx));
        card.classList.add('dragging');
      });
      card.addEventListener('dragend', () => card.classList.remove('dragging'));
      card.addEventListener('dragover', e => e.preventDefault());
      card.addEventListener('drop', e => {
        e.preventDefault();
        const from = parseInt(e.dataTransfer.getData('text/plain'));
        const to = idx;
        if (from !== to) {
          const [moved] = currentItems.splice(from, 1);
          currentItems.splice(to, 0, moved);
          draw();
        }
      });

      stage.appendChild(card);
    });
  }

  draw();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  if (mode === 'sort') {
    actions.append(
      sceneButton('从低到高', () => {
        currentItems.sort((a, b) => (a[sortBy] || 0) - (b[sortBy] || 0));
        draw();
      }),
      sceneButton('从高到低', () => {
        currentItems.sort((a, b) => (b[sortBy] || 0) - (a[sortBy] || 0));
        draw();
      }),
      sceneButton('打乱重来', () => {
        for (let i = currentItems.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [currentItems[i], currentItems[j]] = [currentItems[j], currentItems[i]];
        }
        draw();
      })
    );
  } else if (mode === 'oddone') {
    actions.append(
      sceneButton('点选不同类', () => {
        stage.querySelectorAll('.sort-card').forEach(card => {
          card.style.cursor = 'pointer';
          card.onclick = () => {
            const idx = parseInt(card.dataset.idx);
            stage.querySelectorAll('.sort-card').forEach(c => c.classList.remove('picked'));
            card.classList.add('picked');
            if (idx === props.oddOneIdx) {
              card.classList.add('correct');
            } else {
              card.classList.add('wrong-pick');
              setTimeout(() => card.classList.remove('wrong-pick', 'picked'), 800);
            }
          };
        });
      })
    );
  } else if (mode === 'compare') {
    actions.append(
      sceneButton('比一比', () => {
        const maxItem = currentItems.reduce((a, b) =>
          (a[sortBy] || 0) >= (b[sortBy] || 0) ? a : b
        );
        stage.querySelectorAll('.sort-card').forEach(card => {
          card.classList.remove('highlight');
          if (parseInt(card.dataset.value) === (maxItem[sortBy] || 0)) {
            card.classList.add('highlight');
          }
        });
      })
    );
  }

  return [stage, actions];
}
