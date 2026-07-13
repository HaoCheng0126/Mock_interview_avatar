import { sceneButton } from './shared.js';

export function renderPaperFoldExplorer(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'fold'; // 'fold' | 'cut_hole' | 'matchstick'
  const thickness = props.thickness || 0.1; // mm
  const maxFolds = props.maxFolds || 42;

  const stage = document.createElement('div');
  stage.className = 'fold-explorer-stage';

  let folds = props.folds || 0;
  let shape = props.shape || 'heart';

  if (mode === 'fold') {
    // Paper folding with thickness counter
    const paperArea = document.createElement('div');
    paperArea.className = 'paper-stack-area';

    const paper = document.createElement('div');
    paper.className = 'foldable-paper';

    function updatePaper() {
      const scale = Math.max(0.3, 1 - folds * 0.015);
      const currentThickness = thickness * Math.pow(2, folds);
      paper.style.transform = 'scale(' + scale + ')';
      paper.style.boxShadow = (folds * 0.5) + 'px ' + (folds * 0.5) + 'px 0 rgba(0,0,0,0.08)';

      // Update info
      const infoEl = stage.querySelector('.fold-info');
      if (infoEl) {
        if (currentThickness < 1) {
          infoEl.textContent = '对折 ' + folds + ' 次 | 厚度: ' + (currentThickness).toFixed(2) + ' 毫米';
        } else if (currentThickness < 1000) {
          infoEl.textContent = '对折 ' + folds + ' 次 | 厚度: ' + (currentThickness / 10).toFixed(1) + ' 厘米';
        } else if (currentThickness < 1000000) {
          infoEl.textContent = '对折 ' + folds + ' 次 | 厚度: ' + (currentThickness / 1000).toFixed(1) + ' 米';
        } else {
          infoEl.textContent = '对折 ' + folds + ' 次 | 厚度: ' + (currentThickness / 1000000).toFixed(0) + ' 公里 🚀';
        }
      }
    }

    paperArea.appendChild(paper);

    const info = document.createElement('div');
    info.className = 'fold-info';
    info.textContent = '对折 0 次 | 厚度: 0.10 毫米';

    const actions = document.createElement('div');
    actions.className = 'scene-actions';
    actions.append(
      sceneButton('对折一次 ✋', () => {
        if (folds < maxFolds) {
          folds++;
          updatePaper();
        }
      }),
      sceneButton('折到10次', () => {
        folds = Math.min(10, maxFolds);
        updatePaper();
      }),
      sceneButton('折到30次 🗻', () => {
        folds = Math.min(30, maxFolds);
        updatePaper();
      }),
      sceneButton('折到42次 🌙', () => {
        folds = Math.min(42, maxFolds);
        updatePaper();
      }),
      sceneButton('重新开始', () => {
        folds = 0;
        updatePaper();
      })
    );

    // Comparison landmarks
    const landmarks = document.createElement('div');
    landmarks.className = 'fold-landmarks';
    const marks = [
      { at: 10, label: '10次 ≈ 10厘米 (手掌厚)' },
      { at: 20, label: '20次 ≈ 100米 (30层楼)' },
      { at: 30, label: '30次 ≈ 107公里 (12座珠峰)' },
      { at: 42, label: '42次 ≈ 44万公里 (到达月球！)' },
    ];

    marks.forEach(m => {
      const mark = document.createElement('div');
      mark.className = 'fold-mark' + (folds >= m.at ? ' reached' : '');
      mark.textContent = m.label;
      landmarks.appendChild(mark);
    });

    stage.append(paperArea, info, landmarks);
    updatePaper();
    return [stage, actions];
  }

  if (mode === 'cut_hole') {
    // Paper cut hole — topology demonstration
    const holeArea = document.createElement('div');
    holeArea.className = 'hole-demo-area';

    const paper = document.createElement('div');
    paper.className = 'hole-paper';
    paper.textContent = '📄 A4纸';

    const holeInfo = document.createElement('div');
    holeInfo.className = 'hole-info';
    holeInfo.textContent = '沿着螺旋线剪开，洞就会变得超大！';

    let step = 0;

    function updateHole() {
      paper.classList.remove('hole-step-0', 'hole-step-1', 'hole-step-2', 'hole-step-3');
      paper.classList.add('hole-step-' + step);
      if (step === 0) {
        holeInfo.textContent = '📄 一张普通的A4纸，中间剪个洞…';
        paper.textContent = '📄 A4纸';
      } else if (step === 1) {
        holeInfo.textContent = '✂️ 从边缘剪一条螺旋线…';
        paper.textContent = '📄✂️ 螺旋剪开中…';
      } else if (step === 2) {
        holeInfo.textContent = '🔍 慢慢拉开看…洞在变大！';
        paper.textContent = '⭕ 洞变大了！';
      } else {
        holeInfo.textContent = '🎉 洞大到可以钻过一个人！这就是拓扑学的魔法！';
        paper.textContent = '⭕ 超级大洞！';
        paper.style.transform = 'scale(1.3)';
      }
    }

    holeArea.append(paper, holeInfo);

    const actions = document.createElement('div');
    actions.className = 'scene-actions';
    actions.append(
      sceneButton('剪螺旋线 ✂️', () => {
        step = Math.min(step + 1, 3);
        updateHole();
      }),
      sceneButton('重新演示', () => {
        step = 0;
        paper.style.transform = 'scale(1)';
        updateHole();
      })
    );

    stage.append(holeArea);
    updateHole();
    return [stage, actions];
  }

  if (mode === 'matchstick') {
    // Matchstick transformation — show 6 transforming
    const stickArea = document.createElement('div');
    stickArea.className = 'matchstick-area';

    const display = document.createElement('div');
    display.className = 'matchstick-display';

    // Segment display for digit 6
    // 7-segment: top, upper-left, upper-right, middle, lower-left, lower-right, bottom
    const segments = [
      { id: 'top', label: '─' },
      { id: 'ul', label: '│' },
      { id: 'ur', label: '│' },
      { id: 'mid', label: '─' },
      { id: 'll', label: '│' },
      { id: 'lr', label: '│' },
      { id: 'bot', label: '─' },
    ];

    // Digit patterns for 7-segment display
    const patterns = {
      '6': new Set(['top', 'ul', 'mid', 'll', 'lr', 'bot']),
      '0': new Set(['top', 'ul', 'ur', 'll', 'lr', 'bot']),
      '5': new Set(['top', 'ul', 'mid', 'lr', 'bot']),
      '9': new Set(['top', 'ul', 'ur', 'mid', 'lr', 'bot']),
      '8': new Set(['top', 'ul', 'ur', 'mid', 'll', 'lr', 'bot']),
    };

    let currentDigit = '6';
    let targetDigit = '0';

    function drawSegments() {
      display.innerHTML = '';
      display.className = 'matchstick-display';
      segments.forEach(seg => {
        const stick = document.createElement('div');
        stick.className = 'matchstick seg-' + seg.id;
        if (patterns[targetDigit] && !patterns[targetDigit].has(seg.id)) {
          stick.classList.add('removed');
        } else {
          stick.classList.add('active');
          stick.textContent = seg.label;
        }

        // Make the "extra" segment in 6 (upper-right) clickable
        if (currentDigit === '6' && seg.id === 'ur' && patterns['6'].has('ur') && !patterns['0'].has('ur')) {
          stick.classList.add('clickable-match');
          stick.title = '移动这根火柴，6 就变成 0！';
          stick.onclick = () => {
            currentDigit = '0';
            targetDigit = '0';
            drawSegments();
            const result = document.createElement('div');
            result.className = 'match-result';
            result.textContent = '🎉 太棒了！移动右上角火柴，6 变成了 0！';
            display.appendChild(result);
          };
        }
        display.appendChild(stick);
      });
    }

    drawSegments();

    const digitLabel = document.createElement('div');
    digitLabel.className = 'match-label';
    digitLabel.textContent = '当前: ' + currentDigit + ' → 目标: ' + targetDigit + ' (只能移动一根火柴)';

    const actions = document.createElement('div');
    actions.className = 'scene-actions';
    actions.append(
      sceneButton('试试变 0', () => { targetDigit = '0'; drawSegments(); digitLabel.textContent = '当前: ' + currentDigit + ' → 目标: ' + targetDigit; }),
      sceneButton('试试变 9', () => { targetDigit = '9'; drawSegments(); digitLabel.textContent = '当前: ' + currentDigit + ' → 目标: ' + targetDigit; }),
      sceneButton('试试变 8', () => { targetDigit = '8'; drawSegments(); digitLabel.textContent = '当前: ' + currentDigit + ' → 目标: ' + targetDigit; }),
      sceneButton('试试变 5', () => { targetDigit = '5'; drawSegments(); digitLabel.textContent = '当前: ' + currentDigit + ' → 目标: ' + targetDigit; })
    );

    stage.append(display, digitLabel);
    return [stage, actions];
  }

  // Default fallback
  return [stage];
}
