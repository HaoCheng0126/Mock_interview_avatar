import { sceneButton } from './shared.js';

export function renderPatternSequence(scene) {
  const props = scene.props || {};
  const sequence = props.sequence || [];
  const choices = props.choices || [];
  const answer = props.answer !== undefined ? props.answer : 0;
  const mode = props.mode || 'complete'; // 'complete' | 'nth'
  if (!sequence.length) return [document.createElement('div')];

  const stage = document.createElement('div');
  stage.className = 'pattern-stage';

  const seqRow = document.createElement('div');
  seqRow.className = 'sequence-row';

  let answered = false;
  let nthTarget = props.nthTarget || 0;
  let cycleLength = props.cycleLength || 3;

  function drawSequence() {
    seqRow.innerHTML = '';
    sequence.forEach((item, idx) => {
      const cell = document.createElement('div');
      cell.className = 'seq-cell' + (item === '?' ? ' question' : '');
      cell.textContent = item;
      if (answered && item === '?' && mode === 'complete') {
        cell.textContent = choices[answer];
        cell.classList.add('revealed');
      }
      seqRow.appendChild(cell);
    });
  }

  drawSequence();

  const choiceRow = document.createElement('div');
  choiceRow.className = 'choice-row';

  choices.forEach((choice, idx) => {
    const btn = document.createElement('button');
    btn.className = 'seq-choice';
    btn.type = 'button';
    btn.textContent = choice;
    btn.onclick = () => {
      if (answered) return;
      choiceRow.querySelectorAll('.seq-choice').forEach(b => b.classList.remove('picked', 'correct', 'wrong'));
      if (idx === answer) {
        btn.classList.add('picked', 'correct');
        answered = true;
        drawSequence();
      } else {
        btn.classList.add('picked', 'wrong');
        setTimeout(() => {
          btn.classList.remove('picked', 'wrong');
        }, 800);
      }
    };
    choiceRow.appendChild(btn);
  });

  const nthSection = document.createElement('div');
  nthSection.className = 'nth-section';
  nthSection.style.display = mode === 'nth' ? 'block' : 'none';

  if (mode === 'nth') {
    const nthLabel = document.createElement('div');
    nthLabel.className = 'nth-label';
    nthLabel.textContent = '第几个是什么？用除法算一算！';
    nthSection.appendChild(nthLabel);

    const nthInput = document.createElement('div');
    nthInput.className = 'nth-input-row';

    const input = document.createElement('input');
    input.type = 'number';
    input.min = '1';
    input.max = '100';
    input.value = nthTarget || '10';
    input.className = 'nth-input';
    nthInput.appendChild(input);

    const calcBtn = sceneButton('计算', () => {
      const n = parseInt(input.value) || 10;
      nthTarget = n;
      const remainder = ((n - 1) % cycleLength);
      const answerIdx = remainder;
      nthLabel.textContent = '第' + n + '个：' + n + '÷' + cycleLength + '=' +
        Math.floor(n / cycleLength) + '组 余' + (remainder + 1) +
        ' → 第' + (remainder + 1) + '个：' + choices[answerIdx];
    });
    nthInput.appendChild(calcBtn);
    nthSection.appendChild(nthInput);
  }

  const actions = document.createElement('div');
  actions.className = 'scene-actions';
  actions.append(
    sceneButton('重新开始', () => {
      answered = false;
      choiceRow.querySelectorAll('.seq-choice').forEach(b => b.classList.remove('picked', 'correct', 'wrong'));
      drawSequence();
    })
  );

  stage.append(seqRow, choiceRow, nthSection);
  return [stage, actions];
}
