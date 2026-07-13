import { sceneButton } from './shared.js';

export function renderBarChart(scene) {
  const props = scene.props || {};
  const bars = props.bars || [
    { label: '🥕 胡萝卜', value: 5 },
    { label: '🥜 坚果', value: 7 },
    { label: '🐟 小鱼干', value: 3 },
  ];
  const highlight = props.highlight || ''; // 'max' | 'min'

  const stage = document.createElement('div');
  stage.className = 'barchart-stage';

  const chart = document.createElement('div');
  chart.className = 'bar-chart';

  const maxVal = Math.max(...bars.map(b => b.value), 1);

  function draw() {
    chart.innerHTML = '';
    bars.forEach((bar, idx) => {
      const col = document.createElement('div');
      col.className = 'bar-col';

      const barFill = document.createElement('div');
      barFill.className = 'bar-fill';
      const pct = (bar.value / maxVal) * 100;
      barFill.style.height = (pct * 1.8) + 'px';
      barFill.style.backgroundColor = bar.color || getColor(idx);

      if (highlight === 'max' && bar.value === maxVal) {
        barFill.classList.add('highlight-bar');
      }

      const label = document.createElement('div');
      label.className = 'bar-label';
      label.textContent = bar.label;

      const val = document.createElement('div');
      val.className = 'bar-value';
      val.textContent = bar.value + '票';

      col.append(barFill, val, label);
      chart.appendChild(col);
    });
  }

  function getColor(idx) {
    const colors = ['#d95b59', '#f5c84b', '#4f8ed8', '#58a875', '#b07cd8', '#e8915c'];
    return colors[idx % colors.length];
  }

  draw();

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('数一数格子', () => {
      chart.querySelectorAll('.bar-value').forEach(el => {
        el.style.display = el.style.display === 'none' ? '' : 'none';
      });
    }),
    sceneButton('找最高', () => {
      chart.querySelectorAll('.bar-fill').forEach(f => f.classList.remove('highlight-bar'));
      const maxBar = chart.querySelectorAll('.bar-col')[bars.findIndex(b => b.value === maxVal)];
      if (maxBar) maxBar.querySelector('.bar-fill').classList.add('highlight-bar');
    })
  );

  stage.append(chart);
  return [stage, actions];
}
